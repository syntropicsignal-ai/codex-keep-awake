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

Set the mode with the installed helper:

```sh
python3 "$HOME/.codex/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake/0.6.0/scripts/awake.py" set-mode system
python3 "$HOME/.codex/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake/0.6.0/scripts/awake.py" set-mode idle
```

The mode is stored in `~/Library/Application Support/CodexKeepAwake/config.json`. Replace `0.6.0` in the command with the installed version if you update the plugin later. The default remains `idle` when no mode has been selected.

## Install in Codex

From Codex CLI, add this GitHub marketplace and install the plugin:

```sh
codex plugin marketplace add syntropicsignal-ai/codex-keep-awake --sparse .agents/plugins
codex plugin add codex-keep-awake@syntropicsignal-ai-tools
```

Review and trust the plugin hooks in Codex before they run. Start a new Codex session after installation.

## Local state

The plugin stores the selected mode and active lease IDs, session IDs, expiry times, and the managed `caffeinate` process identity in:

```text
~/Library/Application Support/CodexKeepAwake/state.sqlite3
```

To print the current mode, active leases, their Codex conversation titles when available, and the managed process ID:

```sh
python3 "$HOME/.codex/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake/0.6.0/scripts/awake.py" status
```

The database contains only the current mode, active leases, and process identity. It does not read prompts or transcripts, access repository files, or send network requests.
