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
DATABASE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "CodexKeepAwake"
    / "state.sqlite3"
)


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


def _stop_process(row: tuple[int, str, float, float] | None) -> None:
    if row is None:
        return
    pid, started, _, _ = row
    if _is_our_process(pid, started):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _start_process(expires_at: float, now: float) -> tuple[int, str, float, float]:
    timeout_seconds = max(1, math.ceil(expires_at - now))
    process = subprocess.Popen(
        ["/usr/bin/caffeinate", "-i", "-t", str(timeout_seconds)],
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
    connection.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
    lease_count, latest_expiry = connection.execute(
        "SELECT COUNT(*), MAX(expires_at) FROM leases"
    ).fetchone()
    row = connection.execute(
        "SELECT pid, process_started, started_at, expires_at "
        "FROM wake_process WHERE singleton = 1"
    ).fetchone()
    process_is_live = bool(row and _is_our_process(row[0], row[1]))

    if lease_count == 0:
        _stop_process(row)
        connection.execute("DELETE FROM wake_process WHERE singleton = 1")
        return

    if process_is_live and row[3] >= latest_expiry:
        return

    _stop_process(row)
    connection.execute("DELETE FROM wake_process WHERE singleton = 1")
    pid, started, started_at, expires_at = _start_process(latest_expiry, now)
    connection.execute(
        "INSERT INTO wake_process VALUES (1, ?, ?, ?, ?)",
        (pid, started, started_at, expires_at),
    )


def handle(action: str, event: dict[str, Any]) -> None:
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


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: awake.py ACTION")
    action = sys.argv[1]
    event = json.load(sys.stdin)
    handle(action, event)
    if event.get("hook_event_name") == "Stop":
        print("{}")


if __name__ == "__main__":
    main()
