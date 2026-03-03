You are activating the Redline statusline plugin.

Your task: Configure the user's Claude Code statusline by editing `~/.claude/settings.json` to add (or update) the `statusLine` field.

## Steps

1. Read `~/.claude/settings.json`
2. Add or update the `statusLine` field to:
   ```json
   "statusLine": {
     "type": "command",
     "command": "python3 ${PLUGIN_INSTALL_PATH}/statusline.py"
   }
   ```
   where `${PLUGIN_INSTALL_PATH}` is the actual absolute path to the installed plugin. Find it by looking up the `redline@custom-claude-config` entry in `~/.claude/plugins/installed_plugins.json` and using its `installPath` value.
3. Preserve all other existing settings — only add/update the `statusLine` key.
4. Tell the user the statusline is configured and they need to **restart Claude Code** for it to take effect.

## After setup, share this info

Redline displays a rich statusline showing:
- **Model info**: Current model name (e.g. Opus 4.6)
- **Context window**: Token usage, percentage used/remaining
- **Session usage**: 5-hour rolling window utilization with progress bar
- **Weekly usage**: 7-day rolling window utilization with progress bar
- **Reset times**: When session and weekly limits reset

### Configuration

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

### Managing the Plugin

```
/plugin disable redline    # Temporarily disable
/plugin enable redline     # Re-enable
/plugin uninstall redline  # Fully remove
```
