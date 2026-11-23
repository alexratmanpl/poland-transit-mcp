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
    is, would end the file name there and drop `mode=ro`, so another file would be opened, and opened writable.

    `immutable=1`: SQLite reads the file alone. It does not read a `-wal` or `-journal` file beside it (whose
    pages would override the file's own) and creates no `-shm` or `-wal` file next to it. A city file is never
    changed in place: the build and `download` write a new file and rename it over the old one, and a
    connection already open keeps reading the file it opened."""
    return Path(path).absolute().as_uri() + "?mode=ro&immutable=1"


def refuse_wal_mode(path: Path) -> None:
    """Refuses a file whose header says WAL mode (bytes 18 and 19 are 2). Its latest pages may live in a `-wal`
    file, which the connection does not read, so the file alone may be out of date or half checkpointed; other
    SQLite tools would read the `-wal` and show something else. The build never leaves a file in WAL mode.
    A file that cannot be read, or is not an SQLite file, is left to sqlite3 to report."""
    try:
        with open(path, "rb") as f:
            head = f.read(100)
    except OSError:
        return
    if head.startswith(b"SQLite format 3\x00") and len(head) >= 20 and 2 in (head[18], head[19]):
        raise ValueError(f"{Path(path).name} is in WAL mode: part of its data may be in a separate -wal file, "
                         "which is not read; rebuild or re-download the city file")


def connect_read_only(path: Path, **kw) -> sqlite3.Connection:
    """A connection that cannot change the file: opened read-only and immutable (read_only_uri), with query_only
    set as well. A file in WAL mode is refused (refuse_wal_mode)."""
    refuse_wal_mode(path)
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
