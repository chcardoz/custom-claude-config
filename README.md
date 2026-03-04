# Redline

A Claude Code statusline plugin that displays model info, usage limits, Spotify, system metrics, GitHub contributions, and more — all in a compact box frame.

```
╭─ Claude Opus 4 ────────────────────────────────────────────────────── ⏱ 34m ─╮
│ ctx ████░░░░░░ 42%  sess █░░░░░░░░░ 13%  week ███░░░░░░░ 26%                 │
│ ↻ sess 6:00pm  · week mar 5, 7:00pm                                          │
│ ♫ ▶ Overcompensate - Twenty One Pilots  ·  Focus Off  ·  CPU 32%  ·  MEM 71% │
│                                                                              │
│ M ▀▀▀▀▀▀▀▀  chcardoz/montreal +323 -51                                       │
│ W ▀▀▀▀▀▀▀▀  262 contributions                                                │
│ F ▀▀▀▀▀▀▀▀  8w                                                               │
│ S ▀▀▀▀▀▀▀▀                                                                   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## Requirements

- Python 3.10+
- macOS (for Spotify, Focus, and system metrics via `osascript`, `vm_stat`, etc.)
- `gh` CLI (for GitHub contributions graph)

## Installation

### Option A — Local development (during dev/testing)

```bash
claude --plugin-dir ./redline
```

Loads the plugin directly without installing. Good for iterating.

### Option B — Install from local path

```bash
/plugin marketplace add ./redline
/plugin install redline@redline
```

Or use the interactive `/plugin` UI → Discover tab → select redline → choose scope.

### Option C — Install from GitHub

```bash
/plugin marketplace add your-username/redline
/plugin install redline@your-username-redline
```

### Choosing a scope

When installing, you pick a scope:

- **User** (`~/.claude/settings.json`) — applies to all projects, all sessions. Best for a statusline.
- **Project** (`.claude/settings.json`) — applies to everyone on the repo.
- **Local** (`.claude/settings.local.json`) — just you, just this repo.

## What it shows

| Section | Content |
|---------|---------|
| Progress bars | Context window, session (5h), and weekly (7d) usage with color-coded bars |
| Reset times | When session and weekly limits reset |
| Spotify | Currently playing track and play/pause state |
| Focus | macOS Focus/DND status |
| System | CPU and memory usage percentages |
| Contributions | 8-week GitHub contribution heatmap with git branch and diff stats |
| Stretch reminder | Session elapsed time with periodic stretch notifications |

Progress bars are color-coded:
- **Green** — usage below 50%
- **Yellow** — usage between 50–80%
- **Red** — usage above 80%

Usage bars require an OAuth token. Without one, the statusline gracefully falls back to skeleton bars.

## Configuration

Edit `config.json` in the plugin directory:

```json
{
  "show": {
    "model": true,
    "context": true,
    "session": true,
    "weekly": true,
    "reset_times": true,
    "spotify": true,
    "focus": true,
    "system": true,
    "stretch": true,
    "contributions": true
  },
  "bar_size": 10,
  "cache_ttl_seconds": 60,
  "stretch_interval_minutes": 15,
  "stretch_sound": "Glass",
  "theme": {
    "low_threshold": 50,
    "high_threshold": 80
  }
}
```

| Option | Description | Default |
|--------|-------------|---------|
| `show.*` | Toggle individual sections on/off | all `true` |
| `bar_size` | Number of characters in progress bars | `10` |
| `cache_ttl_seconds` | How long to cache API usage responses | `60` |
| `stretch_interval_minutes` | Minutes between stretch reminder notifications | `15` |
| `stretch_sound` | macOS sound name for stretch notifications | `"Glass"` |
| `theme.low_threshold` | Usage % below this is green | `50` |
| `theme.high_threshold` | Usage % above this is red | `80` |

## Performance

Expensive subprocess calls (Spotify, Focus, system metrics, git status) are cached with short TTLs (5–10s) and run in parallel using a thread pool. This brings typical render time down from ~500ms to under 100ms on cache hits.

Cache files are stored in `~/.cache/redline/`. Delete the directory to force a fresh fetch.

## Credential sources

The plugin looks for an OAuth token in this order:

1. `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`
2. macOS Keychain → "Claude Code-credentials" (macOS only)
3. `CLAUDE_CODE_OAUTH_TOKEN` environment variable

## Managing the plugin

```bash
/plugin disable redline          # Temporarily disable (keeps config)
/plugin enable redline           # Re-enable
/plugin uninstall redline        # Fully remove
```

Or use the interactive `/plugin` UI → Installed tab.

## Slash command

Run `/redline` inside Claude Code for a quick reference of what the plugin shows and how to configure it.
