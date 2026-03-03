#!/usr/bin/env bash
# setup-statusline.sh — Idempotently add the redline statusLine config
# to ~/.claude/settings.json on session start.

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
STATUSLINE_CMD="python3 ${PLUGIN_ROOT}/statusline.py"

# Ensure ~/.claude directory exists
mkdir -p "$(dirname "$SETTINGS_FILE")"

# Use Python (already required by statusline.py) for safe JSON manipulation
python3 - "$SETTINGS_FILE" "$STATUSLINE_CMD" <<'PYEOF'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
statusline_cmd = sys.argv[2]

# Read existing settings or start with empty object
if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        settings = {}
else:
    settings = {}

# Write if statusLine is missing or points to a different redline path
existing = settings.get("statusLine", {})
if existing.get("command") != statusline_cmd:
    settings["statusLine"] = {
        "type": "command",
        "command": statusline_cmd
    }
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
PYEOF
