from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1] / ".agents" / "plugins" / "codex-keep-awake"
SCRIPT = PLUGIN_ROOT / "scripts" / "awake.py"
SPEC = importlib.util.spec_from_file_location("awake", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {SCRIPT}")
AWAKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AWAKE)


class SleepModeTests(unittest.TestCase):
    def test_idle_mode_keeps_display_sleep_available(self) -> None:
        self.assertEqual(AWAKE._caffeinate_arguments("idle", 30), ["-i", "-t", "30"])

    def test_system_mode_adds_system_sleep_assertion(self) -> None:
        self.assertEqual(
            AWAKE._caffeinate_arguments("system", 30),
            ["-i", "-s", "-t", "30"],
        )

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AWAKE._caffeinate_arguments("clamshell", 30)

    def test_cli_persists_selected_mode(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            environment = {**os.environ, "HOME": home}
            selected = subprocess.run(
                [sys.executable, str(SCRIPT), "set-mode", "system"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(json.loads(selected.stdout), {"mode": "system"})

            status = subprocess.run(
                [sys.executable, str(SCRIPT), "status"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(json.loads(status.stdout)["configured_mode"], "system")


if __name__ == "__main__":
    unittest.main()
