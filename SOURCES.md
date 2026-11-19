# Sources

Every source used to build the city files, checked on **2026-11-14** (Wrocław on 2026-11-15, Lublin on 2026-11-17). Sizes are the downloaded
files. Measured with `curl -D` headers and `ls -l`. Licences are as stated by the publisher.

## Map data: OpenStreetMap

| | |
|---|---|
| Publisher | OpenStreetMap contributors |
| Licence | ODbL 1.0, attribution "© OpenStreetMap contributors" |
| Primary URL | `https://download.geofabrik.de/europe/poland/<region>-latest.osm.pbf` |
| Fallback URL | `https://download.openstreetmap.fr/extracts/europe/poland/<region>-latest.osm.pbf` (same OSM data, minutely replicated) |
| Used for the current build | the fallback: `pomorskie` 134,409,273 B, `dolnoslaskie` 212,127,108 B, `malopolskie` 229,534,610 B; replication timestamp **2026-11-13T01:06:06Z**; md5 checked against the published `.md5` files |
| Refresh | weekly |

Quirks:
- Geofabrik first; the OSM France mirror is an automatic fallback (same data).
- The build does not use the whole-country `poland-latest.osm.pbf` (~1.9 GB). It uses the voivodeship extract that contains each city instead: same data, about 10× smaller download. It is then clipped to the city bounding box with `osmium extract -s smart`.
- The sea is mapped as coastline lines, not polygons, so maps draw the Gdańsk Bay shoreline but don't fill the sea.

## Gdańsk: ZTM Gdańsk

| | |
|---|---|
| Dataset | `tristar` on ckan.multimediagdansk.pl ("Otwarte dane ZTM w Gdańsku") |
| Feed URL | `https://ckan.multimediagdansk.pl/dataset/c24aa637-3619-4dc2-a171-a23eec8f2172/resource/30e783e4-2bec-4a7d-bb22-ee3e3b26ca96/download/gtfsgoogle.zip` |
| Licence | dataset licence `cc-by`. The "Regulamin korzystania z Otwartych danych ZTM w Gdańsku" (2024-01-08) allows free reuse if you name *Zarząd Transportu Miejskiego w Gdańsku*, give the address of the "Otwarte dane ZTM w Gdańsku" service (the Regulamin prints <http://www.ztm.gda.pl/otwarty_ztm>; the credit uses the same address over https, which redirects to the dataset, checked 2026-11-17), and state when the data was created and retrieved (answers carry both in `as_of`). Re-read 2026-11-17: unchanged. |
| Size / date | 23,126,169 B, Last-Modified **2026-11-14 01:55 GMT**, retrieved 2026-11-14 |
| Validity | 2026-11-14 .. 2026-11-28: a rolling window of about 15 days, so **refresh daily** |
| Live API | `https://ckan2.multimediagdansk.pl/departures?stopId=<id>`: estimated departures per stop, listed in the same dataset and covered by the same Regulamin. Opt-in only (`--live gdansk`). |

Quirks:
- No `calendar.txt`. Every `service_id` is one date in `calendar_dates.txt`, and trip ids are per date (93,161 trips, 2,228,984 stop times for 15 days).
- The platform number is part of the stop name (`Oliwa PKP 02`). The build splits it into `name` + `platform`.
- Extended route types: 700 = bus, 900 = tram.
- Some headsigns repeat words (`Pruszcz Gdański Pruszcz Gdański Dworzec`). They are kept as published.
- The feed covers ZTM Gdańsk lines, including those running into Sopot and Pruszcz Gdański. **Gdynia (ZKM Gdynia) and SKM trains are separate feeds and are not included.**
- Only live entries with `status: REALTIME` are shown as live. In the one sample taken (Dworzec Główny, 2026-11-14 13:36 UTC), no tram departure was matched to a real-time estimate. Bus departures were.

## Kraków: ZTP Kraków

| | |
|---|---|
| Index | <https://gtfs.ztp.krakow.pl/> |
| Licence | CC BY 4.0 (stated on the index page) |
| Feeds | `GTFS_KRK_A.zip` MPK buses, 23,271,663 B, Last-Modified 2026-11-13 07:44 GMT · `GTFS_KRK_T.zip` trams, 4,888,756 B, 2026-11-13 00:10 GMT · `GTFS_KRK_M.zip` Mobilis buses, 2,774,947 B, 2026-11-13 15:00 GMT. All retrieved 2026-11-14. |
| Refresh | weekly (the feeds are republished several times a week) |
| Real-time | GTFS-RT `TripUpdates*.pb` / `VehiclePositions*.pb` under the same CC BY 4.0. **Not used**: reading them needs a protobuf decoder, a third runtime dependency or hand-written parser. The tram RT feed was also down on 2026-11-14 ("Brak danych GTFS-RT dla grupy Tramwaje"). |

Quirks:
- Three feeds are merged into one city file. Ids are prefixed `A:`, `T:`, `M:`. The same physical stop has separate ids per feed, so changes between feeds go through foot transfers computed on the walking graph.
- Every `calendar.txt` row has all weekdays set to 0. Service is defined only by `calendar_dates.txt`.
- The bus feeds list service dates up to **2028-01-31**: the current timetable projected forward, not a promise. The tram feed ends 2027-02-11.
- The bus feed sets `route_color` to `FFFFFF` for every line. White is treated as "no colour", and the mode default is used.
- Tram line numbering on 2026-11-14: 1, 3, 5, 7, 9, 11–18, 21, 22, 49, 62, 69–78. Older numbers such as 4 and 52 are not in the current feed.
- The tram feed ships `blocks.txt` (not used).

## Wrocław: Otwarte Dane Wrocław

| | |
|---|---|
| Dataset page | <https://open-data.cui.wroclaw.pl/hdb/ft/6/> (the city's new open-data portal, run by CUI Wrocław) |
| Feed URL | resolved at build time: the build opens the dataset page and downloads its newest `/hdb/download/<n>/` link. On 2026-11-15 that was `/hdb/download/162/`. |
| Licence | **CC0 1.0**, stated on the dataset's metadata page <https://open-data.cui.wroclaw.pl/hdb/metadane/13/> ("Licencja: CC0 1.0", read 2026-11-15). Credit is not required; the server gives it anyway. |
| Size / date | 12,057,086 B, retrieved 2026-11-15. The server sends no Last-Modified header, so `as_of` uses the newest file date inside the zip: 2026-11-13 12:22 local time. |
| Validity | 2026-11-16 .. 2026-11-29: a rolling window of about 2 weeks, so **refresh at least weekly** |
| Real-time | the city publishes vehicle positions (dataset "Lokalizacja pojazdów komunikacji miejskiej na trasie"), not departure estimates and not GTFS-RT. **Not used.** |

Quirks:
- The old portal (`www.wroclaw.pl/open-data`) has been replaced by the portal above. The Mobility Database copy (`mdb-980`, last updated 2025-11-25) is stale and is not used.
- Stop names are in capitals (`DWORZEC GŁÓWNY`, `PL. GRUNWALDZKI`). Search folds case, diacritics and common abbreviations (`plac` = `pl.`, `aleja` = `al.`, `osiedle` = `os.`), so "Plac Grunwaldzki" finds the stop.
- `agency.txt` names the companies that run each line: MPK Autobusy, MPK Tramwaje, DLA Wrocław, PT Kłosok, Nowak Transport. Answers show these as `operator`.
- `feed_info.txt` has no `feed_version`, only an end date.
- The zip also ships non-standard files (`variants.txt`, `control_stops.txt`, `contracts_ext.txt`, `vehicle_types.txt`, `route_types.txt`). They are ignored.
- Counts after import: 2,489 stops, 137 routes (112 bus, 25 tram), 42,546 trips, 1,134 stop patterns.

## Poznań: ZTM Poznań

| | |
|---|---|
| Developer portal | <https://www.ztm.poznan.pl/otwarte-dane/dla-deweloperow/> |
| Feed URL | `https://www.ztm.poznan.pl/pl/dla-deweloperow/getGTFSFile`, which always serves the newest file (`Content-Disposition: 20261114_20261129.zip` on 2026-11-15). Older files are listed on `/otwarte-dane/gtfsfiles/`. |
| Terms | "Zasady korzystania z treści udostępnianych przez ZTM w Poznaniu", on the portal page (read 2026-11-15). Free reuse on three conditions: name the source, give the date of the last update, and say the data was processed. The ZTM and Poznań logos may not be used. Software may not touch PEKA card accounts. Every answer meets the three conditions; the server uses no logos and no PEKA data. |
| Size | about 11.8 MB (the 2026-11-13 file), valid about 2 weeks, so **refresh at least weekly** |
| Real-time | GTFS-RT (trip updates, vehicle positions, alerts) under the same terms. Not used: it needs a protobuf decoder. |

Quirks:
- `agency.txt` names the operating companies: MPK Poznań and 13 suburban carriers (KOMBUS, TRANSKOM, Translub, ROKBUS, TPBUS…). Answers show them as `operator`.
- The network reaches well beyond the city: stops span about 67 × 55 km.
- Timetable part alone (measured, 2026-11-13 file): 31,261 runs, 2,058 stop patterns, 7.7 MB.

## Toruń: UM Toruń via mkuran.pl

| | |
|---|---|
| Feed URL | `https://mkuran.pl/gtfs/torun.zip`. Converted by Mikołaj Kuranowski (<https://mkuran.pl/gtfs/>); the city publishes no GTFS of its own. |
| Licence | CC0 1.0, stated on <https://mkuran.pl/gtfs/> (read 2026-11-15) |
| Size | about 2.8 MB; versions run about 6 weeks (2026-11-12 .. 2026-12-23) |
| Real-time | none public |

Quirks:
- No `attributions.txt`. The server credits the city, MZK Toruń and the converter itself.
- The city plans a new network from 1 March 2027, with new line numbers.
- Timetable part alone (measured): 5,138 runs, 308 patterns, 1.4 MB.

## Lublin: ZDiTM Lublin via mkuran.pl

| | |
|---|---|
| Feed URL | `https://mkuran.pl/gtfs/lublin.zip`, converted by Mikołaj Kuranowski (<https://mkuran.pl/gtfs/>). Also listed in the Mobility Database as `mdb-1013`, pointing at the same file. |
| Licence | CC0 1.0, stated on <https://mkuran.pl/gtfs/> ("ZTM Lublin … License: CC0 1.0", read 2026-11-17). No credit is required and commercial use is allowed; the server credits the authority and the converter anyway. |
| Official source | **not found.** The authority, Zarząd Dróg i Transportu Miejskiego w Lublinie (ZDiTM, formerly ZTM Lublin), links no GTFS or open data from <https://www.zdtm.lublin.eu> (`/pl/otwarte-dane` returns 404). The city's open-data portal (otwartedane.lublin.eu) and dane.gov.pl did not respond on 2026-11-17, so they were not checked. |
| Size / date | 2,103,584 B, Last-Modified 2026-11-06 17:48 GMT, retrieved 2026-11-17; `feed_version` 2026-10-31 (the date the operator's current timetable took effect); service dates 2026-10-31 .. 2027-01-29 |
| Real-time | none used (the operator's website shows vehicles on a map; no public feed was found) |
| Map data | the `lubelskie` extract, 139,758,179 B, Geofabrik, replication timestamp 2026-11-16T21:22:36Z |
| Build | 178 s with downloads (156 s of it downloading), max RSS 3.2 GB; `lublin.sqlite` 23.4 MB: 377,148 walking-graph nodes, 67,677 map features, 4,454 places (measured 2026-11-17) |

Quirks:
- One agency, "ZDiTM Lublin" (the authority); operators are not named.
- Bus lines (`route_type` 3) and trolleybus lines 150–162 and 950 (`route_type` 11), shown as mode `trolleybus`.
- No `calendar.txt`, `shapes.txt` or `feed_info` validity: four services defined by `calendar_dates.txt` only: weekdays (`PN`), Saturdays (`WN`), Sundays and holidays (`SN`) and the Friday and Saturday nights of the night lines N1–N3 (`NN`). Legs are drawn stop to stop.
- The pole number is in `stop_code` ("Ogród Saski" + `01`), not in the name, and the feed's `stop_id` is the operator's own stop number (`1001`), so stop ids can be looked up on the operator's timetable pages (`https://www.zdtm.lublin.eu/pl/rozklady/<stop_id>/<line>`).
- 1,316 stops spanning about 35 × 30 km, 75 routes (62 bus, 13 trolleybus), 10,303 trips stored as 9,971 distinct runs in 827 stop patterns.
- The operator's line list also has lines 200–202, the cemetery lines for All Saints' Day (31 October–2 November).
  They are not in the feed. On 2026-11-17 the operator's page for line 200 listed times only for those three days.

## Warszawa: ZTM Warszawa via mkuran.pl

| | |
|---|---|
| Feed URL | `https://mkuran.pl/gtfs/warsaw.zip`, converted by Mikołaj Kuranowski with [WarsawGTFS](https://github.com/MKuranowski/WarsawGTFS). ZTM publishes no GTFS itself. |
| Licence | "ZTM Warszawa Data Usage Terms (Polish) and ODbL for bus shapes", per <https://mkuran.pl/gtfs/> (read 2026-11-15). Attribution: everything in the feed's `attributions.txt`, i.e. ZTM Warszawa, Mikołaj Kuranowski and OpenStreetMap contributors. The server reads that file and repeats it in every answer. |
| ZTM's own terms | read 2026-11-19: ZTM Warszawa's statement on the re-use of public sector information (dated 2024-08-01). Everyone may re-use it under the Act of 11 August 2021 on open data and the re-use of public sector information (Dz. U. 2023 poz. 1524), on two conditions: name the source (ZTM Warszawa) with when the information was made and obtained, and say that it was processed. The server does both: every answer names the source and its publication date (`as_of`), `detail: "full"` and `list_cities` add the retrieval date, and the credits notice says the timetables were processed. ZTM takes no responsibility for processed data. |
| Size | 102.6 MB zip, 603 MB unpacked (the 2026-11-15 file); 31 days, 2026-11-15 .. 2026-12-15 |

What the feed holds, and what the build keeps:
- `stop_times.txt` is 528 MB with 7.77 million rows, for 271,683 trip records. Only **55,911** of those trips are distinct; the rest are the same trips repeated for each week. The build stores each distinct trip once, under a service covering all its dates. That loses nothing.
- The metro is given as 16 template trips plus `frequencies.txt` ("every 5.5 min from 09:17 to 21:12"), without exact times. The build expands them into 2,823 runs and marks them, and answers say their times are estimated from the frequency.
- `fare_attributes.txt` and `fare_rules.txt` are not used. Neither are the extra trip columns (brigade, fleet type, variant code).
- `shapes.txt` (45 MB) is kept, simplified to 4 m. It is ODbL, the same licence as the city files.
- Timetable part alone (measured): 58,775 runs, 1,920 patterns, **13.8 MB**.
- Stops span about 63 × 60 km (SKM and suburban lines). The first build was **169.1 MB**, over the 150 MB budget, almost all of it map features. Leaving unused service roads, sidewalks, crossings and lawns under 500 m² off the map layer (DECISIONS.md) brought it to **129.9 MB**.
- The copy used for the current build is the mkuran.pl feed of 2026-11-15, fetched through the Mobility Database mirror (`mdb-2092`).

## Katowice: ZTM GZM via mkuran.pl

| | |
|---|---|
| Feed URL | `https://mkuran.pl/gtfs/gzm.zip`, converted by Mikołaj Kuranowski from the open data of the Zarząd Transportu Metropolitalnego (<https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona>) |
| Licence | CC BY 4.0, "Attribution: To Zarząd Transportu Metropolitalnego and Mikołaj Kuranowski" (<https://mkuran.pl/gtfs/>, read 2026-11-15). The feed's `attributions.txt` names both, with links; the server repeats them. |
| Size / date | 32,827,770 B, Last-Modified 2026-11-15 13:06 GMT, retrieved 2026-11-16; valid 2026-11-15 .. 2026-12-15 |
| Map data | the `slaskie` extract, 214,511,010 B, OSM France mirror, 2026-11-15; MD5 matches the published one |

What it holds:
- One agency, ZTM (the authority); operators are not named.
- 7,269 stops, 492 routes, 2,841 stop patterns, 34,363 distinct runs over 31 days.
- 22,224 trips on Monday 2026-11-16; the city file loads 25,104 for that day, counting late trips of the day before.
- Stops span about 67 × 77 km (≈5,300 km²), the largest area of any city here.
- The city file is 112.7 MB: a walking graph of 1,734,969 nodes, 315,164 map features and 29,873 places. It built in 107 s, and the build peaked at **3.9 GB** of RAM.

## Zakopane: city buses transcribed from the city's PDFs

Map data comes from the `malopolskie` extract (the same file as Kraków), clipped to the town,
Kościelisko, Kuźnice and the road to Palenica Białczańska.

**Timetable source**
- **City lines 10–18:** 9 PDFs on <https://www.zakopane.pl/komunikacjamiejska/rozklady-jazdy>, 2.2–4.3 MB each, retrieved 2026-11-15 19:45 UTC. Each page is a single 300-ppi image with no text layer, created 2026-09-29.
- **No other machine-readable timetable exists:** nothing in the Mobility Database or Transitland. The city's "Rozkład jazdy" page embeds rozkladzik.pl, a third-party site that was not checked.
- **Terms:** timetables of a municipal service are public sector information, which everyone may re-use under the Act of 11 August 2021 on open data and the re-use of public sector information (Dz. U. 2023 poz. 1524). The zakopane.pl footer says "© Zakopane. Wszystkie prawa zastrzeżone." (all rights reserved) and states no re-use conditions; the town's BIP (public information bulletin) was not checked. The transcription is not published, so **no prebuilt Zakopane file with timetables is published** for now.

**How the timetable was made** (`scripts/zakopane_gtfs.py`)
- **Two independent transcriptions:** two separate readers transcribed every page into JSON without seeing each other's work. Compared cell by cell, they agree on all **4,513** cells. The script refuses to run if they differ.
- **Misprints:** 19 printed cells are clearly misprinted, for example "15:56" between 14:54 and 15:01, "20:52" between 22:50 and 22:54, or "7:9:38". They are left out, and the importer interpolates those stops. One trip loses its final stop this way. Every cell is listed in `data/raw/zakopane/Z-report.txt`.
- **Run days:** taken from the printed legends. A trip runs on the union of its "runs" marks (Q, s, y, ł, n), or daily if it has none, minus its "does not run" marks (q, g). Mark "w" removes three stops from 1.07–31.08 and 24.12–06.01. Mark "&" (continues to the bus station on 24.12) has no printed times and is not modelled.
- **Assumed validity:** the PDFs print no validity dates, so the feed covers 2026-11-15..2027-02-28. Every answer says the dates are assumed.
- **Stop positions:** 73 stops.
  - 52 are OpenStreetMap stops with the same or an equivalent name ("Dworzec st. 17" → "Rejon Dworca st. 17").
  - 5 are placed at the landmark they are named after, and are approximate.
  - 16 are placed on the named street between the neighbouring stops, in proportion to the timetable's minutes, and are estimated.
  - "Kasprusie ZCE" ends up on the same point as "Małe Żywczańskie".
- **Result:** 9 lines, 213 trips, 4,534 stop times. The operator is shown as "Komunikacja miejska Zakopane (Gmina Miasto Zakopane)". TESKO runs the city lines; the operator of lines 13 and 18 was not confirmed.

**Not included:** the county lines (10 scanned permits with no day types), the private minibuses
(e.g. to Morskie Oko) and the TPN e-buses.

## National layer (`poland`): trains and FlixBus coaches

`poland-transit-mcp build poland --download` downloads both feeds (58 MB) and builds `poland.sqlite` without
OSM data (no osmium needed): 14.5 MB, 12 s, peak 334 MB of RAM (measured 2026-11-16).

### Polish Trains GTFS (PKP PLK open data via mkuran.pl)

| | |
|---|---|
| Feed URL | `https://mkuran.pl/gtfs/polish_trains.zip`, built by Mikołaj Kuranowski from PKP PLK's open railway data (<https://www.plk-sa.pl/klienci-i-kontrahenci/api-otwarte-dane>) plus Koleje Mazowieckie's own data |
| Licence | PKP PLK "Otwarte Dane Kolejowe" terms v1.0 (<https://pdp-api.plk-sa.pl/terms/terms-v1.0.en.md>, read 2026-11-16): building travel-planning tools is a listed use; anyone publishing the data must cite PKP Polskie Linie Kolejowe S.A.; reselling the raw data without consent is prohibited. Koleje Mazowieckie: re-use of public sector information (<https://bip.mazowieckie.com.pl/pl/ponowne-wykorzystywanie-informacji-sektora-publicznego>), **not read yet**. |
| Credits | the feed's `attributions.txt`: Koleje Mazowieckie (data source, operator), Mikołaj Kuranowski (GTFS producer), PKP PLK (data source, authority); repeated in every answer |
| Size / date | 26,553,191 B, retrieved 2026-11-15; feed version 2026-11-12T02:45; valid 2026-11-12 .. 2026-12-12 |

What it holds, and what the build keeps:
- 13 operators: PolRegio, PKP Intercity, Koleje Mazowieckie, Koleje Śląskie, Koleje Dolnośląskie, Koleje Wielkopolskie, SKM Warszawa, PKP SKM Trójmiasto, ŁKA, Koleje Małopolskie, Arriva RP, Leo Express, RegioJet.
- 12,135 stops: stations, and under each a stop per platform and track (e.g. `80416_RAIL_5_10`, Kraków Główny platform 5, track 10). 8,486 platform-level stops in Poland, the rest at border stations abroad.
- 25,714 trips, of which **12,880** are distinct runs; 477,590 stop times.
- Platform on 98 % of stop times and track on 96 %. Both are kept per stop, with each train's published name and number (`3120/1 Karłowicz`) and category (`IC`, `EIP`, `R`, `Os`...), in a `trip_info` table.
- A few hundred services also list past dates back to December 2025. Validity is taken from `feed_info.txt` (2026-11-12 ..) so that those do not stretch it.
- Shapes kept, simplified to 25 m (lines across the country; 4 m in the cities).
- Checked against Koleje Małopolskie's own feed: see [docs/CHECKS.md](docs/CHECKS.md).
- The rail timetable changes on 13 December 2026; the feed is rebuilt about weekly.

### FlixBus Europe GTFS (trips serving Poland)

| | |
|---|---|
| Feed URL | `https://files.mobilitydatabase.org/tdg-11681/latest.zip`: the Mobility Database's mirror of FlixBus's dataset on France's national access point (<https://transport.data.gouv.fr/datasets/flixbus-horaires-theoriques-du-reseau-europeen-1>) |
| Licence | ODbL 1.0, the same as the city files |
| Publisher | FlixMobility Tech GmbH (`feed_info.txt`) |
| Size / date | 31,412,778 B, retrieved 2026-11-15; valid 2026-11-08 .. 2027-05-08 |

Quirks, and what the build does:
- **Times are in UTC.** The agency time zone is UTC. The build adds Poland's offset for each date: +1 h in winter, +2 h in summer. FlixBus plans in local time, so a summer run at 13:05 UTC and a winter run at 14:05 UTC are both 15:05 in Poland, and are stored once. The offset is taken at noon, so a run in the hour of a clock change can be an hour off on those two nights a year.
- **Europe-wide.** Only trips that call at a stop with `stop_timezone` Europe/Warsaw are kept, and they are kept whole as far as their stops keep Polish time (Czechia, Germany, Austria, Italy and the other Central European zones): a Praha → Wrocław or Berlin → Kraków coach appears whole. A stop on another clock (e.g. Vilnius, Kyiv) is dropped, and the trip keeps its parts on Polish time. Waypoints ("Heidelberg Pre Waypoint", "Puttgarden (Ferry) Waypoint", "(Pre UK Check)") are not stops for passengers and are left out (`not_stops`). A run that starts abroad the evening before takes the UTC offset of the day it reaches Poland, and a run longer than about two days leaves out its first stops abroad, so that it ends within 48 h of its day's midnight. In the 2026-11-17 feed: 473 of 2,082 stops and 550 runs (210 stops before, when only Polish stops were kept). A trip that reaches Poland after midnight UTC (times like 25:30, up to 41:15 in the feed) moves to the next day.
- 37,938 trips → **508** distinct runs in Poland, 172 lines.
- `shapes.txt` (151 MB, nearly all outside Poland) is not imported: coach legs are drawn stop to stop.
- The operator is named "FlixBus-eu" in the feed and "FlixBus" in answers.
- Stop names are in English without diacritics ("Krakow, MDA Bus Station"). Name matching ignores diacritics.
- 21 Polish stop names end in a stand or street number ("Kostrzyn nad Odra, Dworcowa Street 22 and 17",
  "Chelm, Armii Krajowej Street/KFC 05 and 06"). They are kept whole: the rule that splits a trailing platform number
  off a stop name (made for ZTM Gdańsk's "Oliwa PKP 02") is off for this feed (`platform_in_name=False`).

Not available as open data: the private Kraków–Zakopane coaches and minibuses (Szwagropol, Majer, Trans-Kos...),
PKS Polonus, Neobus, and all fares.

## Other mirrors checked

The Mobility Database catalogue (<https://files.mobilitydatabase.org/feeds_v2.csv>) was used only to look up feed URLs and the stale Wrocław copy.
