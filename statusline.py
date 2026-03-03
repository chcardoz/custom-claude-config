# /// script
# requires-python = ">=3.10"
# ///
"""Redline — Rich statusline for Claude Code."""

import json
import os
import platform
import ssl
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
STRETCH_STATE_PATH = CACHE_DIR / "stretch_state.json"
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
            "spotify": True,
            "focus": True,
            "system": True,
            "stretch": True,
        },
        "bar_size": 10,
        "cache_ttl_seconds": 60,
        "theme": {"low_threshold": 50, "high_threshold": 80},
        "stretch_interval_minutes": 15,
        "stretch_sound": "Glass",
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


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context, trying certifi then system certs."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # macOS: try the Homebrew / system cert bundle locations
    for ca in (
        "/etc/ssl/cert.pem",
        "/opt/homebrew/etc/openssl/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
    ):
        if os.path.isfile(ca):
            return ssl.create_default_context(cafile=ca)
    return ssl.create_default_context()


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
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ctx) as resp:
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


def progress_bar(pct: float, size: int, theme: dict) -> str:
    """Build a colored block-style progress bar."""
    filled = round(pct / 100 * size)
    filled = max(0, min(size, filled))
    color = color_for_pct(pct, theme)
    return f"{color}{'█' * filled}{'░' * (size - filled)}{RESET}"


def color_for_pct(pct: float, theme: dict) -> str:
    """Return ANSI color code based on usage percentage."""
    if pct >= theme["high_threshold"]:
        return RED
    if pct >= theme["low_threshold"]:
        return YELLOW
    return GREEN


def get_spotify_now_playing() -> dict | None:
    """Get current Spotify track info. Returns None if Spotify not running."""
    try:
        # Check if Spotify is running
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to (name of processes) contains "Spotify"'],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0 or "true" not in result.stdout.lower():
            return None

        # Get track info
        track_result = subprocess.run(
            ["osascript", "-e",
             'tell application "Spotify" to return (name of current track) & " - " & (artist of current track)'],
            capture_output=True, text=True, timeout=2,
        )
        # Get player state
        state_result = subprocess.run(
            ["osascript", "-e",
             'tell application "Spotify" to player state as string'],
            capture_output=True, text=True, timeout=2,
        )
        if track_result.returncode != 0:
            return None

        track_info = track_result.stdout.strip()
        state = state_result.stdout.strip().lower() if state_result.returncode == 0 else "unknown"
        return {"track": track_info, "state": state}
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_focus_status() -> dict | None:
    """Detect macOS Focus/DND status. Returns dict with 'active' bool."""
    if platform.system() != "Darwin":
        return None
    try:
        # Check assertion store for active focus modes
        assertions_path = Path.home() / "Library" / "DoNotDisturb" / "DB" / "Assertions.json"
        result = subprocess.run(
            ["plutil", "-p", str(assertions_path)],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            output = result.stdout
            # If there are active assertions with storeAssertionRecords entries, focus is on
            if "storeAssertionRecords" in output:
                # Check if there are actual entries (non-empty dict)
                import re
                records_match = re.search(r"storeAssertionRecords.*?=>.*?\{(.*?)\}", output, re.DOTALL)
                if records_match and records_match.group(1).strip():
                    return {"active": True}
            return {"active": False}
    except (subprocess.TimeoutExpired, OSError):
        pass

    try:
        # Fallback: check controlcenter defaults
        result = subprocess.run(
            ["defaults", "read", "com.apple.controlcenter", "NSStatusItem Visible FocusModes"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return {"active": result.stdout.strip() == "1"}
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


def get_system_info() -> dict | None:
    """Get CPU and memory usage."""
    try:
        cpu_pct = None
        mem_pct = None

        # CPU: sum all process CPU usage, normalize by core count
        cpu_result = subprocess.run(
            ["ps", "-A", "-o", "%cpu"],
            capture_output=True, text=True, timeout=3,
        )
        if cpu_result.returncode == 0:
            lines = cpu_result.stdout.strip().split("\n")[1:]  # skip header
            total_cpu = sum(float(l.strip()) for l in lines if l.strip())
            # Get core count
            ncpu_result = subprocess.run(
                ["sysctl", "-n", "hw.ncpu"],
                capture_output=True, text=True, timeout=2,
            )
            ncpu = int(ncpu_result.stdout.strip()) if ncpu_result.returncode == 0 else 1
            cpu_pct = min(100.0, total_cpu / ncpu)

        # Memory: parse vm_stat
        mem_result = subprocess.run(
            ["vm_stat"],
            capture_output=True, text=True, timeout=3,
        )
        if mem_result.returncode == 0:
            import re
            pages = {}
            for line in mem_result.stdout.split("\n"):
                m = re.match(r"(.+?):\s+(\d+)", line)
                if m:
                    pages[m.group(1).strip().lower()] = int(m.group(2))
            page_size = 16384  # default
            ps_match = re.search(r"page size of (\d+) bytes", mem_result.stdout)
            if ps_match:
                page_size = int(ps_match.group(1))

            free = pages.get("pages free", 0)
            active = pages.get("pages active", 0)
            inactive = pages.get("pages inactive", 0)
            speculative = pages.get("pages speculative", 0)
            wired = pages.get("pages wired down", 0)
            # Total = all known page categories
            total = free + active + inactive + speculative + wired
            if total > 0:
                used = active + wired
                mem_pct = (used / total) * 100

        if cpu_pct is not None or mem_pct is not None:
            return {"cpu": cpu_pct, "mem": mem_pct}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def _read_stretch_state() -> dict:
    """Read stretch state from cache file."""
    try:
        with open(STRETCH_STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_stretch_state(state: dict) -> None:
    """Write stretch state to cache file."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STRETCH_STATE_PATH, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def _check_stretch_reminder(interval_minutes: int, sound: str) -> str | None:
    """Check if a stretch reminder is due and return elapsed session time string."""
    now = time.time()
    state = _read_stretch_state()

    # Start a new session if no prior state or last render is stale (>5 min gap)
    if not state.get("session_start") or now - state.get("last_render", 0) > 300:
        state = {"session_start": now, "last_render": now, "last_notification": now}
        _write_stretch_state(state)
        return "0m"

    state["last_render"] = now

    # Check if notification is due
    interval_seconds = interval_minutes * 60
    if now - state.get("last_notification", 0) >= interval_seconds:
        elapsed_minutes = int((now - state["session_start"]) / 60)
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "You have been working for {elapsed_minutes} minutes. '
                 f'Time to stand up and stretch!" '
                 f'with title "Stretch Reminder" sound name "{sound}"'],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        state["last_notification"] = now

    _write_stretch_state(state)

    # Format elapsed time
    elapsed_seconds = int(now - state["session_start"])
    minutes = elapsed_seconds // 60
    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h{mins:02d}m"
    return f"{minutes}m"


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


def strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences for visible-length calculation."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


BRAILLE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_STATE_PATH = CACHE_DIR / "spinner.state"


def _spinner_char() -> str:
    """Return the next braille spinner frame, persisted across renders."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        idx = 0
        try:
            idx = int(SPINNER_STATE_PATH.read_text().strip())
        except (OSError, ValueError):
            pass
        char = BRAILLE_FRAMES[idx % len(BRAILLE_FRAMES)]
        SPINNER_STATE_PATH.write_text(str((idx + 1) % len(BRAILLE_FRAMES)))
        return f"{CYAN}{char}{RESET}"
    except OSError:
        return BRAILLE_FRAMES[0]


def box_frame(lines: list, title: str = "", min_width: int = 0, footer_right: str = "", title_right: str = "") -> str:
    """Wrap lines in a Unicode box-drawing frame with an optional title.

    footer_right places a string right-aligned on its own line before the bottom border.
    title_right places a string right-aligned in the top border.
    """
    content_widths = [len(strip_ansi(l)) for l in lines]
    width = max(min_width, max(content_widths)) if content_widths else min_width
    # Top border with title (and optional right title)
    if title:
        title_segment = f" {title} "
        if title_right:
            right_segment = f" {title_right} "
            dashes = width + 2 - len(title_segment) - len(right_segment) - 2
            top = f"╭─{title_segment}{'─' * dashes}{right_segment}─╮"
        else:
            top = f"╭─{title_segment}{'─' * (width - len(title_segment) + 1)}╮"
    else:
        top = f"╭{'─' * (width + 2)}╮"
    # Bottom border
    bottom = f"╰{'─' * (width + 2)}╯"
    # Content rows, padded to width
    framed = [top]
    for line in lines:
        pad = width - len(strip_ansi(line))
        framed.append(f"│ {line}{' ' * pad} │")
    if footer_right:
        vis_len = len(strip_ansi(footer_right))
        pad = width - vis_len
        framed.append(f"│ {' ' * pad}{footer_right} │")
    framed.append(bottom)
    return "\n".join(framed)


def main() -> None:
    config = load_config()
    show = config["show"]
    bar_size = config["bar_size"]
    theme = config["theme"]

    stdin_data = read_stdin()
    model_info = stdin_data.get("model", {})
    ctx = stdin_data.get("context_window", {})

    model_name = model_info.get("display_name", "Unknown")
    content_lines: list[str] = []

    # ── Build bar segments for a single line ──────────────────────────────
    bar_parts: list[str] = []

    if show.get("context", True):
        used_pct = ctx.get("used_percentage")
        if used_pct is not None:
            bar = progress_bar(used_pct, bar_size, theme)
            bar_parts.append(f"ctx {bar} {int(used_pct)}%")
        else:
            bar_parts.append(f"ctx {DIM}{'░' * bar_size} ——%{RESET}")

    usage = None
    if show.get("session", True) or show.get("weekly", True):
        token = get_oauth_token()
        if token:
            usage = read_cache(config["cache_ttl_seconds"])
            if usage is None:
                usage = fetch_usage(token)
                if usage:
                    write_cache(usage)

    reset_parts: list[str] = []

    if usage:
        five_hour = usage.get("five_hour", {})
        seven_day = usage.get("seven_day", {})

        if show.get("session", True):
            session_pct = five_hour.get("utilization")
            if session_pct is not None:
                bar = progress_bar(session_pct, bar_size, theme)
                bar_parts.append(f"sess {bar} {int(session_pct)}%")
            else:
                bar_parts.append(f"sess {DIM}{'░' * bar_size} ——%{RESET}")
            if show.get("reset_times", True):
                resets_at = five_hour.get("resets_at", "")
                if resets_at:
                    reset_parts.append(f"↻ sess {format_reset_time(resets_at)}")

        if show.get("weekly", True):
            weekly_pct = seven_day.get("utilization")
            if weekly_pct is not None:
                bar = progress_bar(weekly_pct, bar_size, theme)
                bar_parts.append(f"week {bar} {int(weekly_pct)}%")
            else:
                bar_parts.append(f"week {DIM}{'░' * bar_size} ——%{RESET}")
            if show.get("reset_times", True):
                resets_at = seven_day.get("resets_at", "")
                if resets_at:
                    reset_parts.append(f"week {format_reset_time(resets_at)}")
    else:
        # No usage data — show skeleton bars
        if show.get("session", True):
            bar_parts.append(f"sess {DIM}{'░' * bar_size} ——%{RESET}")
        if show.get("weekly", True):
            bar_parts.append(f"week {DIM}{'░' * bar_size} ——%{RESET}")

    if bar_parts:
        content_lines.append("  ".join(bar_parts))

    if reset_parts:
        content_lines.append(f"{DIM}{'  · '.join(reset_parts)}{RESET}")

    # ── Spotify + Focus + System (single line) ────────────────────────
    info_parts: list[str] = []

    if show.get("spotify", True):
        spotify = get_spotify_now_playing()
        if spotify:
            icon = "▶" if spotify["state"] == "playing" else "⏸"
            track = spotify["track"]
            max_len = 40
            if len(track) > max_len:
                track = track[:max_len - 1] + "…"
            info_parts.append(f"♫ {icon} {track}")
        else:
            info_parts.append(f"{DIM}♫ Not playing{RESET}")

    if show.get("focus", True):
        focus = get_focus_status()
        if focus and focus.get("active"):
            info_parts.append(f"{YELLOW}Focus On{RESET}")
        else:
            info_parts.append(f"{DIM}Focus Off{RESET}")

    if show.get("system", True):
        sysinfo = get_system_info()
        if sysinfo:
            if sysinfo.get("cpu") is not None:
                cpu = sysinfo["cpu"]
                cpu_color = color_for_pct(cpu, theme)
                info_parts.append(f"CPU {cpu_color}{int(cpu)}%{RESET}")
            else:
                info_parts.append(f"CPU {DIM}——%{RESET}")
            if sysinfo.get("mem") is not None:
                mem = sysinfo["mem"]
                mem_color = color_for_pct(mem, theme)
                info_parts.append(f"MEM {mem_color}{int(mem)}%{RESET}")
            else:
                info_parts.append(f"MEM {DIM}——%{RESET}")
        else:
            info_parts.append(f"CPU {DIM}——%{RESET}")
            info_parts.append(f"MEM {DIM}——%{RESET}")

    if info_parts:
        content_lines.append(f"{'  ·  '.join(info_parts)}")

    # ── Stretch reminder ────────────────────────────────────────────
    title_right = ""
    if show.get("stretch", True):
        interval = config.get("stretch_interval_minutes", 15)
        sound = config.get("stretch_sound", "Glass")
        elapsed = _check_stretch_reminder(interval, sound)
        if elapsed:
            title_right = f"⏱ {elapsed}"

    # ── Output in box frame ───────────────────────────────────────────
    spinner = _spinner_char()
    if content_lines:
        print(box_frame(content_lines, title=model_name, footer_right=spinner, title_right=title_right))
    else:
        print(model_name)


if __name__ == "__main__":
    main()
