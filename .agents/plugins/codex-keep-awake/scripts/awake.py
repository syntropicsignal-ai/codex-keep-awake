#!/usr/bin/env python3
"""Maintain one bounded caffeinate process for all active Codex turns."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


MAX_HOLD_SECONDS = 12 * 60 * 60
REFRESH_AFTER_SECONDS = 10 * 60 * 60
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
               started_at REAL NOT NULL
           )"""
    )
    os.chmod(DATABASE, 0o600)
    return connection


def _identity(pid: int) -> tuple[str, str] | None:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm=", "-o", "lstart="],
        check=False,
        capture_output=True,
        text=True,
        timeout=1,
    )
    fields = result.stdout.strip().split(None, 1)
    if result.returncode != 0 or len(fields) != 2:
        return None
    return fields[0], fields[1]


def _is_our_process(pid: int, started: str) -> bool:
    identity = _identity(pid)
    return bool(
        identity
        and Path(identity[0]).name == "caffeinate"
        and identity[1] == started
    )


def _stop_process(row: tuple[int, str, float] | None) -> None:
    if row is None:
        return
    pid, started, _ = row
    if _is_our_process(pid, started):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _start_process() -> tuple[int, str, float]:
    process = subprocess.Popen(
        ["/usr/bin/caffeinate", "-i", "-t", str(MAX_HOLD_SECONDS)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(25):
        identity = _identity(process.pid)
        if identity and Path(identity[0]).name == "caffeinate":
            return process.pid, identity[1], time.time()
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
    lease_count = connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
    row = connection.execute(
        "SELECT pid, process_started, started_at FROM wake_process WHERE singleton = 1"
    ).fetchone()
    process_is_live = bool(row and _is_our_process(row[0], row[1]))

    if lease_count == 0:
        _stop_process(row)
        connection.execute("DELETE FROM wake_process WHERE singleton = 1")
        return

    should_refresh = bool(
        row and now - row[2] >= REFRESH_AFTER_SECONDS
    )
    if process_is_live and not should_refresh:
        return

    _stop_process(row)
    connection.execute("DELETE FROM wake_process WHERE singleton = 1")
    pid, started, started_at = _start_process()
    connection.execute(
        "INSERT INTO wake_process VALUES (1, ?, ?, ?)",
        (pid, started, started_at),
    )


def handle(action: str, event: dict[str, Any]) -> None:
    connection = _db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        now = time.time()

        if action in {"touch", "subagent-start"}:
            kind = "subagent" if action == "subagent-start" else "main"
            lease_id = _lease_id(event, kind)
            if lease_id:
                connection.execute(
                    "INSERT INTO leases VALUES (?, ?, ?) "
                    "ON CONFLICT(lease_id) DO UPDATE SET expires_at = excluded.expires_at",
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
        elif action == "session-end":
            session_id = event.get("session_id")
            if session_id:
                connection.execute(
                    "DELETE FROM leases WHERE session_id = ?", (session_id,)
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
