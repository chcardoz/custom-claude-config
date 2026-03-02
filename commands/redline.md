Redline — Claude Code Statusline Plugin

Redline displays a rich statusline showing:
- **Model info**: Current model name (e.g. Opus 4.6)
- **Context window**: Token usage, percentage used/remaining
- **Session usage**: 5-hour rolling window utilization with progress bar
- **Weekly usage**: 7-day rolling window utilization with progress bar
- **Reset times**: When session and weekly limits reset

## Configuration

Edit `config.json` in the plugin directory to customize:

- `show.model` — Toggle model name display (default: true)
- `show.context` — Toggle context window info (default: true)
- `show.session` — Toggle session usage bar (default: true)
- `show.weekly` — Toggle weekly usage bar (default: true)
- `show.reset_times` — Toggle reset time display (default: true)
- `bar_size` — Number of characters in progress bars (default: 10)
- `cache_ttl_seconds` — How long to cache API responses (default: 60)
- `theme.low_threshold` — Usage % below this is green (default: 50)
- `theme.high_threshold` — Usage % above this is red (default: 80)

## Managing the Plugin

```
/plugin disable redline    # Temporarily disable
/plugin enable redline     # Re-enable
/plugin uninstall redline  # Fully remove
```

## Cache

Usage data is cached at `~/.cache/redline/cache.json` to avoid excessive API calls.
Delete this file to force a fresh API call.
