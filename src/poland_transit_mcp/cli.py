"""Command line.

  poland-transit-mcp [--cities gdansk,krakow|all] [--memory-budget MB] [--data-dir DIR] [--http PORT] [--live gdansk] [--no-download]
  poland-transit-mcp download gdansk,krakow [--data-dir DIR] [--from URL]
  poland-transit-mcp build gdansk [--download] [--skip-gtfs] [--data-dir DIR]
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import memory  # noqa: F401  (sets how large freed allocations go back to the OS, before any are made)
from .city import City
from .config import CITIES

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


def download(ids: list[str], directory: Path, base: str | None = None) -> None:
    """Fetches prebuilt <city>.sqlite.gz files (GitHub Releases by default) and unpacks them."""
    base = base or os.environ.get("POLAND_TRANSIT_DATA_URL") or DEFAULT_DATA_URL
    for cid in ids:
        url = f"{base}/{cid}.sqlite.gz"
        log(f"downloading {url}")
        part = directory / f"{cid}.sqlite.part"
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310
            directory.mkdir(parents=True, exist_ok=True)  # only once the server has answered: a failed fetch leaves nothing behind
            with gzip.GzipFile(fileobj=r) as gz, open(part, "wb") as out:
                while chunk := gz.read(1 << 20):
                    out.write(chunk)
        part.replace(directory / f"{cid}.sqlite")
        log(f"saved {directory / f'{cid}.sqlite'}")


def serve(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(
        prog="poland-transit-mcp", description="MCP server for Polish cities (stdio by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="subcommands:\n"
               "  build CITY [--download]     build ./data/cities/CITY.sqlite from OSM and GTFS (see build --help);\n"
               "                              outside a clone, then start with --data-dir ./data/cities\n"
               "  download CITY[,CITY...]     fetch prebuilt city files (not published yet: use build)")
    ap.add_argument("--cities", default=os.environ.get("POLAND_TRANSIT_CITIES"), help="comma-separated city ids (default: all files present)")
    ap.add_argument("--data-dir", help="where <city>.sqlite files live")
    ap.add_argument("--http", type=int, metavar="PORT", help="serve Streamable HTTP on 127.0.0.1:PORT/mcp instead of stdio")
    ap.add_argument("--live", default=os.environ.get("POLAND_TRANSIT_LIVE", ""), help="opt in to live departures, e.g. gdansk")
    ap.add_argument("--no-download", action="store_true", help="never fetch missing city files")
    ap.add_argument("--memory-budget", type=float, metavar="MB",
                    default=float(os.environ.get("POLAND_TRANSIT_MEMORY_MB") or 480),
                    help="memory target for the whole server process (default 480); 230 MB of it (150 with "
                         "POLAND_TRANSIT_MMAP_THRESHOLD set on Linux, which returns large frees to the OS at once) "
                         "is set aside for Python and one query, "
                         "and least recently used cities are unloaded to keep within the rest (they reload on "
                         "their next query)")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    directory = data_dir(a.data_dir)
    if a.cities == "all":
        wanted = sorted(CITIES)
    elif a.cities:
        wanted = [c.strip() for c in a.cities.split(",") if c.strip()]
    else:
        wanted = sorted(p.stem for p in directory.glob("*.sqlite")) if directory.is_dir() else []
    missing = [c for c in wanted if not (directory / f"{c}.sqlite").exists()]
    if missing and not a.no_download:
        try:
            download(missing, directory)
        except Exception as e:  # noqa: BLE001 - report and carry on with what we have
            log(f"could not download {', '.join(missing)}: {e}")
    cities: dict[str, City] = {}
    for cid in wanted:
        try:
            cities[cid] = City.open(directory, cid)
        except FileNotFoundError as e:
            log(f"skipping {cid}: {e}")
    if not cities:
        log(f"no city data found in {directory}. Build it: poland-transit-mcp build gdansk --download, then start "
            f"with --data-dir ./data/cities if you are not in a clone (prebuilt downloads are not published yet)")
        sys.exit(1)
    from .live import enable_live
    from .server import create_server
    enable_live([c for c in a.live.split(",") if c.strip() in cities])
    from .pool import CityPool
    from .timeutil import now_local
    pool = CityPool(cities, a.memory_budget)
    server = create_server(pool)
    from . import engine, telemetry
    log(telemetry.setup(pool, engine.NAME))
    keep_all = pool.budget_to_keep_all_mb()
    if keep_all > a.memory_budget:
        log(f"memory: these cities need about {keep_all} MB to stay loaded together; with {a.memory_budget:g} MB the "
            f"least recently used is unloaded and reloads on its next query (1-4 s). To keep them all: --memory-budget {keep_all}")
    log(f"ready: {', '.join(cities)} from {directory} in {round((time.perf_counter() - t0) * 1000)} ms "
        f"(memory budget {a.memory_budget:g} MB, search engine: {engine.NAME})")
    if not os.environ.get("POLAND_TRANSIT_NO_WARMUP"):
        threading.Thread(target=pool.warm, args=(list(cities), now_local().date), daemon=True).start()
    if a.http:
        log(f"Streamable HTTP on http://127.0.0.1:{a.http}/mcp")
        server.run("streamable-http", host="127.0.0.1", port=a.http, stateless_http=True, json_response=True)
    else:
        server.run()


def main(argv: list[str] | None = None) -> None:
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
            log(f"could not download {a.cities}: {e}")
            sys.exit(1)
    elif argv and argv[0] == "build":
        from .build.build_city import main as build_main
        build_main(argv[1:])
    else:
        serve(argv)


if __name__ == "__main__":
    main()
