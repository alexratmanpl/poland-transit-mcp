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


def read_only_uri(path: Path) -> str:
    """The SQLite URI that opens `path` read-only. The path is percent-encoded: a `?` or `#` in it, written as it
    is, would end the file name there and drop `mode=ro`, so another file would be opened, and opened writable."""
    return Path(path).absolute().as_uri() + "?mode=ro"


def connect_read_only(path: Path, **kw) -> sqlite3.Connection:
    """A connection that cannot change the file: opened read-only, and with query_only set as well."""
    conn = sqlite3.connect(read_only_uri(path), uri=True, **kw)
    conn.execute("PRAGMA query_only = ON")
    return conn


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
        return cls(connect_read_only(path, check_same_thread=False))

    def execute(self, sql: str, params=()) -> Rows:
        with self._lock:
            return Rows(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
