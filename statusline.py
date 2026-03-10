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
import tempfile
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).parent))
CONFIG_PATH = PLUGIN_ROOT / "config.json"
CACHE_DIR = Path.home() / ".cache" / "redline"
CACHE_PATH = CACHE_DIR / "cache.json"
STRETCH_STATE_PATH = CACHE_DIR / "stretch_state.json"
OUTPUT_CACHE_PATH = CACHE_DIR / "output_cache.json"
OUTPUT_CACHE_TTL = 3  # seconds — reuse rendered output if fresh
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
            "contributions": True,
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

SUBPROCESS_CACHE_PATH = CACHE_DIR / "subprocess_cache.json"
_subprocess_cache: dict | None = None  # loaded once per render
_cache_lock = threading.Lock()
_MISSING = object()  # sentinel to distinguish "cached None" from "no cache entry"


def _load_subprocess_cache() -> dict:
    """Load the subprocess cache file once per render."""
    global _subprocess_cache
    if _subprocess_cache is not None:
        return _subprocess_cache
    try:
        with open(SUBPROCESS_CACHE_PATH) as f:
            _subprocess_cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        _subprocess_cache = {}
    return _subprocess_cache


def _cached_result(key: str, ttl: int):
    """Read a cached subprocess result if still fresh. Returns _MISSING on cache miss."""
    cache = _load_subprocess_cache()
    entry = cache.get(key)
    if entry and time.time() - entry.get("timestamp", 0) < ttl:
        return entry.get("data")
    return _MISSING


def _write_cached_result(key: str, data) -> None:
    """Write a subprocess result to the shared cache (thread-safe, atomic)."""
    try:
        with _cache_lock:
            cache = _load_subprocess_cache()
            cache[key] = {"timestamp": time.time(), "data": data}
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(cache, f)
                os.replace(tmp, SUBPROCESS_CACHE_PATH)
            except BaseException:
                os.unlink(tmp)
                raise
    except OSError:
        pass


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


# ─── Output cache ────────────────────────────────────────────────────────────


def _read_output_cache(stdin_data: dict) -> str | None:
    """Return cached rendered output if still fresh and stdin hasn't changed."""
    try:
        with open(OUTPUT_CACHE_PATH) as f:
            cached = json.load(f)
        if time.time() - cached.get("timestamp", 0) >= OUTPUT_CACHE_TTL:
            return None
        if cached.get("stdin_hash") != _hash_stdin(stdin_data):
            return None
        return cached.get("output")
    except (OSError, json.JSONDecodeError):
        return None


def _write_output_cache(stdin_data: dict, output: str) -> None:
    """Cache the rendered output string."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({
                    "timestamp": time.time(),
                    "stdin_hash": _hash_stdin(stdin_data),
                    "output": output,
                }, f)
            os.replace(tmp, OUTPUT_CACHE_PATH)
        except BaseException:
            os.unlink(tmp)
            raise
    except OSError:
        pass


def _hash_stdin(stdin_data: dict) -> str:
    """Quick deterministic hash of stdin data for cache invalidation."""
    return json.dumps(stdin_data, sort_keys=True, separators=(",", ":"))


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


def _fetch_spotify_now_playing() -> dict | None:
    """Get current Spotify track info (uncached). Returns None if Spotify not running.

    Uses a single osascript call to check running state, track, and player state.
    """
    try:
        script = (
            'tell application "System Events"\n'
            '  if (name of processes) contains "Spotify" then\n'
            '    tell application "Spotify"\n'
            '      set t to (name of current track) & " - " & (artist of current track)\n'
            '      set s to player state as string\n'
            '      return t & "\\n" & s\n'
            '    end tell\n'
            '  else\n'
            '    return "NOT_RUNNING"\n'
            '  end if\n'
            'end tell'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        if output == "NOT_RUNNING":
            return None
        parts = output.split("\n", 1)
        if len(parts) < 2:
            return None
        return {"track": parts[0].strip(), "state": parts[1].strip().lower()}
    except (subprocess.TimeoutExpired, OSError):
        return None


def get_spotify_now_playing() -> dict | None:
    """Get current Spotify track info with 5s cache."""
    cached = _cached_result("spotify", 5)
    if cached is not _MISSING:
        return cached
    result = _fetch_spotify_now_playing()
    _write_cached_result("spotify", result)
    return result


def _fetch_focus_status() -> dict | None:
    """Detect macOS Focus/DND status (uncached)."""
    if platform.system() != "Darwin":
        return None
    try:
        assertions_path = Path.home() / "Library" / "DoNotDisturb" / "DB" / "Assertions.json"
        result = subprocess.run(
            ["plutil", "-p", str(assertions_path)],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            output = result.stdout
            if "storeAssertionRecords" in output:
                import re
                records_match = re.search(r"storeAssertionRecords.*?=>.*?\{(.*?)\}", output, re.DOTALL)
                if records_match and records_match.group(1).strip():
                    return {"active": True}
            return {"active": False}
    except (subprocess.TimeoutExpired, OSError):
        pass

    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.controlcenter", "NSStatusItem Visible FocusModes"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return {"active": result.stdout.strip() == "1"}
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


def get_focus_status() -> dict | None:
    """Detect macOS Focus/DND status with 10s cache."""
    cached = _cached_result("focus", 10)
    if cached is not _MISSING:
        return cached
    result = _fetch_focus_status()
    _write_cached_result("focus", result)
    return result


def _fetch_system_info() -> dict | None:
    """Get CPU and memory usage (uncached).

    Uses 'top -l 1 -n 0 -s 0' for a single-sample summary instead of
    'ps -A -o %cpu' which lists every process (heavy stdout, contributes
    to ArrayBuffer accumulation in the Node.js host process).
    """
    try:
        cpu_pct = None
        mem_pct = None

        import re

        # Single 'top' invocation gives both CPU and memory in a compact summary
        top_result = subprocess.run(
            ["top", "-l", "1", "-n", "0", "-s", "0"],
            capture_output=True, text=True, timeout=3,
        )
        if top_result.returncode == 0:
            for line in top_result.stdout.split("\n"):
                # CPU usage: "CPU usage: 5.26% user, 3.94% sys, 90.79% idle"
                if line.startswith("CPU usage:"):
                    idle_m = re.search(r"([\d.]+)%\s*idle", line)
                    if idle_m:
                        cpu_pct = min(100.0, 100.0 - float(idle_m.group(1)))
                # PhysMem: "PhysMem: 16G used (2345M wired, ...M compressor), 567M unused."
                if line.startswith("PhysMem:"):
                    used_m = re.search(r"([\d.]+)([MG])\s*used", line)
                    unused_m = re.search(r"([\d.]+)([MG])\s*unused", line)
                    if used_m and unused_m:
                        def _to_mb(val: str, unit: str) -> float:
                            v = float(val)
                            return v * 1024 if unit == "G" else v
                        used_mb = _to_mb(used_m.group(1), used_m.group(2))
                        unused_mb = _to_mb(unused_m.group(1), unused_m.group(2))
                        total_mb = used_mb + unused_mb
                        if total_mb > 0:
                            mem_pct = (used_mb / total_mb) * 100

        if cpu_pct is not None or mem_pct is not None:
            return {"cpu": cpu_pct, "mem": mem_pct}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def get_system_info() -> dict | None:
    """Get CPU and memory usage with 5s cache."""
    cached = _cached_result("system", 5)
    if cached is not _MISSING:
        return cached
    result = _fetch_system_info()
    _write_cached_result("system", result)
    return result


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


# ─── GitHub Contributions ────────────────────────────────────────────────────

CONTRIBUTIONS_CACHE_PATH = CACHE_DIR / "contributions.json"
CONTRIBUTIONS_CACHE_TTL = 86400  # 24 hours

# GitHub green palette — 256-color approximations (much smaller output than
# true-color \033[38;2;R;G;Bm sequences, reducing per-render buffer size by ~60%).
CONTRIB_COLORS_256 = {
    "NONE": 239,              # dark gray
    "FIRST_QUARTILE": 157,    # light green
    "SECOND_QUARTILE": 71,    # medium green
    "THIRD_QUARTILE": 35,     # darker green
    "FOURTH_QUARTILE": 22,    # darkest green
}


def _fg_256(code: int) -> str:
    return f"\033[38;5;{code}m"


def _bg_256(code: int) -> str:
    return f"\033[48;5;{code}m"


def _read_contributions_cache() -> dict | None:
    try:
        with open(CONTRIBUTIONS_CACHE_PATH) as f:
            cached = json.load(f)
        if time.time() - cached.get("timestamp", 0) < CONTRIBUTIONS_CACHE_TTL:
            return cached
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _write_contributions_cache(data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data["timestamp"] = time.time()
        with open(CONTRIBUTIONS_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def _fetch_contributions() -> dict | None:
    """Fetch last 8 weeks of GitHub contributions via gh CLI."""
    cached = _read_contributions_cache()
    if cached:
        return cached

    try:
        # Get username
        user_result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=10,
        )
        if user_result.returncode != 0:
            return None
        username = user_result.stdout.strip()
        if not username:
            return None

        # Compute date range: last 8 weeks
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(weeks=8)).strftime("%Y-%m-%dT00:00:00Z")
        to_date = now.strftime("%Y-%m-%dT23:59:59Z")

        query = """
query($user: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $user) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionLevel
            weekday
          }
        }
      }
    }
  }
}"""
        gh_result = subprocess.run(
            ["gh", "api", "graphql",
             "-f", f"query={query}",
             "-F", f"user={username}",
             "-F", f"from={from_date}",
             "-F", f"to={to_date}"],
            capture_output=True, text=True, timeout=15,
        )
        if gh_result.returncode != 0:
            return None

        resp = json.loads(gh_result.stdout)
        cal = resp["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        weeks = cal["weeks"]
        total = cal["totalContributions"]

        # Take last 8 weeks
        weeks = weeks[-8:] if len(weeks) > 8 else weeks

        result = {"username": username, "total": total, "weeks": []}
        for w in weeks:
            days = []
            for d in w["contributionDays"]:
                days.append(d["contributionLevel"])
            result["weeks"].append(days)

        _write_contributions_cache(result)
        return result

    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _fetch_git_status() -> str | None:
    """Get a compact git status summary (uncached)."""
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if branch_result.returncode != 0:
            return None
        branch = branch_result.stdout.strip()

        diff_result = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        added = 0
        removed = 0
        if diff_result.returncode == 0:
            for line in diff_result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts_raw = line.split("\t")
                if len(parts_raw) >= 2 and parts_raw[0] != "-":
                    added += int(parts_raw[0])
                    removed += int(parts_raw[1])

        parts = [f"{CYAN}{branch}{RESET}"]
        if added or removed:
            parts.append(f"{GREEN}+{added}{RESET}")
            parts.append(f"{RED}-{removed}{RESET}")
        else:
            parts.append(f"{DIM}clean{RESET}")

        return " ".join(parts)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _get_git_status() -> str | None:
    """Get a compact git status summary with 5s cache."""
    cached = _cached_result("git_status", 5)
    if cached is not _MISSING:
        return cached
    result = _fetch_git_status()
    _write_cached_result("git_status", result)
    return result


def _render_contribution_grid(data: dict) -> list[str]:
    """Render contribution data as a 4-line half-block grid with day labels.

    Uses ▀ to pack two days per character. Rows = Mon–Sun, Columns = weeks.
    """
    weeks = data["weeks"]
    total = data["total"]
    num_weeks = len(weeks)

    # Build grid: grid[row][col], row 0=Sun..6=Sat (GitHub API order)
    grid = [["NONE"] * num_weeks for _ in range(7)]
    for col, week_days in enumerate(weeks):
        for i, level in enumerate(week_days):
            if i < 7:
                grid[i][col] = level

    def _half_block(top_level: str, bot_level: str) -> str:
        top_c = CONTRIB_COLORS_256.get(top_level, CONTRIB_COLORS_256["NONE"])
        bot_c = CONTRIB_COLORS_256.get(bot_level, CONTRIB_COLORS_256["NONE"])
        return f"{_fg_256(top_c)}{_bg_256(bot_c)}▀{RESET}"

    def _solo_block(level: str) -> str:
        c = CONTRIB_COLORS_256.get(level, CONTRIB_COLORS_256["NONE"])
        return f"{_fg_256(c)}{_bg_256(c)}▀{RESET}"

    # Pairs in Mon–Sun order: (Mon+Tue), (Wed+Thu), (Fri+Sat), Sun solo
    # GitHub API: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
    row_pairs = [(1, 2), (3, 4), (5, 6)]
    day_labels = ["M", "W", "F", "S"]

    # Right-side info
    git_status = _get_git_status()
    count_str = f"{total} contributions"
    weeks_str = f"{DIM}{num_weeks}w{RESET}"

    result_lines = []
    for idx, (top_row, bot_row) in enumerate(row_pairs):
        blocks = "".join(_half_block(grid[top_row][col], grid[bot_row][col]) for col in range(num_weeks))
        line = f"{DIM}{day_labels[idx]}{RESET} {blocks}"
        if idx == 0 and git_status:
            result_lines.append(f"{line}  {git_status}")
        elif idx == 1:
            result_lines.append(f"{line}  {count_str}")
        elif idx == 2:
            result_lines.append(f"{line}  {weeks_str}")
        else:
            result_lines.append(line)

    # Sun solo row
    sun_blocks = "".join(_solo_block(grid[0][col]) for col in range(num_weeks))
    result_lines.append(f"{DIM}{day_labels[3]}{RESET} {sun_blocks}")

    return result_lines


# ─── Main ────────────────────────────────────────────────────────────────────


def strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences for visible-length calculation."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def box_frame(lines: list, title: str = "", min_width: int = 0, title_right: str = "") -> str:
    """Wrap lines in a Unicode box-drawing frame with an optional title.

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
    framed.append(bottom)
    return "\n".join(framed)


def main() -> None:
    config = load_config()
    show = config["show"]
    bar_size = config["bar_size"]
    theme = config["theme"]

    stdin_data = read_stdin()

    # ── Fast path: reuse cached output if stdin unchanged and cache fresh ──
    cached_output = _read_output_cache(stdin_data)
    if cached_output is not None:
        print(cached_output)
        return

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

    # ── Parallel fetch: Spotify, Focus, System, Contributions ──────
    PARALLEL_TIMEOUT = 5  # seconds — never block render longer than this
    futures: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        if show.get("spotify", True):
            futures["spotify"] = pool.submit(get_spotify_now_playing)
        if show.get("focus", True):
            futures["focus"] = pool.submit(get_focus_status)
        if show.get("system", True):
            futures["system"] = pool.submit(get_system_info)
        if show.get("contributions", True):
            futures["contributions"] = pool.submit(_fetch_contributions)
        parallel: dict[str, object] = {}
        for k, f in futures.items():
            try:
                parallel[k] = f.result(timeout=PARALLEL_TIMEOUT)
            except TimeoutError:
                # For contributions, fall back to stale cache (ignore TTL)
                if k == "contributions":
                    parallel[k] = _read_contributions_cache()
                else:
                    parallel[k] = None

    info_parts: list[str] = []

    if show.get("spotify", True):
        spotify = parallel.get("spotify")
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
        focus = parallel.get("focus")
        if focus and focus.get("active"):
            info_parts.append(f"{YELLOW}Focus On{RESET}")
        else:
            info_parts.append(f"{DIM}Focus Off{RESET}")

    if show.get("system", True):
        sysinfo = parallel.get("system")
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

    # ── GitHub Contributions heatmap ──────────────────────────────────
    if show.get("contributions", True):
        contrib_data = parallel.get("contributions")
        if contrib_data and contrib_data.get("weeks"):
            content_lines.append("")  # padding
            contrib_lines = _render_contribution_grid(contrib_data)
            content_lines.extend(contrib_lines)

    # ── Stretch reminder ────────────────────────────────────────────
    title_right = ""
    if show.get("stretch", True):
        interval = config.get("stretch_interval_minutes", 15)
        sound = config.get("stretch_sound", "Glass")
        elapsed = _check_stretch_reminder(interval, sound)
        if elapsed:
            title_right = f"⏱ {elapsed}"

    # ── Output in box frame ───────────────────────────────────────────
    if content_lines:
        output = box_frame(content_lines, title=model_name, title_right=title_right)
    else:
        output = model_name
    _write_output_cache(stdin_data, output)
    print(output)


if __name__ == "__main__":
    main()
