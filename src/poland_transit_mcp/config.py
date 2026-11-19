"""Per-city configuration of the built-in Polish data sets. Adding a city = adding an entry here (and building its
data file). Other data sets come from a definitions file instead (datasets.py, `build --datasets FILE`)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Feed:
    key: str  # short prefix used to namespace ids when several feeds are merged
    name: str
    url: str
    licence: str
    attribution: str
    # when set, `url` is a dataset page and the newest file is its first link matching this regex
    latest_link: str | None = None
    # people and organisations to credit besides the publisher, as (role, name, link). Entries from the
    # feed's own attributions.txt are added at build time.
    credits: tuple[tuple[str, str, str], ...] = ()
    # made locally (e.g. transcribed from PDFs by a script) instead of downloaded; read from raw/<city>/<key>/
    local: str | None = None
    # keep only trips that call at a stop with this stop_timezone, whole as far as their stops keep Polish time
    # (D29: e.g. a Praha - Wrocław coach whole; a stop on another clock, e.g. Vilnius, is dropped)
    stop_timezone: str | None = None
    # stop names matching this regular expression are waypoints, not stops for passengers: they are left out
    not_stops: str | None = None
    # the feed's times are in UTC (its agency_timezone); they are converted to Polish time per date at import
    utc_times: bool = False
    # store each trip's train name/number and category, and its platform and track at every stop (rail feeds)
    trip_details: bool = False
    # import shapes.txt; off for a feed whose shapes are mostly outside the area (legs are then drawn stop to stop)
    shapes: bool = True
    # operator names as shown, where the feed's agency_name is not how the operator is known
    operator_names: tuple[tuple[str, str], ...] = ()
    # shapes are simplified to this many metres (coarser for lines drawn across the country)
    shape_tolerance_m: float = 4
    # stop names may carry the platform as a trailing two-digit number ("Oliwa PKP 02"): build/gtfs.py
    # platform_splitter decides per feed (all of them when nearly every name has one, otherwise only zero-padded
    # numbers or ones shared by 3+ stops of a name, never after "nr"). Off for a feed whose names end in numbers
    # that belong to the name ("Dworcowa Street 22 and 17")
    platform_in_name: bool = True


@dataclass(frozen=True)
class CityConfig:
    id: str
    name: str
    osm_region: str  # regional extract name (Geofabrik / OSM France naming)
    bbox: tuple[float, float, float, float]  # min_lat, min_lon, max_lat, max_lon
    feeds: tuple[Feed, ...]
    live: str | None = None  # live departures provider, if the operator's terms allow it
    notes: tuple[str, ...] = field(default=())
    # the national layer: trains and coaches between cities, no map or walking graph
    national: bool = False
    # said in every trip to or from this city that uses the national layer
    intercity_notes: tuple[str, ...] = field(default=())
    # the time zone of the feeds' times; the server reads every file on Polish time, so another zone is accepted
    # only where its clock agrees with Europe/Warsaw (timeutil.same_clock), e.g. Europe/Prague
    timezone: str = "Europe/Warsaw"


OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"

# Regional OSM extracts, tried in order: Geofabrik, then the OpenStreetMap France mirror (same data).
# The mirror spells multi-word regions with "_" (kujawsko_pomorskie), Geofabrik with "-".
OSM_SOURCES = (
    "https://download.geofabrik.de/europe/poland/{region}-latest.osm.pbf",
    "https://download.openstreetmap.fr/extracts/europe/poland/{region_mirror}-latest.osm.pbf",
)


def osm_url(template: str, region: str) -> str:
    return template.format(region=region, region_mirror=region.replace("-", "_"))

_KRK = "Zarząd Transportu Publicznego w Krakowie, https://gtfs.ztp.krakow.pl"
# Mikołaj Kuranowski converts several Polish timetables to GTFS and publishes them at mkuran.pl
_MKURAN = ("GTFS conversion", "Mikołaj Kuranowski", "https://mkuran.pl/gtfs/")

CITIES: dict[str, CityConfig] = {
    "gdansk": CityConfig(
        id="gdansk",
        name="Gdańsk (with Sopot; ZTM Gdańsk network)",
        osm_region="pomorskie",
        bbox=(54.228, 18.345, 54.485, 18.945),
        feeds=(Feed(
            key="G",
            name="ZTM Gdańsk GTFS",
            url="https://ckan.multimediagdansk.pl/dataset/c24aa637-3619-4dc2-a171-a23eec8f2172/resource/"
                "30e783e4-2bec-4a7d-bb22-ee3e3b26ca96/download/gtfsgoogle.zip",
            licence="CC BY (Otwarte dane ZTM w Gdańsku)",
            attribution="Zarząd Transportu Miejskiego w Gdańsku, https://www.ztm.gda.pl/otwarty_ztm",
        ),),
        live="ztm-gdansk",
        notes=("Gdynia (ZKM Gdynia) and SKM trains are not in the ZTM Gdańsk feed and are not covered.",),
    ),
    "wroclaw": CityConfig(
        id="wroclaw",
        name="Wrocław",
        osm_region="dolnoslaskie",
        bbox=(50.975, 16.69, 51.245, 17.31),
        feeds=(Feed(
            key="W",
            name="Wrocław GTFS (Otwarte Dane Wrocław)",
            url="https://open-data.cui.wroclaw.pl/hdb/ft/6/",
            licence="CC0 1.0",
            attribution="Urząd Miejski Wrocławia – Wydział Transportu, https://open-data.cui.wroclaw.pl",
            latest_link=r'href="(/hdb/download/\d+/)"',
        ),),
    ),
    "krakow": CityConfig(
        id="krakow",
        name="Kraków",
        osm_region="malopolskie",
        bbox=(49.9, 19.58, 50.25, 20.36),
        feeds=(
            Feed("A", "ZTP Kraków GTFS – buses (MPK)", "https://gtfs.ztp.krakow.pl/GTFS_KRK_A.zip", "CC BY 4.0", _KRK),
            Feed("T", "ZTP Kraków GTFS – trams", "https://gtfs.ztp.krakow.pl/GTFS_KRK_T.zip", "CC BY 4.0", _KRK),
            Feed("M", "ZTP Kraków GTFS – buses (Mobilis)", "https://gtfs.ztp.krakow.pl/GTFS_KRK_M.zip", "CC BY 4.0", _KRK),
        ),
    ),
    "poznan": CityConfig(
        id="poznan",
        name="Poznań (with the ZTM Poznań agglomeration lines)",
        osm_region="wielkopolskie",
        bbox=(52.145, 16.45, 52.755, 17.29),
        feeds=(Feed(
            key="P",
            name="ZTM Poznań GTFS",
            url="https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile",  # always the newest file
            licence="ZTM Poznań open-data terms: free reuse naming the source, the last update and that the data "
                    "was processed; no ZTM or Poznań logos",
            attribution="Zarząd Transportu Miejskiego w Poznaniu, https://www.ztm.poznan.pl/otwarte-dane/dla-deweloperow/",
        ),),
        notes=("ZTM Poznań also publishes GTFS-RT; it is not used (see DECISIONS.md).",),
    ),
    "torun": CityConfig(
        id="torun",
        name="Toruń",
        osm_region="kujawsko-pomorskie",
        bbox=(52.92, 18.32, 53.11, 18.93),
        feeds=(Feed(
            key="T",
            name="Toruń GTFS (UM Toruń, converted by mkuran.pl)",
            url="https://mkuran.pl/gtfs/torun.zip",
            licence="CC0 1.0",
            attribution="Gmina Miasta Toruń (timetables), MZK Toruń (operator)",
            credits=(_MKURAN,),
        ),),
        notes=("The city plans a new network from 1 March 2027; line numbers will change.",),
    ),
    "katowice": CityConfig(
        id="katowice",
        name="Katowice and the GZM metropolis (ZTM network)",
        osm_region="slaskie",
        bbox=(49.97, 18.40, 50.585, 19.48),  # the extent of the network's stops, about 67 x 77 km
        feeds=(Feed(
            key="K",
            name="GZM GTFS (ZTM, converted by mkuran.pl)",
            url="https://mkuran.pl/gtfs/gzm.zip",
            licence="CC BY 4.0",
            attribution="Zarząd Transportu Metropolitalnego (ZTM GZM), "
                        "https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona",
            credits=(_MKURAN,),
        ),),
        notes=("ZTM GZM is the transport authority; the feed does not name the operating companies.",),
    ),
    "lublin": CityConfig(
        id="lublin",
        name="Lublin (ZDiTM network: bus, trolleybus)",
        osm_region="lubelskie",
        bbox=(51.08, 22.345, 51.405, 22.785),  # the extent of the network's stops, about 35 x 30 km
        feeds=(Feed(
            key="L",
            name="Lublin GTFS (ZDiTM Lublin, converted by mkuran.pl)",
            url="https://mkuran.pl/gtfs/lublin.zip",
            licence="CC0 1.0",
            attribution="Zarząd Dróg i Transportu Miejskiego w Lublinie (ZDiTM), https://www.zdtm.lublin.eu",
            credits=(_MKURAN,),
        ),),
    ),
    "zakopane": CityConfig(
        id="zakopane",
        name="Zakopane (map and walking only)",
        osm_region="malopolskie",
        bbox=(49.18, 19.80, 49.34, 20.14),  # the town, Kościelisko, Kuźnice and the road to Palenica Białczańska
        feeds=(Feed(
            key="Z",
            name="Zakopane city buses, lines 10–18 (transcribed from the city's PDF timetables)",
            url="https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy",
            licence="public sector information (Polish open data act of 2021); the town's site states no re-use conditions",
            attribution="Gmina Miasto Zakopane, https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy",
            local="python scripts/zakopane_gtfs.py",
        ),),
        notes=("Zakopane's timetables exist only as image PDFs. They were transcribed twice, independently, and the "
               "two readings agree on all 4,513 cells. The PDFs state no validity dates: the dates used are an "
               "assumption, so check the operator before travelling.",
               "Of 73 stops, 52 are placed at OpenStreetMap stops, 5 at the landmark they are named after "
               "(approximate) and 16 on the named street between the neighbouring stops (estimated).",
               "County lines and the private minibuses (e.g. to Morskie Oko) are not included. Trains and "
               "FlixBus coaches are in the national layer \"poland\" (plan_trip with to_city)."),
        intercity_notes=("The private coaches and minibuses between Kraków and Zakopane (e.g. Szwagropol, Majer) "
                         "run often but publish no open timetables, so they are not included.",),
    ),
    "warszawa": CityConfig(
        id="warszawa",
        name="Warszawa (ZTM network: bus, tram, metro, SKM)",
        osm_region="mazowieckie",
        bbox=(51.915, 20.58, 52.5, 21.47),
        feeds=(Feed(
            key="Z",
            name="Warszawa GTFS (ZTM Warszawa, converted by mkuran.pl)",
            url="https://mkuran.pl/gtfs/warsaw.zip",
            licence="public sector information re-used under ZTM Warszawa's conditions: name the source, when the data was made and obtained, and that it was processed; bus shapes ODbL 1.0",
            attribution="Zarząd Transportu Miejskiego w Warszawie, https://ztm.waw.pl",
            credits=(_MKURAN,),
        ),),
        notes=("Metro times are estimated from the published frequency (the feed gives no exact metro times).",),
    ),
    # The national layer: trains and long-distance coaches, joined to the cities at their stations
    # (intercity.py). No map and no walking graph; departures, line_info and plan_trip's to_city use it.
    "poland": CityConfig(
        id="poland",
        name="Poland: trains (13 rail operators) and FlixBus coaches",
        osm_region="",
        bbox=(49.0, 14.1, 54.9, 24.2),
        national=True,
        feeds=(
            Feed(
                key="PL",
                name="Polish Trains GTFS (PKP PLK open data, converted by mkuran.pl)",
                url="https://mkuran.pl/gtfs/polish_trains.zip",
                licence="PKP PLK open railway data terms v1.0 (credit PKP PLK; no resale of the raw data); "
                        "Koleje Mazowieckie: re-use of public sector information",
                attribution="PKP Polskie Linie Kolejowe S.A., https://www.plk-sa.pl/klienci-i-kontrahenci/api-otwarte-dane",
                credits=(_MKURAN,),
                trip_details=True,
                shape_tolerance_m=25,
            ),
            Feed(
                key="FX",
                name="FlixBus Europe GTFS (trips serving Poland)",
                # the Mobility Database's mirror of the dataset on transport.data.gouv.fr (France's access point)
                url="https://files.mobilitydatabase.org/tdg-11681/latest.zip",
                licence="ODbL 1.0",
                attribution="FlixBus, via transport.data.gouv.fr, "
                            "https://transport.data.gouv.fr/datasets/flixbus-horaires-theoriques-du-reseau-europeen-1",
                credits=(("feed mirror", "Mobility Database", "https://mobilitydatabase.org"),),
                stop_timezone="Europe/Warsaw",
                # "Heidelberg Pre Waypoint", "Puttgarden (Ferry) Waypoint", "C4T All4Trucks (Pre UK Check)"
                not_stops=r"(?i)\bway-?point\b|\bpre uk check\)",
                utc_times=True,
                shapes=False,
                operator_names=(("FlixBus-eu", "FlixBus"), ("FlixTrain-eu", "FlixTrain")),
                # names end in stand or house numbers ("Chelm, Armii Krajowej Street/KFC 05 and 06"); keep them whole
                platform_in_name=False,
            ),
        ),
        notes=("Fares are not included. Coaches other than FlixBus (e.g. the private Kraków–Zakopane buses, "
               "PKS Polonus, Neobus) publish no open timetables and are not covered.",
               "Change times between trains are an estimate (5 minutes): Polish railways publish no minimum "
               "connection times.",
               "FlixBus publishes its times in UTC; they are converted to Polish time for each date."),
    ),
}
