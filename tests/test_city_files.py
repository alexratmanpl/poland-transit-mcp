"""A city file is read as it is: no side file beside it changes what is read."""
import shutil
import sqlite3
from pathlib import Path

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
