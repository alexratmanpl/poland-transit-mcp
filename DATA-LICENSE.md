# Data licence

The **code** in this repository is MIT-licensed (see `LICENSE`). The **city data files**
(`<city>.sqlite`, built by `poland-transit-mcp build` or, once published, attached to GitHub Releases) are a separate
work with their own terms, described here. No data file is stored in this git repository.

## City files: ODbL 1.0

Every city file is derived from OpenStreetMap, so the city files are published under the
**Open Database License (ODbL) 1.0**: <https://opendatacommons.org/licenses/odbl/1-0/>.

Attribution: **© OpenStreetMap contributors** — <https://www.openstreetmap.org/copyright>

If you redistribute a city file, or a database derived from one, keep this attribution and
offer it under the ODbL. Every answer from the server already carries the attribution in its
`source` field.

## Timetable data inside the city files

The city files also contain public-transport timetables from each operator's GTFS feed. Those
feeds have their own licences and credit requirements, and these continue to apply to the
timetable content. Every server response names the feed it used, together with its publication
and retrieval dates (`source`, `as_of`).

| City | Feed | Licence | Required credit | Prebuilt file may be published |
|---|---|---|---|---|
| Gdańsk | ZTM Gdańsk GTFS ("Rozkład jazdy GTFS", dataset `tristar` on ckan.multimediagdansk.pl) | CC BY (dataset licence `cc-by`); reuse rules in ZTM's "Regulamin korzystania z Otwartych danych ZTM w Gdańsku" (2024-01-08) | "Zarząd Transportu Miejskiego w Gdańsku", the address of the service (the Regulamin prints <http://www.ztm.gda.pl/otwarty_ztm>; given over https), and the dates the data was created and retrieved | yes |
| Kraków | ZTP Kraków GTFS: `GTFS_KRK_A` (buses), `GTFS_KRK_T` (trams), `GTFS_KRK_M` (Mobilis buses) from gtfs.ztp.krakow.pl | CC BY 4.0 (stated on gtfs.ztp.krakow.pl) | "Zarząd Transportu Publicznego w Krakowie", <https://gtfs.ztp.krakow.pl> | yes |
| Poznań | ZTM Poznań GTFS (www.ztm.poznan.pl, developer portal) | ZTM Poznań's terms: free reuse | the source (ZTM Poznań), the date of the last update, and a statement that the data was processed; no ZTM or Poznań logos | yes |
| Toruń | Toruń GTFS converted by Mikołaj Kuranowski (mkuran.pl) | CC0 1.0 | none required; the server credits Gmina Miasta Toruń, MZK Toruń and Mikołaj Kuranowski (<https://mkuran.pl/gtfs/>) | yes |
| Lublin | Lublin GTFS converted by Mikołaj Kuranowski (mkuran.pl) from ZDiTM Lublin timetables | CC0 1.0 (stated on <https://mkuran.pl/gtfs/>) | none required; the server credits Zarząd Dróg i Transportu Miejskiego w Lublinie (ZDiTM) and Mikołaj Kuranowski (<https://mkuran.pl/gtfs/>) | yes |
| Warszawa | Warszawa GTFS converted by Mikołaj Kuranowski (mkuran.pl) from ZTM Warszawa data | ZTM Warszawa data usage terms; bus shapes ODbL 1.0 | everything in the feed's `attributions.txt`: ZTM Warszawa, Mikołaj Kuranowski (<https://mkuran.pl/gtfs/>), OpenStreetMap contributors | **no**, until ZTM's terms are read |
| Katowice (GZM) | GZM GTFS converted by Mikołaj Kuranowski (mkuran.pl) from ZTM GZM open data | CC BY 4.0 | "Zarząd Transportu Metropolitalnego" and "Mikołaj Kuranowski" (<https://mkuran.pl/gtfs/>) | yes |
| Zakopane | transcribed by this project from the city's image-only PDF timetables (zakopane.pl) | unclear: the site states "all rights reserved" and no reuse terms | Gmina Miasto Zakopane | **map and walking only**; the transcription is not published |
| Wrocław | Wrocław GTFS from Otwarte Dane Wrocław (open-data.cui.wroclaw.pl, dataset `ft/6`) | CC0 1.0 (stated on the dataset's metadata page) | none required; the server credits "Urząd Miejski Wrocławia – Wydział Transportu" anyway | yes |

The national layer (`poland.sqlite`, trains and coaches between cities) has no OpenStreetMap data:

| File | Feed | Licence | Required credit | Prebuilt file may be published |
|---|---|---|---|---|
| `poland` | Polish Trains GTFS (mkuran.pl) from PKP PLK open data and Koleje Mazowieckie data | PKP PLK open railway data terms v1.0: use in travel-planning tools allowed; no resale of the raw data. Koleje Mazowieckie: re-use of public sector information (terms not read yet) | "PKP Polskie Linie Kolejowe S.A.", plus everything in the feed's `attributions.txt` (Koleje Mazowieckie, Mikołaj Kuranowski) | **no**, until Koleje Mazowieckie's terms are read; build it yourself |
| `poland` | FlixBus Europe GTFS via transport.data.gouv.fr, trips serving Poland | ODbL 1.0 | FlixBus; transport.data.gouv.fr | (same file) |

`poland.sqlite` is not offered under one licence: each feed's terms apply to its part (the FlixBus part is ODbL 1.0,
the train part follows PKP PLK's and Koleje Mazowieckie's terms).

The city files are built from these feeds after processing: feeds are merged, ids are prefixed,
shapes are simplified and only the service window is kept. Every answer says so, and tells you to
check the operator for last-minute changes.

Live departure estimates (Gdańsk only, opt-in) are fetched by your own server process straight
from ZTM Gdańsk. They are never stored in the city files and never proxied by this project.

This summary is not legal advice; the licence texts linked above are authoritative.
