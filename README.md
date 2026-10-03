# Codex Keep Awake

A small Codex plugin that prevents macOS idle sleep while local Codex turns are active.

## Core algorithm

- Keep a set of active turn and subagent lease IDs in SQLite.
- Serialize hook updates with SQLite's write transaction.
- Run one shared `caffeinate -i` process while the set is non-empty.
- Stop that process when the final lease is released.
- Expire abandoned leases after 12 hours; the `caffeinate` process has the same hard timeout as a final failsafe.

The timeout is deliberately bounded. The plugin subscribes only to prompt submission, turn completion or interruption, and subagent start or stop. Acquiring an existing lease and releasing an absent lease are no-ops, so duplicate lifecycle events are safe. Hooks also check that the helper still exists before running it, so a session holding an old plugin-cache path can finish without a missing-file hook error during an update or uninstall. A single turn lasting longer than 12 hours can outlive the failsafe; increase the limit if that is a realistic workload.

`-i` prevents idle system sleep while allowing the display to sleep. Closing a MacBook lid can still trigger clamshell sleep. This plugin does not change `pmset` or request administrator access.

## Install in Codex

From Codex CLI, add this GitHub marketplace and install the plugin:

```sh
codex plugin marketplace add syntropicsignal-ai/codex-keep-awake --sparse .agents/plugins
codex plugin add codex-keep-awake@syntropicsignal-ai-tools
```

Review and trust the plugin hooks in Codex before they run. Start a new Codex session after installation.

## Local state

The plugin stores only active lease IDs, session IDs, expiry times, and the managed `caffeinate` process identity in:

```text
~/Library/Application Support/CodexKeepAwake/state.sqlite3
```

It does not read prompts, transcripts, repository files, or send network requests.
