"""Returning freed memory to the operating system.

Walking searches over a large city allocate big temporary arrays. glibc keeps the freed space in the
process, so resident memory grows with use although the data is gone: measured on Warszawa, 60 trip
plans took RSS from 231 to 280 MB, and malloc_trim brought it back to 220 MB. Calling it after each
tool call (in a background thread, after a short quiet spell) keeps RSS close to what is actually in use. Elsewhere (macOS, Windows, musl) it does nothing.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

try:
    _libc = ctypes.CDLL("libc.so.6") if sys.platform.startswith("linux") else None
    _trim = _libc.malloc_trim if _libc else None
    # Optionally, allocations above a threshold (e.g. a walking search's ~20 MB distance arrays on
    # Warszawa) get their own mapping, which goes back to the OS as soon as it is freed.
    # Off by default: it made trip planning ~25 % slower on Gdańsk (each walking search then gets
    # fresh pages from the OS). Set POLAND_TRANSIT_MMAP_THRESHOLD=1048576 to trade speed for steadier memory.
    _threshold = int(os.environ.get("POLAND_TRANSIT_MMAP_THRESHOLD", 0))
    if _libc and _threshold:
        _libc.mallopt(-3, _threshold)  # M_MMAP_THRESHOLD
    LARGE_FREES_RETURNED = bool(_libc and _threshold)
except (OSError, AttributeError):  # not glibc
    _trim = None
    LARGE_FREES_RETURNED = False


_pending = threading.Event()


def _worker() -> None:
    while True:
        _pending.wait()
        # trim once things have been quiet for a moment, off the response path
        while _pending.is_set():
            _pending.clear()
            time.sleep(0.25)
        _trim(0)


def release_free_memory() -> None:
    """Asks a background thread to trim the heap shortly after the current burst of calls."""
    global _thread
    if _trim is None:
        return
    if _thread is None:
        _thread = threading.Thread(target=_worker, name="malloc-trim", daemon=True)
        _thread.start()
    _pending.set()


_thread: threading.Thread | None = None
