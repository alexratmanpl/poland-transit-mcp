"""Command line.

  poland-transit-mcp [--cities gdansk,krakow|all] [--memory-budget MB] [--days-kept N] [--warm-days N] [--warm-from N]
                     [--data-dir DIR] [--http PORT [--host ADDR] [--max-connections N]] [--live gdansk] [--no-download]
  poland-transit-mcp download gdansk,krakow [--data-dir DIR] [--from URL]
  poland-transit-mcp build gdansk [--download] [--skip-gtfs] [--force] [--data-dir DIR]
  poland-transit-mcp credits [--cities gdansk,poland] [--data-dir DIR] > credits.json
"""
from __future__ import annotations

import argparse
import gzip
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import (
    memory,  # sets how large freed allocations go back to the OS, before any are made
    net,
    web,
)
from .city import City
from .config import CITIES
from .web import DEFAULT_MAX_CONNECTIONS, KEEP_ALIVE_S

DEFAULT_DATA_URL = "https://github.com/alexratmanpl/poland-transit-mcp/releases/download/data-latest"


def log(*a) -> None:
    print("[poland-transit-mcp]", *a, file=sys.stderr, flush=True)


def data_dir(explicit: str | None) -> Path:
    if explicit or os.environ.get("POLAND_TRANSIT_DATA"):
        return Path(explicit or os.environ["POLAND_TRANSIT_DATA"]).expanduser().resolve()
    # data/cities of a source checkout only. Never the caller's working directory: a client that starts the
    # server inside some other repository would otherwise load that repository's database files.
    root = Path(__file__).resolve().parents[2]
    local = root / "data" / "cities"
    if (root / "pyproject.toml").is_file() and local.is_dir():
        return local
    return Path.home() / ".cache" / "poland-transit-mcp"


def at_least(lowest: int):
    """An argparse type: an integer, `lowest` or more."""
    def parse(text: str) -> int:
        try:
            n = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
        if n < lowest:
            raise argparse.ArgumentTypeError(f"{n} is less than {lowest}")
        return n
    return parse


DOWNLOAD_DEADLINE_S = 1800  # a whole city file, however slowly the server sends it


def download(ids: list[str], directory: Path, base: str | None = None) -> None:
    """Fetches prebuilt <city>.sqlite.gz files (GitHub Releases by default) and unpacks them. Each one is unpacked
    into a new temporary file of its own beside the target (created exclusively, so a planted file or link there is
    never written through), which replaces the target only when it is complete and is deleted when it is not."""
    base = base or os.environ.get("POLAND_TRANSIT_DATA_URL") or DEFAULT_DATA_URL
    for cid in ids:
        url = f"{base}/{cid}.sqlite.gz"
        log(f"downloading {url}")
        with net.urlopen(url, timeout=120, deadline=DOWNLOAD_DEADLINE_S) as r:
            directory.mkdir(parents=True, exist_ok=True)  # only once the server has answered: a failed fetch leaves nothing behind
            # not named *.sqlite: a leftover is never loaded as a city
            fd, part = tempfile.mkstemp(dir=directory, prefix=f".{cid}.", suffix=".part")
            try:
                mask = os.umask(0)
                os.umask(mask)
                os.chmod(part, 0o666 & ~mask)  # as a plainly created file: mkstemp makes it 0600
                with gzip.GzipFile(fileobj=r) as gz, os.fdopen(fd, "wb") as out:
                    while chunk := gz.read(1 << 20):
                        out.write(chunk)
                os.replace(part, directory / f"{cid}.sqlite")
            except BaseException:
                Path(part).unlink(missing_ok=True)
                raise
        log(f"saved {directory / f'{cid}.sqlite'}")


def serve(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="poland-transit-mcp", description="MCP server for Polish cities (stdio by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="subcommands:\n"
               "  build CITY [--download]     build ./data/cities/CITY.sqlite from OSM and GTFS (see build --help);\n"
               "                              outside a clone, then start with --data-dir ./data/cities\n"
               "  download CITY[,CITY...]     fetch prebuilt city files (not published yet: use build)\n"
               "  credits [--cities ...]      print the full credits of the city files as JSON")
    ap.add_argument("--cities", default=os.environ.get("POLAND_TRANSIT_CITIES"), help="comma-separated city ids (default: all files present)")
    ap.add_argument("--data-dir", help="where <city>.sqlite files live")
    ap.add_argument("--http", type=int, metavar="PORT", help="serve Streamable HTTP on HOST:PORT/mcp instead of stdio")
    ap.add_argument("--host", default=None,
                    help="address --http binds to (default $POLAND_TRANSIT_HOST, else 127.0.0.1). There is no "
                         "authentication: bind another address, e.g. 0.0.0.0 in a container, only on a network that "
                         "nothing untrusted can reach")
    ap.add_argument("--max-connections", type=at_least(1), metavar="N",
                    default=os.environ.get("POLAND_TRANSIT_MAX_CONNECTIONS") or DEFAULT_MAX_CONNECTIONS,
                    help="--http: a cap on open connections (default: none). It counts idle connections too, so "
                         "past it every request is answered 503; put a shared server behind a gateway instead "
                         f"(SECURITY.md). A connection idle for {KEEP_ALIVE_S} s after a reply is closed")
    ap.add_argument("--live", default=os.environ.get("POLAND_TRANSIT_LIVE", ""), help="opt in to live departures, e.g. gdansk")
    ap.add_argument("--no-download", action="store_true", help="never fetch missing city files")
    ap.add_argument("--memory-budget", type=float, metavar="MB",
                    default=float(os.environ.get("POLAND_TRANSIT_MEMORY_MB") or 480),
                    help="memory target for the whole server process (default 480); 230 MB of it (150 with "
                         "POLAND_TRANSIT_MMAP_THRESHOLD set on Linux, which returns large frees to the OS at once) "
                         "is set aside for Python and one query, "
                         "and least recently used cities are unloaded to keep within the rest (they reload on "
                         "their next query)")
    # string defaults go through `type` too, so a bad variable is refused like a bad flag
    ap.add_argument("--days-kept", type=at_least(1), metavar="N", default=os.environ.get("POLAND_TRANSIT_DAYS_KEPT") or "2",
                    help="dates each city keeps built in memory (default 2); over the memory budget, cached dates "
                         "of unused cities are dropped, down to one each, before whole cities are unloaded")
    ap.add_argument("--warm-days", type=at_least(1), metavar="N", default=os.environ.get("POLAND_TRANSIT_WARM_DAYS") or "1",
                    help="dates the start-up warm-up builds for each city (default 1, at most --days-kept), within "
                         "70%% of the memory budget; POLAND_TRANSIT_NO_WARMUP=1 turns the warm-up off")
    ap.add_argument("--warm-from", type=at_least(0), metavar="N", default=os.environ.get("POLAND_TRANSIT_WARM_FROM") or "0",
                    help="days after today the warm-up starts (default 0, today; 1 warms from tomorrow)")
    ap.add_argument("--max-calls", type=at_least(1), metavar="N", default=os.environ.get("POLAND_TRANSIT_MAX_CALLS") or "6",
                    help="tool calls answered at once (default 6); memory grows with each call in flight, so this keeps "
                         "a server shared by several clients near its memory budget")
    ap.add_argument("--call-wait", type=at_least(0), metavar="SECONDS", default=os.environ.get("POLAND_TRANSIT_CALL_WAIT") or "5",
                    help="how long a call waits for a free place among --max-calls before it is refused as busy "
                         "(default 5; keep it below the client's own timeout)")
    a = ap.parse_args(argv)
    if a.host and not a.http:  # the variable alone may be set in an image that is also run over stdio
        ap.error("--host needs --http")
    a.host = (a.host or os.environ.get("POLAND_TRANSIT_HOST") or "127.0.0.1").strip("[]")
    t0 = time.perf_counter()
    directory = data_dir(a.data_dir)
    if a.cities == "all":  # every built-in data set, and every file present (e.g. built from a definitions file)
        wanted = sorted(set(CITIES) | ({p.stem for p in directory.glob("*.sqlite")} if directory.is_dir() else set()))
    elif a.cities:
        wanted = [c.strip() for c in a.cities.split(",") if c.strip()]
    else:
        wanted = sorted(p.stem for p in directory.glob("*.sqlite")) if directory.is_dir() else []
    missing = [c for c in wanted if not (directory / f"{c}.sqlite").exists()]
    if missing and not a.no_download:
        try:
            download(missing, directory)
        except Exception as e:  # noqa: BLE001 - report and carry on with what we have
            log(f"could not download {', '.join(missing)}: {e}{net.tls_note(e)}")
    cities: dict[str, City] = {}
    for cid in wanted:
        try:
            cities[cid] = City.open(directory, cid)
        except (FileNotFoundError, ValueError) as e:
            log(f"skipping {cid}: {e}")
        except (sqlite3.Error, KeyError, TypeError) as e:  # not a city file, or a damaged one: the others still load
            log(f"skipping {cid}: {directory / f'{cid}.sqlite'} is not a readable city file "
                f"({type(e).__name__}: {e})")
    if not cities:
        log(f"no city data found in {directory}. Build it: poland-transit-mcp build gdansk --download, then start "
            f"with --data-dir ./data/cities if you are not in a clone (prebuilt downloads are not published yet)")
        sys.exit(1)
    from .live import enable_live
    from .server import CallLimit, create_server
    enable_live([c for c in a.live.split(",") if c.strip() in cities])
    from .pool import CityPool
    from .timeutil import add_days, now_local
    pool = CityPool(cities, a.memory_budget, days_kept=a.days_kept)
    server = create_server(pool, calls=CallLimit(a.max_calls, a.call_wait))
    from . import engine, telemetry
    log(telemetry.setup(pool, engine.NAME))
    keep_all = pool.budget_to_keep_all_mb()
    if keep_all > a.memory_budget:
        log(f"memory: these cities need about {keep_all} MB to stay loaded together with {a.days_kept} dates each; with "
            f"{a.memory_budget:g} MB, cached dates are dropped first, then the least recently used city is unloaded and "
            f"reloads on its next query (1-4 s). To keep them all: --memory-budget {keep_all}")
    from .server import picture_warning
    if picture_warning():
        log(f"WARNING: {picture_warning()}")
    log(f"ready: {', '.join(cities)} from {directory} in {round((time.perf_counter() - t0) * 1000)} ms "
        f"(memory budget {a.memory_budget:g} MB, at most {a.max_calls} calls at once, search engine: {engine.NAME})")
    if not os.environ.get("POLAND_TRANSIT_NO_WARMUP"):
        threading.Thread(target=pool.warm, args=(list(cities), add_days(now_local().date, a.warm_from)),
                         kwargs={"days": a.warm_days}, daemon=True).start()
    if a.http:
        shown = f"[{a.host}]" if ":" in a.host else a.host
        log(f"Streamable HTTP on http://{shown}:{a.http}/mcp")
        if not web.is_loopback(a.host):
            log(f"WARNING: bound to {a.host}, not loopback, and there is no authentication: anyone who can reach this "
                "address can call the tools. The DNS-rebinding check (Host and Origin headers) is on for loopback "
                "addresses only")
        web.run(server, a.host, a.http, max_connections=a.max_connections)
    else:
        server.run()


def main(argv: list[str] | None = None) -> None:
    if argv is None and not (sys.argv[1:2] and sys.argv[1] in ("download", "build", "credits")):
        memory.restart_returning_freed_memory()  # macOS: the server's freed memory goes back to the OS
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "download":
        ap = argparse.ArgumentParser(prog="poland-transit-mcp download")
        ap.add_argument("cities", nargs="?", default=",".join(CITIES))
        ap.add_argument("--data-dir")
        ap.add_argument("--from", dest="base")
        a = ap.parse_args(argv[1:])
        try:
            download([c for c in a.cities.split(",") if c], data_dir(a.data_dir), a.base)
        except Exception as e:  # noqa: BLE001 - one readable line instead of a traceback (an unpublished release answers 404)
            log(f"could not download {a.cities}: {e}{net.tls_note(e)}")
            sys.exit(1)
    elif argv and argv[0] == "build":
        from .build.build_city import main as build_main
        build_main(argv[1:])
    elif argv and argv[0] == "credits":  # the full credits of the data files, to publish next to them
        import json

        from .credits import credit_set
        ap = argparse.ArgumentParser(prog="poland-transit-mcp credits",
                                     description="print the full credits of the city files as JSON")
        ap.add_argument("--cities", help="comma-separated city ids (default: every file in the data dir)")
        ap.add_argument("--data-dir")
        a = ap.parse_args(argv[1:])
        directory = data_dir(a.data_dir)
        ids = [c for c in (a.cities or "").split(",") if c] or sorted(p.stem for p in directory.glob("*.sqlite"))
        if not ids:
            log(f"no city data found in {directory}")
            sys.exit(1)
        print(json.dumps(credit_set([City.open(directory, c) for c in ids]), ensure_ascii=False, indent=1))
    else:
        serve(argv)


if __name__ == "__main__":
    main()
