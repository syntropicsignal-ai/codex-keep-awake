#!/usr/bin/env python3
"""Maintain one bounded caffeinate process for all active Codex turns."""

from __future__ import annotations

import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


LEASE_TTL_SECONDS = 12 * 60 * 60
HOOK_ACTIONS = {
    "UserPromptSubmit": "acquire",
    "SubagentStart": "subagent-start",
    "SubagentStop": "subagent-stop",
    "Stop": "release",
    "Interrupt": "release",
}
DATABASE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "CodexKeepAwake"
    / "state.sqlite3"
)
MODE_CONFIG = DATABASE.parent / "config.json"
SESSION_INDEX = Path.home() / ".codex" / "session_index.jsonl"
SLEEP_MODES = {"idle", "system"}


def _db() -> sqlite3.Connection:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DATABASE.parent, 0o700)
    connection = sqlite3.connect(DATABASE, timeout=5, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS leases (
               lease_id TEXT PRIMARY KEY,
               session_id TEXT NOT NULL,
               expires_at REAL NOT NULL
           )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS wake_process (
               singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
               pid INTEGER NOT NULL,
               process_started TEXT NOT NULL,
               started_at REAL NOT NULL,
               expires_at REAL NOT NULL DEFAULT 0
           )"""
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(wake_process)")
    }
    if "expires_at" not in columns:
        connection.execute(
            "ALTER TABLE wake_process ADD COLUMN expires_at REAL NOT NULL DEFAULT 0"
        )
    if "mode" not in columns:
        connection.execute(
            "ALTER TABLE wake_process ADD COLUMN mode TEXT NOT NULL DEFAULT 'idle'"
        )
    os.chmod(DATABASE, 0o600)
    return connection


def _identity(pid: int) -> tuple[str, str] | None:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
        timeout=1,
    )
    fields = result.stdout.strip().split(None, 5)
    if result.returncode != 0 or len(fields) != 6:
        return None
    return fields[5], " ".join(fields[:5])


def _is_caffeinate(command: str) -> bool:
    executable = command.split(None, 1)[0]
    return Path(executable).name == "caffeinate"


def _is_our_process(pid: int, started: str) -> bool:
    identity = _identity(pid)
    return bool(
        identity
        and _is_caffeinate(identity[0])
        and identity[1] == started
    )


def _stop_process(row: tuple[Any, ...] | None) -> None:
    if row is None:
        return
    pid, started, _, _ = row[:4]
    if _is_our_process(pid, started):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _caffeinate_arguments(mode: str, timeout_seconds: int) -> list[str]:
    if mode not in SLEEP_MODES:
        raise ValueError("mode must be 'idle' or 'system'")
    command = ["-i"]
    if mode == "system":
        command.append("-s")
    command.extend(("-t", str(timeout_seconds)))
    return command


def _start_process(
    expires_at: float, now: float, mode: str
) -> tuple[int, str, float, float]:
    timeout_seconds = max(1, math.ceil(expires_at - now))
    command = ["/usr/bin/caffeinate", *_caffeinate_arguments(mode, timeout_seconds)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(25):
        identity = _identity(process.pid)
        if identity and _is_caffeinate(identity[0]):
            started_at = time.time()
            return process.pid, identity[1], started_at, started_at + timeout_seconds
        time.sleep(0.02)
    process.terminate()
    raise RuntimeError("Could not confirm the caffeinate process identity")


def _configured_mode() -> str:
    try:
        with MODE_CONFIG.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        return "idle"
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read {MODE_CONFIG}: {error}") from error
    if not isinstance(config, dict) or config.get("mode") not in SLEEP_MODES:
        raise ValueError(f"{MODE_CONFIG} must contain mode 'idle' or 'system'")
    return config["mode"]


def _set_mode(mode: str) -> None:
    if mode not in SLEEP_MODES:
        raise ValueError("mode must be 'idle' or 'system'")
    MODE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(MODE_CONFIG.parent, 0o700)
    temporary_path = MODE_CONFIG.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps({"mode": mode}) + "\n", encoding="utf-8"
    )
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(MODE_CONFIG)
    if DATABASE.exists():
        connection = _db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _reconcile(connection, time.time())
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _conversation_title(session_id: str | None) -> str | None:
    if not session_id:
        return None
    title = None
    try:
        with SESSION_INDEX.open(encoding="utf-8") as index:
            for line in index:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("id") == session_id:
                    value = entry.get("thread_name")
                    title = value if isinstance(value, str) and value else title
    except (OSError, UnicodeError):
        return None
    return title


def _lease_id(event: dict[str, Any], kind: str = "main") -> str | None:
    session_id = event.get("session_id")
    turn_id = event.get("turn_id")
    if not session_id or not turn_id:
        return None
    suffix = event.get("agent_id") if kind == "subagent" else kind
    if kind == "subagent" and not suffix:
        return None
    return f"{session_id}:{turn_id}:{suffix}"


def _reconcile(connection: sqlite3.Connection, now: float) -> None:
    mode = _configured_mode()
    connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
    lease_count, latest_expiry = connection.execute(
        "SELECT COUNT(*), MAX(expires_at) FROM leases"
    ).fetchone()
    row = connection.execute(
        "SELECT pid, process_started, started_at, expires_at, mode "
        "FROM wake_process WHERE singleton = 1"
    ).fetchone()
    process_is_live = bool(row and _is_our_process(row[0], row[1]))

    if lease_count == 0:
        _stop_process(row)
        connection.execute("DELETE FROM wake_process WHERE singleton = 1")
        return

    if process_is_live and row[3] >= latest_expiry and row[4] == mode:
        return

    _stop_process(row)
    connection.execute("DELETE FROM wake_process WHERE singleton = 1")
    pid, started, started_at, expires_at = _start_process(
        latest_expiry, now, mode
    )
    connection.execute(
        "INSERT INTO wake_process (singleton, pid, process_started, started_at, expires_at, mode) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (pid, started, started_at, expires_at, mode),
    )


def handle(event: dict[str, Any]) -> None:
    action = HOOK_ACTIONS.get(event.get("hook_event_name", ""))
    if action is None:
        return

    connection = _db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        now = time.time()
        connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))

        if action in {"acquire", "subagent-start"}:
            kind = "subagent" if action == "subagent-start" else "main"
            lease_id = _lease_id(event, kind)
            if lease_id:
                connection.execute(
                    "INSERT OR IGNORE INTO leases VALUES (?, ?, ?)",
                    (
                        lease_id,
                        event["session_id"],
                        now + LEASE_TTL_SECONDS,
                    ),
                )
        elif action in {"release", "subagent-stop"}:
            kind = "subagent" if action == "subagent-stop" else "main"
            lease_id = _lease_id(event, kind)
            if lease_id:
                connection.execute(
                    "DELETE FROM leases WHERE lease_id = ?", (lease_id,)
                )
        _reconcile(connection, now)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _show_status() -> None:
    mode = _configured_mode()
    if not DATABASE.exists():
        print(json.dumps({"configured_mode": mode, "active_leases": []}))
        return
    connection = sqlite3.connect(f"{DATABASE.as_uri()}?mode=ro", uri=True)
    try:
        now = time.time()
        leases = []
        for lease_id, session_id, expires_at in connection.execute(
            "SELECT lease_id, session_id, expires_at FROM leases "
            "WHERE expires_at > ? ORDER BY expires_at",
            (now,),
        ):
            parts = lease_id.split(":")
            leases.append(
                {
                    "session_id": session_id,
                    "turn_id": parts[1] if len(parts) > 1 else None,
                    "owner": parts[2] if len(parts) > 2 else None,
                    "conversation_title": _conversation_title(session_id),
                    "expires_in_seconds": max(0, round(expires_at - now)),
                }
            )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(wake_process)")
        }
        if "mode" in columns:
            process = connection.execute(
                "SELECT pid, mode FROM wake_process WHERE singleton = 1"
            ).fetchone()
        else:
            old_process = connection.execute(
                "SELECT pid FROM wake_process WHERE singleton = 1"
            ).fetchone()
            process = (old_process[0], "idle") if old_process else None
        print(
            json.dumps(
                {
                    "caffeinate_pid": process[0] if process else None,
                    "configured_mode": mode,
                    "active_mode": process[1] if process else None,
                    "active_leases": leases,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        connection.close()


def main() -> None:
    if sys.argv[1:] == ["status"]:
        _show_status()
        return
    if len(sys.argv) == 3 and sys.argv[1] == "set-mode":
        _set_mode(sys.argv[2])
        print(json.dumps({"mode": sys.argv[2]}))
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: awake.py [status | set-mode idle|system]")
    event = json.load(sys.stdin)
    handle(event)
    if event.get("hook_event_name") in {"Stop", "SubagentStop"}:
        print("{}")


if __name__ == "__main__":
    main()
