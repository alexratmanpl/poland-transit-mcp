"""A city file's read-only SQLite connection, shared by the threads that answer tool calls.

Tool calls run in worker threads, several at once over --http. One sqlite3 connection must not run two
statements from two threads at the same time: their steps interleave and fail with InterfaceError ("bad
parameter or other API misuse"), or return wrong rows. A statement here runs to its last row under the
connection's lock, so the lock is never held while the caller works on the rows.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Rows(list):
    """A statement's rows, read in full; `fetchone` and `fetchall` as on a cursor."""

    def fetchone(self):
        return self[0] if self else None

    def fetchall(self) -> list:
        return list(self)


class SharedConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()

    @classmethod
    def read_only(cls, path: Path) -> "SharedConnection":
        return cls(sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False))

    def execute(self, sql: str, params=()) -> Rows:
        with self._lock:
            return Rows(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
