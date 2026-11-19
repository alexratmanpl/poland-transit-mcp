"""A feed file with an endless line or an absurd size (a zip bomb) is refused while it streams: the build reports the
feed as broken and keeps nothing of it, instead of reading one huge line into memory."""
from __future__ import annotations

import sqlite3
import zipfile

import pytest
from fixture import write_gtfs

from poland_transit_mcp.build import gtfs
from poland_transit_mcp.build.gtfs import import_feed
from poland_transit_mcp.config import Feed
from poland_transit_mcp.schema import SCHEMA

FEED = Feed("Z", "Zip feed", "https://example.invalid/z.zip", "CC0", "synthetic test data")
OTHER = Feed("T", "Good feed", "https://example.invalid/t.zip", "CC0", "synthetic test data")


def zipped(src, dest, replace: dict[str, bytes] | None = None):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.iterdir()):
            z.writestr(f.name, (replace or {}).get(f.name, f.read_bytes()))
    return dest


@pytest.fixture()
def feed_dir(tmp_path):
    write_gtfs(tmp_path / "gtfs")
    return tmp_path / "gtfs"


def counts(db, key: str) -> tuple[int, int, int]:
    return tuple(db.execute(f"SELECT COUNT(*) FROM {t} WHERE feed = ?", (key,)).fetchone()[0]
                 for t in ("stops", "routes", "calendar"))


def test_one_endless_line_is_refused_with_the_default_limit(feed_dir, tmp_path):
    """20 MB in one line (a 20 KB zip; the same at 1.2 GB used to get the build killed for memory)."""
    bomb = b"stop_id,stop_name,stop_lat,stop_lon\n" + b"a" * (20 << 20)
    path = zipped(feed_dir, tmp_path / "bomb.zip", {"stops.txt": bomb})
    assert path.stat().st_size < 100_000
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    logged = []
    meta = import_feed(db, path, FEED, log=logged.append)
    assert meta["status"] == "broken (stops.txt has a line longer than 4 MB)"
    assert any("not imported" in line for line in logged)
    assert counts(db, "Z") == (0, 0, 0)


def test_a_bomb_late_in_a_feed_leaves_nothing_of_it_and_other_feeds_whole(feed_dir, tmp_path, monkeypatch):
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    assert import_feed(db, zipped(feed_dir, tmp_path / "good.zip"), OTHER)["status"] == "ok"
    good = counts(db, "T")
    monkeypatch.setattr(gtfs, "MAX_LINE", 1000)
    bomb = (feed_dir / "stop_times.txt").read_bytes() + b"x" * 5000  # stops, routes and calendar come first
    meta = import_feed(db, zipped(feed_dir, tmp_path / "late.zip", {"stop_times.txt": bomb}), FEED)
    assert meta["status"].startswith("broken (stop_times.txt has a line longer than")
    assert counts(db, "Z") == (0, 0, 0) and counts(db, "T") == good
    assert db.execute("SELECT COUNT(*) FROM trips").fetchone()[0] > 0  # the good feed's


def test_a_file_too_large_in_all_is_refused_in_a_folder_and_by_its_stated_size(feed_dir, tmp_path, monkeypatch):
    size = (feed_dir / "stop_times.txt").stat().st_size
    monkeypatch.setattr(gtfs, "MAX_FILE", size - 1)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    for path in (feed_dir, zipped(feed_dir, tmp_path / "big.zip")):  # streamed; stated in the zip
        meta = import_feed(db, path, FEED)
        assert meta["status"] == "broken (stop_times.txt unpacks to more than 0 GB)", path
        assert counts(db, "Z") == (0, 0, 0)


def test_lines_up_to_the_limit_and_every_line_ending_are_read(feed_dir, monkeypatch):
    longest = max(len(line) for f in feed_dir.iterdir() for line in f.read_bytes().splitlines())
    monkeypatch.setattr(gtfs, "MAX_LINE", longest)
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    assert import_feed(db, feed_dir, FEED)["status"] == "ok"
