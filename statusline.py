# /// script
# requires-python = ">=3.10"
# ///
"""Redline — Rich statusline for Claude Code."""

import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).parent))
CONFIG_PATH = PLUGIN_ROOT / "config.json"
CACHE_DIR = Path.home() / ".cache" / "redline"
CACHE_PATH = CACHE_DIR / "cache.json"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
HTTP_TIMEOUT = 5

# ─── ANSI Colors ─────────────────────────────────────────────────────────────

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"


def load_config() -> dict:
    """Load config.json with defaults."""
    defaults = {
        "show": {
            "model": True,
            "context": True,
            "session": True,
            "weekly": True,
            "reset_times": True,
        },
        "bar_size": 10,
        "cache_ttl_seconds": 60,
        "theme": {"low_threshold": 50, "high_threshold": 80},
    }
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        # Merge top-level and nested dicts
        for key in defaults:
            if key not in cfg:
                cfg[key] = defaults[key]
            elif isinstance(defaults[key], dict):
                for k, v in defaults[key].items():
                    cfg[key].setdefault(k, v)
        return cfg
    except (OSError, json.JSONDecodeError):
        return defaults


def read_stdin() -> dict:
    """Read and parse JSON from stdin."""
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}


# ─── Credential retrieval ────────────────────────────────────────────────────


def get_token_from_credentials_file() -> str | None:
    """Read OAuth token from ~/.claude/.credentials.json."""
    try:
        with open(CREDENTIALS_PATH) as f:
            data = json.load(f)
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (OSError, json.JSONDecodeError):
        return None


def get_token_from_keychain() -> str | None:
    """Read OAuth token from macOS Keychain."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        data = json.loads(raw)
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def get_oauth_token() -> str | None:
    """Get OAuth token from available sources, in priority order."""
    return (
        get_token_from_credentials_file()
        or get_token_from_keychain()
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    )


# ─── Cache ───────────────────────────────────────────────────────────────────


def read_cache(ttl: int) -> dict | None:
    """Read cached usage data if still fresh."""
    try:
        with open(CACHE_PATH) as f:
            cached = json.load(f)
        if time.time() - cached.get("timestamp", 0) < ttl:
            return cached.get("usage_data")
    except (OSError, json.JSONDecodeError):
        pass
    return None


def write_cache(usage_data: dict) -> None:
    """Write usage data to cache."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump({"timestamp": time.time(), "usage_data": usage_data}, f)
    except OSError:
        pass


# ─── API ─────────────────────────────────────────────────────────────────────


def fetch_usage(token: str) -> dict | None:
    """Call Anthropic usage API."""
    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


# ─── Formatting helpers ──────────────────────────────────────────────────────


def format_tokens(n: int | float) -> str:
    """Format token count as human-readable string."""
    n = int(n)
    if n >= 1_000_000:
        val = n / 1_000_000
        return f"{val:.1f}m" if val != int(val) else f"{int(val)}m"
    if n >= 1_000:
        val = n / 1_000
        return f"{val:.1f}k" if val != int(val) else f"{int(val)}k"
    return str(n)


def progress_bar(pct: float, size: int) -> str:
    """Build a progress bar string with ● and ○."""
    filled = round(pct / 100 * size)
    filled = max(0, min(size, filled))
    return "●" * filled + "○" * (size - filled)


def color_for_pct(pct: float, theme: dict) -> str:
    """Return ANSI color code based on usage percentage."""
    if pct >= theme["high_threshold"]:
        return RED
    if pct >= theme["low_threshold"]:
        return YELLOW
    return GREEN


def format_reset_time(iso_str: str) -> str:
    """Format an ISO 8601 timestamp as a friendly local time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        now = datetime.now(timezone.utc).astimezone()
        time_str = local_dt.strftime("%-I:%M%p").lower()
        # If it's today, just show time
        if local_dt.date() == now.date():
            return time_str
        # Otherwise include date
        date_str = local_dt.strftime("%b %-d").lower()
        return f"{date_str}, {time_str}"
    except (ValueError, OSError):
        return iso_str


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    config = load_config()
    show = config["show"]
    bar_size = config["bar_size"]
    theme = config["theme"]

    stdin_data = read_stdin()
    model_info = stdin_data.get("model", {})
    ctx = stdin_data.get("context_window", {})

    # ── Line 1: Model | Tokens | Usage ────────────────────────────────────
    parts_line1 = []

    model_name = model_info.get("display_name", "Unknown")
    if show.get("model", True):
        parts_line1.append(f"{CYAN}{model_name}{RESET}")

    if show.get("context", True):
        total_tokens = ctx.get("total_input_tokens", 0) + ctx.get("total_output_tokens", 0)
        window_size = ctx.get("context_window_size", 0)
        used_pct = ctx.get("used_percentage", 0)
        remain_pct = ctx.get("remaining_percentage", 100)
        remaining_tokens = window_size - total_tokens if window_size else 0

        parts_line1.append(f"{format_tokens(total_tokens)} / {format_tokens(window_size)}")
        parts_line1.append(
            f"{int(used_pct)}% used {format_tokens(total_tokens)}"
        )
        parts_line1.append(
            f"{int(remain_pct)}% remain {format_tokens(remaining_tokens)}"
        )

    line1 = " | ".join(parts_line1)

    # ── Lines 2-3: Usage bars + reset times ───────────────────────────────
    line2 = ""
    line3 = ""

    usage = None
    if show.get("session", True) or show.get("weekly", True):
        token = get_oauth_token()
        if token:
            usage = read_cache(config["cache_ttl_seconds"])
            if usage is None:
                usage = fetch_usage(token)
                if usage:
                    write_cache(usage)

    if usage:
        five_hour = usage.get("five_hour", {})
        seven_day = usage.get("seven_day", {})

        session_pct = (five_hour.get("utilization", 0)) * 100
        weekly_pct = (seven_day.get("utilization", 0)) * 100

        parts_line2 = []
        parts_line3 = []

        if show.get("session", True):
            s_color = color_for_pct(session_pct, theme)
            s_bar = progress_bar(session_pct, bar_size)
            parts_line2.append(f"current: {s_color}{s_bar}{RESET} {int(session_pct)}%")

            if show.get("reset_times", True):
                resets_at = five_hour.get("resets_at", "")
                parts_line3.append(f"resets {format_reset_time(resets_at)}" if resets_at else "")

        if show.get("weekly", True):
            w_color = color_for_pct(weekly_pct, theme)
            w_bar = progress_bar(weekly_pct, bar_size)
            parts_line2.append(f"weekly: {w_color}{w_bar}{RESET} {int(weekly_pct)}%")

            if show.get("reset_times", True):
                resets_at = seven_day.get("resets_at", "")
                parts_line3.append(f"resets {format_reset_time(resets_at)}" if resets_at else "")

        line2 = "  |  ".join(parts_line2)
        line3 = f"{DIM}{'  |  '.join(p for p in parts_line3 if p)}{RESET}" if any(parts_line3) else ""

    # ── Output ────────────────────────────────────────────────────────────
    lines = [line1]
    if line2:
        lines.append(line2)
    if line3:
        lines.append(line3)

    print("\n".join(lines))


if __name__ == "__main__":
    main()
