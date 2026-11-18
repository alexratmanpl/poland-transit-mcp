"""Memory the budget counts (timetable.py's estimates) and memory given back to the OS (memory.py)."""
import gc
import os
import subprocess
import sys
import tracemalloc

import pytest

from poland_transit_mcp import cli, memory
from poland_transit_mcp.city import City


def test_a_dates_estimate_covers_what_it_holds(city_dir):
    """A date's estimate (its trips and both routing directions) is at least the memory it holds, Python objects
    (tracemalloc) plus the engine's tables, and not far above it. The estimate before 2026-11-18 was 0.83 of it
    here (0.72 to 0.89 on real data), so a budget of many dates was passed by that much."""
    c = City.open(city_dir, "testowo")
    c.keep_days(5)
    tt = c.tt
    first = tt.day(20261109)  # the engine's foot transfers are built with the city's first table, counted apart
    tt.raptor(first, False)
    tracemalloc.start()
    try:
        for d in (20261110, 20261111, 20261114):
            gc.collect()
            before = tracemalloc.get_traced_memory()[0]
            day = tt.day(d)
            fwd, bwd = tt.raptor(day, False), tt.raptor(day, True)
            gc.collect()
            held = tracemalloc.get_traced_memory()[0] - before + sum(
                r.engine.nbytes() for r in (fwd, bwd) if r.engine is not None)
            assert held <= day.nbytes <= 1.25 * held, (d, day.nbytes, held)
    finally:
        tracemalloc.stop()


@pytest.fixture()
def execs(monkeypatch):
    calls = []
    monkeypatch.setattr(memory.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    monkeypatch.delenv(memory.MACOS_CACHE_VAR, raising=False)
    return calls


def test_macos_restarts_with_the_large_block_cache_off(execs, monkeypatch):
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    memory.restart_returning_freed_memory()
    assert len(execs) == 1
    path, argv, env = execs[0]
    assert (path, argv) == (sys.executable, sys.orig_argv)  # the same command line, so the same server
    assert env[memory.MACOS_CACHE_VAR] == "0"
    assert {k: v for k, v in env.items() if k != memory.MACOS_CACHE_VAR} == dict(os.environ)


@pytest.mark.parametrize("platform, preset", [("linux", None), ("win32", None), ("darwin", "0"), ("darwin", "1")])
def test_no_restart_elsewhere_or_when_the_variable_is_set(execs, monkeypatch, platform, preset):
    """A MallocLargeCache already set is left alone: 1 keeps the macOS default, and a restarted process has 0."""
    monkeypatch.setattr(memory.sys, "platform", platform)
    if preset is not None:
        monkeypatch.setenv(memory.MACOS_CACHE_VAR, preset)
    memory.restart_returning_freed_memory()
    assert execs == []


def test_a_failed_restart_carries_on_and_says_so(monkeypatch, capsys):
    def fail(*a):
        raise OSError("no such file")
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    monkeypatch.delenv(memory.MACOS_CACHE_VAR, raising=False)
    monkeypatch.setattr(memory.os, "execve", fail)
    memory.restart_returning_freed_memory()
    assert "could not restart with MallocLargeCache=0" in capsys.readouterr().err


@pytest.fixture()
def restarts(monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "restart_returning_freed_memory", lambda: calls.append(1))
    return calls


def test_only_the_server_entry_point_restarts(restarts, monkeypatch, tmp_path):
    """The real command line (main() without arguments) serving: yes. Called with arguments (tests, embedding),
    or the build, download and credits commands: no."""
    with pytest.raises(SystemExit):
        cli.main(["--data-dir", str(tmp_path), "--host", "0.0.0.0"])
    assert restarts == []
    monkeypatch.setattr(sys, "argv", ["poland-transit-mcp", "--data-dir", str(tmp_path), "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        cli.main()
    assert restarts == [1]
    from poland_transit_mcp.build import build_city
    monkeypatch.setattr(build_city, "main", lambda argv: None)
    monkeypatch.setattr(sys, "argv", ["poland-transit-mcp", "build", "gdansk"])
    cli.main()
    assert restarts == [1]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_the_restarted_process_runs_the_same_command_with_the_variable():
    env = {k: v for k, v in os.environ.items() if k != memory.MACOS_CACHE_VAR}
    code = ("import os, sys; print('run', os.environ.get('MallocLargeCache'), flush=True); "
            "from poland_transit_mcp import memory; memory.restart_returning_freed_memory()")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("\n")[:2] == ["run None", "run 0"]  # once without it, then once again with it
