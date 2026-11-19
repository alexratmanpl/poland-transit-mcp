"""Build safeguards (build/safeguard.py): a worse rebuild never replaces the last good city file."""
import json
import sqlite3
from datetime import date, timedelta

import pytest

from poland_transit_mcp import net
from poland_transit_mcp.build import build_city, safeguard
from poland_transit_mcp.build.safeguard import (
    EXIT_REJECTED,
    expiry_warnings,
    read_feeds,
    worse_feeds,
)


def feed(key="T", status="ok", runs=100, end_date=20261231, name="Test GTFS"):
    f = {"key": key, "name": name, "status": status, "end_date": end_date}
    if runs is not None:
        f["runs"] = runs
    return f


def city_file(path, feeds, marker="old"):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [("feeds", json.dumps(feeds)), ("marker", marker)])
    db.commit()
    db.close()


def open_new(tmp, feeds):
    """A new city file as the build leaves it before finish(): open, inside a transaction."""
    db = sqlite3.connect(tmp, isolation_level=None)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("BEGIN")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [("feeds", json.dumps(feeds)), ("marker", "new")])
    return db


def marker(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT value FROM meta WHERE key = 'marker'").fetchone()[0]
    finally:
        db.close()


TODAY = date(2026, 11, 17)


def test_worse_feeds():
    old = [feed("A"), feed("B"), feed("C", status="missing"), feed("D", runs=None)]
    assert worse_feeds(None, [feed("A", status="missing")], TODAY) == []  # a first build is never refused
    assert worse_feeds(old, [feed("A"), feed("B", runs=60), feed("C", status="missing"), feed("D", runs=1)],
                       TODAY) == []
    found = worse_feeds(old, [feed("A", status="missing"), feed("B", runs=49), feed("E", status="broken")], TODAY)
    assert found == ["Test GTFS (A): was ok in the current file, now missing",
                     "Test GTFS (B): 49 runs, down from 100 in the current file (below 50%): the feed looks gutted"]
    assert worse_feeds(old, [feed("A", status="skipped")], TODAY) == [
        "Test GTFS (A): was ok in the current file, now skipped"]  # --skip-gtfs needs --force too


def test_an_ended_feed_is_not_protected():
    old = [feed("A", end_date=20261116)]
    assert worse_feeds(old, [feed("A", status="missing")], TODAY) == []


def test_expiry_warnings():
    today = TODAY
    feeds = [feed("A", end_date=20261216), feed("B", end_date=20261130), feed("C", end_date=20261116),
             feed("D", status="missing")]
    assert expiry_warnings(feeds, today) == [
        "Test GTFS (B): ends on 2026-11-30, in 13 days; rebuild before then",
        "Test GTFS (C): ended on 2026-11-16; rebuild with a newer feed",
        "Test GTFS (D): missing, so this file has no timetable from it"]


def test_a_failed_download_keeps_the_last_good_file(tmp_path):
    """The feed's download failed, so the rebuild has no timetable from it: the old file stays."""
    out, tmp = tmp_path / "city.sqlite", tmp_path / "city.sqlite.tmp"
    city_file(out, [feed("A"), feed("B")])
    db = open_new(tmp, [feed("A"), feed("B", status="missing", runs=None)])
    with pytest.raises(SystemExit) as e:
        build_city.finish(db, tmp, out, [feed("A"), feed("B", status="missing", runs=None)])
    assert e.value.code == EXIT_REJECTED
    assert marker(out) == "old"
    assert marker(tmp_path / "city.sqlite.rejected") == "new"
    assert not tmp.exists()


def test_a_refused_build_names_failed_downloads_and_the_kept_file_s_expiry(tmp_path, monkeypatch, capsys):
    out, tmp = tmp_path / "city.sqlite", tmp_path / "city.sqlite.tmp"
    soon = int((date.today() + timedelta(days=5)).strftime("%Y%m%d"))
    city_file(out, [feed("A", end_date=soon)])
    monkeypatch.setattr(build_city, "FAILED_DOWNLOADS", ["https://example.invalid/a.zip"])
    new = [feed("A", status="missing", runs=None)]
    with pytest.raises(SystemExit):
        build_city.finish(open_new(tmp, new), tmp, out, new)
    log = capsys.readouterr().out
    assert "download failed, so this build used the previous file, if any: https://example.invalid/a.zip" in log
    assert "WARNING: kept file: Test GTFS (A): ends on" in log


def test_a_first_build_writes_and_names_a_missing_feed(tmp_path, capsys):
    out, tmp = tmp_path / "city.sqlite", tmp_path / "city.sqlite.tmp"
    new = [feed("A", status="missing", runs=None)]
    build_city.finish(open_new(tmp, new), tmp, out, new)
    assert marker(out) == "new"
    assert "WARNING: Test GTFS (A): missing, so this file has no timetable from it" in capsys.readouterr().out


def test_a_gutted_feed_keeps_the_last_good_file_unless_forced(tmp_path):
    out, tmp = tmp_path / "city.sqlite", tmp_path / "city.sqlite.tmp"
    city_file(out, [feed("A", runs=1000)])
    new = [feed("A", runs=12)]
    with pytest.raises(SystemExit):
        build_city.finish(open_new(tmp, new), tmp, out, new)
    assert marker(out) == "old"
    build_city.finish(open_new(tmp, new), tmp, out, new, force=True)
    assert marker(out) == "new"
    assert not (tmp_path / "city.sqlite.rejected").exists()  # a successful build clears an old rejected file


def test_a_good_rebuild_replaces_the_file(tmp_path):
    out, tmp = tmp_path / "city.sqlite", tmp_path / "city.sqlite.tmp"
    city_file(out, [feed("A", runs=1000)])
    new = [feed("A", runs=990)]
    build_city.finish(open_new(tmp, new), tmp, out, new)
    assert marker(out) == "new" and read_feeds(out) == new


def test_download_failure_keeps_the_previous_zip(tmp_path, monkeypatch):
    dest = tmp_path / "A.zip"
    dest.write_bytes(b"last good zip")

    def fail(*a, **k):
        raise OSError("certificate verify failed")

    monkeypatch.setattr(net, "_open", fail)
    assert build_city.download("https://example.invalid/a.zip", dest) is False
    assert dest.read_bytes() == b"last good zip"
    assert not (tmp_path / "A.zip.part").exists()


def test_import_records_runs(city_dir):
    """The feed record carries its number of stored runs, which the safeguard compares."""
    feeds = read_feeds(city_dir / "testowo.sqlite")
    assert feeds[0]["status"] == "ok" and feeds[0]["runs"] > 0


def test_read_feeds_without_a_usable_file(tmp_path):
    assert read_feeds(tmp_path / "none.sqlite") is None
    assert safeguard.read_feeds(tmp_path) is None  # a directory is not a readable city file
    bad = tmp_path / "bad.sqlite"
    city_file(bad, [])
    db = sqlite3.connect(bad)
    db.execute("UPDATE meta SET value = 'not json' WHERE key = 'feeds'")
    db.commit()
    db.close()
    assert read_feeds(bad) is None


@pytest.mark.parametrize("folder", ["we?ird#dir", "a#b", "q?mode=rw", "100% sure"])
def test_a_city_file_opens_read_only_whatever_its_path(city_dir, tmp_path, folder):
    """A `?` or `#` in the path must not end the file name in the SQLite URI: that dropped mode=ro and opened
    (or created) another file, writable."""
    import shutil

    from poland_transit_mcp.city import City
    from poland_transit_mcp.shared_db import SharedConnection
    where = tmp_path / folder
    where.mkdir()
    shutil.copy(city_dir / "testowo.sqlite", where / "testowo.sqlite")
    before = sorted(p.name for p in tmp_path.iterdir())
    db = SharedConnection.read_only(where / "testowo.sqlite")
    assert dict(db.execute("SELECT key, value FROM meta"))["city"] == "testowo"
    for change in ("UPDATE meta SET value = 'x' WHERE key = 'city'", "CREATE TABLE planted (x)"):
        with pytest.raises(sqlite3.OperationalError):
            db.execute(change)
    db.close()
    assert read_feeds(where / "testowo.sqlite")[0]["status"] == "ok"
    assert City.open(where, "testowo").id == "testowo"
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # no stray file at a cut-off name
