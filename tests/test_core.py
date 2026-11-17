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
    assert parse_when("soon", now) is None
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
    import urllib.request

    from poland_transit_mcp import cli

    def refuse(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    target = tmp_path / "cities"
    with pytest.raises(SystemExit) as e:
        cli.main(["download", "gdansk", "--data-dir", str(target)])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "could not download" in err and "404" in err
    assert not target.exists()
