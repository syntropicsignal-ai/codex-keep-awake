---
name: keep-awake
description: Check the active Codex Keep Awake status or switch its persistent sleep mode between idle and system.
---

# Keep Awake controls

Use the installed Codex Keep Awake plugin helper to inspect or change the mode. Find the currently installed plugin under `$CODEX_HOME/plugins/cache/syntropicsignal-ai-tools/codex-keep-awake` (use `~/.codex` when `CODEX_HOME` is unset) and run its `scripts/awake.py`; if several version folders exist, use the version that provided this skill.

- For a status request, run `awake.py status` and explain the configured mode, active mode, active Codex leases, and whether `caffeinate` is running.
- For a mode change, run `awake.py set-mode idle` or `awake.py set-mode system` according to the user's request, then run `awake.py status` to confirm the result.
- `idle` prevents idle system sleep while allowing the display to sleep. `system` adds macOS's stronger system-sleep assertion, which is supported only on AC power. It does not guarantee that a MacBook will stay awake when its lid is closed.
- If no Codex leases are active, the selected mode is saved for the next active turn; report that no current assertion is running.
- Do not change any other Codex or macOS sleep settings.
