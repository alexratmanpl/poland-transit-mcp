import pytest

from poland_transit_mcp.build.gtfs import Source
from poland_transit_mcp.geo import decode_parts, encode_parts, haversine, point_in_rings, simplify
from poland_transit_mcp.text import fold, tokens
from poland_transit_mcp.timeutil import LocalTime, add_days, fmt_wall, parse_gtfs_time, parse_when, weekday


def test_fold_removes_polish_diacritics():
    assert fold("Wrocław Główny") == "wroclaw glowny"
    assert fold("ŻABI KRUK – Łąkowa") == "zabi kruk lakowa"
    assert tokens("  Długi  Targ ") == ["dlugi", "targ"]


def test_time_parsing_and_night_services():
    now = LocalTime(20261116, 10 * 3600)
    assert parse_when("23:45", now) == LocalTime(20261116, 23 * 3600 + 45 * 60)
    assert parse_when("2026-11-20 07:05", now) == LocalTime(20261120, 7 * 3600 + 5 * 60)
    assert parse_when("2026-11-20T06:05:00Z", now) == LocalTime(20261120, 7 * 3600 + 5 * 60)  # CET = UTC+1
    assert parse_when("2026-07-20T06:05:00Z", now) == LocalTime(20260720, 8 * 3600 + 5 * 60)  # CEST = UTC+2
    assert parse_when("25:10", now) == LocalTime(20261116, 25 * 3600 + 600)  # the night after the day
    assert parse_when(None, now) == now
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 600
    assert fmt_wall(20261231, 25 * 3600 + 600) == ("2027-01-01", "01:10")
    assert add_days(20260228, 1) == 20260301
    assert weekday(20261116) == 0 and weekday(20261115) == 6


def test_geometry_helpers():
    line = [(54.0, 18.0), (54.00001, 18.0005), (54.0, 18.001)]
    assert len(simplify(line, 5)) == 2 and len(simplify(line, 0.1)) == 3
    parts = decode_parts(encode_parts([line, [(1, 2), (3, 4)]]))
    assert parts[1] == [(1, 2), (3, 4)]
    sq = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
    hole = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4), (0.4, 0.4)]
    assert point_in_rings(0.2, 0.2, [sq, hole]) and not point_in_rings(0.5, 0.5, [sq, hole])
    assert abs(haversine(54, 18, 54.001, 18) - 111.2) < 0.5


def test_csv_bom_crlf_and_quotes(tmp_path):
    (tmp_path / "x.txt").write_bytes('﻿a,b,c\r\n1,"x, ""y""",z\r\n2,,"multi\nline"\r\n'.encode())
    with Source(tmp_path).rows("x.txt") as rows:
        out = list(rows)
    assert out[0] == {"a": "1", "b": 'x, "y"', "c": "z"}
    assert out[1]["c"] == "multi\nline"
    with Source(tmp_path).rows("missing.txt") as rows:
        assert list(rows) == []


def test_build_fails_fast_without_osmium(monkeypatch):
    import shutil

    import pytest

    from poland_transit_mcp.build import build_city
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="brew install osmium-tool"):
        build_city.main(["gdansk"])


def test_fold_treats_polish_abbreviations_alike():
    assert fold("Plac Grunwaldzki") == fold("PL. GRUNWALDZKI") == "pl grunwaldzki"
    assert fold("Aleja Róż") == fold("al. Róż")
    assert fold("Osiedle Piastów") == fold("Os. Piastów")


def test_data_dir_ignores_working_directory(tmp_path, monkeypatch):
    """A server started inside some other repository must not load that repository's data/cities."""
    from poland_transit_mcp import cli
    (tmp_path / "data" / "cities").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POLAND_TRANSIT_DATA", raising=False)
    assert cli.data_dir(None) != tmp_path / "data" / "cities"
    assert cli.data_dir(str(tmp_path)) == tmp_path.resolve()  # an explicit directory is still honoured


def test_failed_download_gives_one_line_and_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    """An unpublished release answers 404: the command must say so in one line, exit 1, and not create the directory."""
    import urllib.error

    from poland_transit_mcp import cli, net

    def refuse(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(net, "_open", refuse)
    target = tmp_path / "cities"
    with pytest.raises(SystemExit) as e:
        cli.main(["download", "gdansk", "--data-dir", str(target)])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "could not download" in err and "404" in err
    assert not target.exists()


def test_a_download_writes_a_new_temporary_file_and_removes_a_broken_one(tmp_path, monkeypatch):
    """The unpacked file goes to a new file of its own (a planted link at a predictable name is never written
    through), replaces the target only when complete, and a failed unpack leaves nothing behind."""
    import gzip
    import io
    import os

    from poland_transit_mcp import cli, net
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (tmp_path / "gdansk.sqlite.part").symlink_to(victim)  # where the old code wrote
    bodies = {"gdansk": gzip.compress(b"city bytes"), "krakow": gzip.compress(bytes(range(256)) * 400)[:500]}
    monkeypatch.setattr(net, "_open", lambda url, timeout: io.BytesIO(bodies[url.rsplit("/", 1)[1].split(".")[0]]))
    cli.download(["gdansk"], tmp_path, "https://example.invalid")
    assert (tmp_path / "gdansk.sqlite").read_bytes() == b"city bytes" and victim.read_text() == "keep me"
    mask = os.umask(0)
    os.umask(mask)
    assert (tmp_path / "gdansk.sqlite").stat().st_mode & 0o777 == 0o666 & ~mask  # readable as before, not 0600
    with pytest.raises(EOFError):  # a cut-off file
        cli.download(["krakow"], tmp_path, "https://example.invalid")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gdansk.sqlite", "gdansk.sqlite.part", "victim.txt"]


@pytest.mark.parametrize("args, env, host, warned", [
    ([], None, "127.0.0.1", False),
    (["--host", "0.0.0.0"], None, "0.0.0.0", True),
    ([], "0.0.0.0", "0.0.0.0", True),
    (["--host", "::1"], None, "::1", False),
    (["--host", "[::1]"], None, "::1", False),
    (["--host", "127.0.0.1"], "0.0.0.0", "127.0.0.1", False),  # the flag wins over the variable
    (["--host", "127.0.0.2"], None, "127.0.0.2", False),  # every loopback address is loopback
    (["--host", "127.1"], None, "127.1", False),
    (["--host", "localhost"], None, "localhost", False),
    (["--host", "192.168.1.5"], None, "192.168.1.5", True),
])
def test_http_binds_loopback_unless_told_otherwise(tmp_path, monkeypatch, capsys, args, env, host, warned):
    from fixture import build_fixture

    from poland_transit_mcp import cli, server, web
    runs = []
    monkeypatch.setattr(web, "run", lambda srv, host, port, max_connections: runs.append((host, port, max_connections)))
    limits = []
    monkeypatch.setattr(server, "create_server", lambda pool, calls: limits.append(calls) or object())
    monkeypatch.setenv("POLAND_TRANSIT_NO_WARMUP", "1")
    monkeypatch.delenv("POLAND_TRANSIT_MAX_CONNECTIONS", raising=False)
    if env:
        monkeypatch.setenv("POLAND_TRANSIT_HOST", env)
    else:
        monkeypatch.delenv("POLAND_TRANSIT_HOST", raising=False)
    build_fixture(tmp_path)
    cli.main(["--data-dir", str(tmp_path), "--cities", "testowo", "--no-download", "--http", "8080", *args])
    assert runs == [(host, 8080, web.DEFAULT_MAX_CONNECTIONS)]
    assert ("no authentication" in capsys.readouterr().err) is warned
    assert [(c.max_calls, c.wait_s) for c in limits] == [(6, 5)]  # the limit on calls at once (default)


def test_no_connection_cap_by_default_and_one_when_asked(tmp_path, monkeypatch):
    """A cap counts idle connections too (a few silent sockets would turn every call away with 503), so none is set
    unless the operator asks for one."""
    from fixture import build_fixture

    from poland_transit_mcp import cli, server, web
    assert web.DEFAULT_MAX_CONNECTIONS is None
    runs = []
    monkeypatch.setattr(web, "run", lambda srv, host, port, max_connections: runs.append(max_connections))
    monkeypatch.setattr(server, "create_server", lambda pool, calls: object())
    monkeypatch.setenv("POLAND_TRANSIT_NO_WARMUP", "1")
    monkeypatch.delenv("POLAND_TRANSIT_MAX_CONNECTIONS", raising=False)
    monkeypatch.delenv("POLAND_TRANSIT_HOST", raising=False)
    build_fixture(tmp_path)
    base = ["--data-dir", str(tmp_path), "--cities", "testowo", "--no-download", "--http", "8080"]
    cli.main(base)
    cli.main([*base, "--max-connections", "500"])
    monkeypatch.setenv("POLAND_TRANSIT_MAX_CONNECTIONS", "300")
    cli.main(base)
    assert runs == [None, 500, 300]


def test_one_unreadable_city_file_is_skipped(tmp_path, monkeypatch, capsys):
    """A damaged or foreign *.sqlite in the data dir is named and skipped; the other cities still start."""
    import sqlite3

    from fixture import build_fixture

    from poland_transit_mcp import cli, web
    monkeypatch.setattr(web, "run", lambda *a, **k: None)
    monkeypatch.setenv("POLAND_TRANSIT_NO_WARMUP", "1")
    build_fixture(tmp_path)
    (tmp_path / "broken.sqlite").write_bytes(b"not a database at all" * 100)
    other = sqlite3.connect(tmp_path / "foreign.sqlite")
    other.execute("CREATE TABLE meta (key TEXT, value TEXT)")  # a database, but no city's metadata
    other.commit()
    other.close()
    wal = sqlite3.connect(tmp_path / "journalled.sqlite")  # its header keeps WAL mode after it is closed
    wal.execute("PRAGMA journal_mode = WAL")
    wal.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    wal.commit()
    wal.close()
    started = []
    monkeypatch.setattr("poland_transit_mcp.server.create_server",
                        lambda pool, calls: started.append(sorted(pool)) or object())
    cli.main(["--data-dir", str(tmp_path), "--no-download", "--http", "8080"])
    err = capsys.readouterr().err
    assert started == [["testowo"]]
    assert "skipping broken" in err and "skipping foreign" in err and "not a readable city file" in err, err
    assert "skipping journalled: journalled.sqlite is in WAL mode" in err, err


def test_the_host_variable_does_not_stop_stdio(tmp_path, monkeypatch):
    from fixture import build_fixture

    from poland_transit_mcp import cli, server
    runs = []

    class Fake:
        def run(self, *a, **kw):
            runs.append((a, kw))
    monkeypatch.setattr(server, "create_server", lambda pool, calls: Fake())
    monkeypatch.setenv("POLAND_TRANSIT_NO_WARMUP", "1")
    monkeypatch.setenv("POLAND_TRANSIT_HOST", "0.0.0.0")
    build_fixture(tmp_path)
    cli.main(["--data-dir", str(tmp_path), "--cities", "testowo", "--no-download"])
    assert runs == [((), {})]  # stdio


def test_host_without_http_is_refused(tmp_path, capsys):
    from poland_transit_mcp import cli
    with pytest.raises(SystemExit):
        cli.main(["--data-dir", str(tmp_path), "--host", "0.0.0.0"])
    assert "--host needs --http" in capsys.readouterr().err


def test_clip_line_keeps_a_segment_that_crosses_the_box_with_both_ends_outside():
    from poland_transit_mcp.geo import clip_line
    assert clip_line([(0, 0), (2, 2)], (0.5, 0.5, 1.5, 1.5)) == [[(0, 0), (2, 2)]]
    assert clip_line([(0, 0), (0, 2)], (0.5, 0.5, 1.5, 1.5)) == []  # passes beside it
    # inside, out, and back: two runs, each reaching one point beyond the edge
    assert clip_line([(1, 1), (1, 3), (1, 4), (1, 1.2)], (0.5, 0.5, 1.5, 1.5)) == [[(1, 1), (1, 3)], [(1, 4), (1, 1.2)]]
