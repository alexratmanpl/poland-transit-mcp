"""A city file is read as it is: no side file beside it changes what is read, and a damaged or tampered file is
refused with a readable error before it can allocate memory by a value it holds."""
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from poland_transit_mcp.build.safeguard import read_feeds
from poland_transit_mcp.city import City
from poland_transit_mcp.shared_db import connect_read_only


def listing(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


def wal_copy(city_dir: Path, work: Path, into: Path) -> Path:
    """testowo switched to WAL mode, with a `-wal` file that renames the city. The files are copied while the
    writer is still open: closing it would write the change into the file and delete the `-wal`."""
    shutil.copy(city_dir / "testowo.sqlite", work / "testowo.sqlite")
    db = sqlite3.connect(work / "testowo.sqlite")
    assert db.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    db.execute("PRAGMA wal_autocheckpoint = 0")
    db.execute("UPDATE meta SET value = 'NAME FROM THE -wal FILE' WHERE key = 'name'")
    db.commit()
    shutil.copy(work / "testowo.sqlite", into / "testowo.sqlite")
    shutil.copy(work / "testowo.sqlite-wal", into / "testowo.sqlite-wal")
    db.close()
    return into / "testowo.sqlite"


def test_a_file_in_wal_mode_is_refused_and_its_wal_is_not_read(city_dir, tmp_path):
    work, served = tmp_path / "work", tmp_path / "served"
    work.mkdir()
    served.mkdir()
    path = wal_copy(city_dir, work, served)
    assert path.read_bytes()[18:20] == b"\x02\x02"
    plain = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)  # what a plain read-only open would show
    assert dict(plain.execute("SELECT key, value FROM meta"))["name"] == "NAME FROM THE -wal FILE"
    plain.close()
    (served / "testowo.sqlite-shm").unlink(missing_ok=True)  # that open makes one
    before = listing(served)
    with pytest.raises(ValueError, match="testowo.sqlite is in WAL mode.*rebuild or re-download"):
        City.open(served, "testowo")
    assert read_feeds(path) is None  # the build's check of the current file: nothing to compare with
    assert listing(served) == before  # no -shm made, the -wal left as it was


def test_a_wal_file_beside_a_file_in_rollback_mode_is_ignored(city_dir, tmp_path):
    work, served = tmp_path / "work", tmp_path / "served"
    work.mkdir()
    served.mkdir()
    wal_copy(city_dir, work, tmp_path)
    shutil.copy(city_dir / "testowo.sqlite", served / "testowo.sqlite")
    shutil.copy(tmp_path / "testowo.sqlite-wal", served / "testowo.sqlite-wal")  # planted next to a good file
    before = listing(served)
    c = City.open(served, "testowo")
    assert c.name == "Testowo (synthetic)"  # not the -wal file's name
    assert listing(served) == before


def test_opening_and_loading_a_city_writes_nothing_beside_it(city_dir, tmp_path):
    shutil.copy(city_dir / "testowo.sqlite", tmp_path / "testowo.sqlite")
    c = City.open(tmp_path, "testowo")
    assert c.tt.n_stops and c.graph.n
    con = connect_read_only(tmp_path / "testowo.sqlite")
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    con.close()
    assert listing(tmp_path) == ["testowo.sqlite"]


def tampered(city_dir: Path, tmp_path: Path, *changes: tuple[str, tuple]) -> City:
    shutil.copy(city_dir / "testowo.sqlite", tmp_path / "testowo.sqlite")
    db = sqlite3.connect(tmp_path / "testowo.sqlite")
    for sql, params in changes:
        db.execute(sql, params)
    db.commit()
    db.close()
    return City.open(tmp_path, "testowo")


@pytest.mark.parametrize("idx", [10_000_000, 2**62, -5])
def test_a_stop_index_far_out_is_refused_before_the_stop_list_is_allocated(city_dir, tmp_path, idx):
    """One stop_idx of 150 million got the process killed for memory, 2**62 gave a MemoryError; a negative one
    would be stored from the end of the list. Each is refused with the usual readable error instead."""
    c = tampered(city_dir, tmp_path, ("UPDATE stops SET stop_idx = ? WHERE stop_idx = (SELECT MAX(stop_idx) FROM stops)", (idx,)))
    with pytest.raises(ValueError, match="timetable is malformed \\(stop indexes run from .* stops\\); rebuild"):
        c.tt


def test_stop_indexes_with_gaps_still_load(city_dir, tmp_path):
    """The national layer numbers its stops with gaps (largest index 1.12x the count): a few gaps are fine."""
    n = sqlite3.connect(city_dir / "testowo.sqlite").execute("SELECT COUNT(*) FROM stops").fetchone()[0]
    c = tampered(city_dir, tmp_path, ("UPDATE stops SET stop_idx = ? WHERE stop_idx = 1", (2 * n,)),
                 ("UPDATE transfers SET from_stop = ? WHERE from_stop = 1", (2 * n,)),
                 ("UPDATE transfers SET to_stop = ? WHERE to_stop = 1", (2 * n,)))
    db = sqlite3.connect(tmp_path / "testowo.sqlite")
    for idx, blob in db.execute("SELECT pattern_idx, stops FROM patterns").fetchall():
        s = np.frombuffer(blob, "<i4").copy()
        s[s == 1] = 2 * n
        db.execute("UPDATE patterns SET stops = ? WHERE pattern_idx = ?", (s.astype("<i4").tobytes(), idx))
    db.commit()
    db.close()
    assert c.tt.n_stops == 2 * n + 1 and c.tt.stops[2 * n] is not None


@pytest.mark.parametrize("value", [-1, 10_000])
def test_a_pattern_naming_a_missing_stop_is_refused(city_dir, tmp_path, value):
    db = sqlite3.connect(city_dir / "testowo.sqlite")
    idx, blob = db.execute("SELECT pattern_idx, stops FROM patterns LIMIT 1").fetchone()
    db.close()
    s = np.frombuffer(blob, "<i4").copy()
    s[0] = value
    c = tampered(city_dir, tmp_path, ("UPDATE patterns SET stops = ? WHERE pattern_idx = ?", (s.astype("<i4").tobytes(), idx)))
    with pytest.raises(ValueError, match="timetable is malformed \\(1 stop patterns name stops the file does not have\\)"):
        c.tt


@pytest.mark.parametrize("column", ["from_stop", "to_stop"])
def test_a_transfer_naming_a_missing_stop_is_refused(city_dir, tmp_path, column):
    c = tampered(city_dir, tmp_path, (f"UPDATE transfers SET {column} = -3 WHERE {column} = (SELECT MIN({column}) FROM transfers)", ()))
    with pytest.raises(ValueError, match="timetable is malformed \\(a foot transfer .* the file does not have\\)"):
        c.tt
