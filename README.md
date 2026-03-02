# Redline

A Claude Code plugin that displays a rich statusline showing model info, context window usage, and session/weekly rate limits with progress bars.

```
Opus 4.6 | 0 / 1.0m | 0% used 0 | 100% remain 1000000
current: ○○○○○○○○○○ 1%   |  weekly: ●○○○○○○○○○ 16%
resets 2:30pm              |  resets mar 6, 8:30am
```

## Requirements

- Python 3.10+

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

| Line | Content |
|------|---------|
| 1 | Model name, token count / window size, used %, remaining % |
| 2 | Session (5-hour) and weekly (7-day) usage progress bars |
| 3 | Reset times for session and weekly limits |

Progress bars are color-coded:
- **Green** — usage below 50%
- **Yellow** — usage between 50–80%
- **Red** — usage above 80%

Lines 2–3 require an OAuth token. Without one, the statusline gracefully falls back to showing only model and context info.

## Configuration

Edit `config.json` in the plugin directory:

```json
{
  "show": {
    "model": true,
    "context": true,
    "session": true,
    "weekly": true,
    "reset_times": true
  },
  "bar_size": 10,
  "cache_ttl_seconds": 60,
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
| `cache_ttl_seconds` | How long to cache API responses | `60` |
| `theme.low_threshold` | Usage % below this is green | `50` |
| `theme.high_threshold` | Usage % above this is red | `80` |

## Credential sources

The plugin looks for an OAuth token in this order:

1. `~/.claude/.credentials.json` → `claudeAiOauth.accessToken`
2. macOS Keychain → "Claude Code-credentials" (macOS only)
3. `CLAUDE_CODE_OAUTH_TOKEN` environment variable

## Cache

Usage data is cached at `~/.cache/redline/cache.json` to avoid excessive API calls. Delete this file to force a fresh fetch.

## Managing the plugin

```bash
/plugin disable redline          # Temporarily disable (keeps config)
/plugin enable redline           # Re-enable
/plugin uninstall redline        # Fully remove
```

Or use the interactive `/plugin` UI → Installed tab.

## Slash command

Run `/redline` inside Claude Code for a quick reference of what the plugin shows and how to configure it.
