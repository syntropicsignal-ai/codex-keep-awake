# Codex Keep Awake

A small Codex plugin that keeps macOS awake while local Codex turns are active.

## Core algorithm

- Keep a set of active turn and subagent lease IDs in SQLite.
- Serialize hook updates with SQLite's write transaction.
- Run one shared `caffeinate` process while the set is non-empty.
- Stop that process when the final lease is released.
- Expire abandoned leases after 12 hours; the `caffeinate` process has the same hard timeout as a final failsafe.
- Every lifecycle hook invokes the same Python entry point; it dispatches from Codex's `hook_event_name` field.

The timeout is deliberately bounded. The plugin subscribes only to prompt submission, turn completion or interruption, and subagent start or stop. Acquiring an existing lease and releasing an absent lease are no-ops, so duplicate lifecycle events are safe. Close active Codex sessions before updating or uninstalling the plugin so their loaded hooks do not point at a removed cache directory. A single turn lasting longer than 12 hours can outlive the failsafe; increase the limit if that is a realistic workload.

## Sleep mode

The default `idle` mode runs `caffeinate -i`: it prevents idle system sleep while allowing the display to sleep.

Choose `system` mode to add `caffeinate -s`. macOS supports this stronger system-sleep assertion only while connected to AC power. The display can still sleep. macOS can still enter clamshell sleep when a MacBook lid closes; this mode does not override that behavior. Apple documents closed-lid use with an external display, power, and external input devices.

Use the bundled `$codex-keep-awake:keep-awake` skill in Codex to check the current status or choose a mode. You can also set it with the installed helper:

```sh
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PLUGIN_ROOT="$CODEX_HOME/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake/0.7.0"
python3 "$PLUGIN_ROOT/scripts/awake.py" set-mode system
python3 "$PLUGIN_ROOT/scripts/awake.py" set-mode idle
```

The selected mode is stored in `~/Library/Application Support/CodexKeepAwake/config.json`. Replace `0.7.0` with the installed version if you update the plugin later. The default remains `idle` when no mode has been selected.

## Install in Codex

From Codex CLI, add this GitHub marketplace and install the plugin:

```sh
codex plugin marketplace add syntropicsignal-ai/codex-keep-awake --sparse .agents/plugins
codex plugin add codex-keep-awake@syntropicsignal-ai-tools
```

Review and trust the plugin hooks in Codex before they run. Start a new Codex session after installation.

## Local state

The plugin stores the selected mode in:

```text
~/Library/Application Support/CodexKeepAwake/config.json
```

It stores lease IDs, session IDs, expiry times, and the managed `caffeinate` process identity and active mode in:

```text
~/Library/Application Support/CodexKeepAwake/state.sqlite3
```

To print the current mode, active leases, their Codex conversation titles when available, and the managed process ID:

```sh
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PLUGIN_ROOT="$CODEX_HOME/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake/0.7.0"
python3 "$PLUGIN_ROOT/scripts/awake.py" status
```

The status command also reads `~/.codex/session_index.jsonl` to resolve conversation titles when available. The plugin does not read prompts or transcripts, inspect repository files, or send network requests.
