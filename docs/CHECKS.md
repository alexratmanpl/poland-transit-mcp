# Acceptance checks

Run on **2026-11-14** against the city files built that day; Wrocław on **2026-11-15**. Every number below is *measured*,
and the command is given. Anything not observed is labelled *not checked* or *could not verify*.

Data used:
- **OSM:** replication timestamp 2026-11-13T01:06:06Z.
- **ZTM Gdańsk GTFS:** published 2026-11-14 01:55 GMT.
- **ZTP Kraków GTFS:** published 2026-11-13.
- **Wrocław GTFS (Otwarte Dane Wrocław):** files dated 2026-11-13 12:22, retrieved 2026-11-15, valid 2026-11-16..2026-11-29.

All timetable queries are for **Monday 2026-11-16**, an ordinary weekday.

## Timetable accuracy against the operators' websites

### Gdańsk: ZTM Gdańsk, `https://ztm.gda.pl/rozklady/`

The operator publishes one page per line, stop and date (`…-dzien-20261116.html`), plus one page
per trip. Pages were fetched 2026-11-14 13:44–13:53 UTC with `curl -sSL`, and times read from each
departure's `aria-label`. Every stop id below equals the operator's own bracketed stop number.

| Check | Ours | Operator page (observed) | Verdict |
|---|---|---|---|
| Tram 8 @ Brama Wyżynna from 08:00 | 08:08 → Jelitkowo [2102]; 08:18 → Jelitkowo [2102]; 08:18 → Głęboka [2101] | 2102 → Jelitkowo: 07:58, **08:08, 08:18**, 08:28 · 2101 → Głęboka: 07:58, **08:18**, 08:28 (`rozklad-008_20261003-7-1`, `…-44-2`) | **match** |
| Bus 106 @ Dworzec Główny from 13:00 | 13:18 → Plac Solidarności [1016]; 13:47, 14:41 → Muzeum Westerplatte [1021] | 1016: 10:32, **13:18**, 15:14 · 1021: 11:58, **13:47, 14:41**, 16:01 (`rozklad-106_20261024-25-1`, `…-2-2`) | **match** |
| Tram 6 @ Oliwa from 17:30 | 17:30 → Łostowice [2046]; 17:35 → Jelitkowo [2045]; 17:40 → Strzyża PKM [2046] | 2046: **17:30, 17:40** (footnote "kurs do: Strzyża PKM"), 17:50 · 2045: 17:25, **17:35**, 17:45 (`rozklad-006_20261102-10-2`, `…-35-1`) | **match** |

Small difference: our headsigns keep the feed's en dash ("Muzeum Westerplatte – Terminal
Promowy"), while the operator's website uses a hyphen.

Live departures (opt-in, `--live gdansk`) were merged against the real ZTM Gdańsk API on 2026-11-14:
25 scheduled departures matched at Dworzec Główny.

### Kraków: times not compared (the operator's timetable is served only through a supplier API)

- `rozklady.mpk.krakow.pl` redirects everything to `mpk.krakow.pl/rozklady-jazdy`.
- That page is an empty shell. Its script loads the timetable from the supplier's API at `services.mpk.amistad.pl`.
- That API was not checked.
- `ztp.krakow.pl` only embeds the same page.

So no Kraków departure time was compared with an operator page. Pages fetched 2026-11-14 13:44–14:08 UTC.

| Check | Ours (GTFS published 2026-11-13) | Operator context seen (no times) | Verdict |
|---|---|---|---|
| Tram 18 @ Teatr Bagatela from 08:00 | 08:05 → Górka Narodowa P+R; 08:10 → Czerwone Maki P+R; 08:20 → Górka Narodowa P+R | MPK notice 153 (valid from 9.11.2026) and ZTP notices 2188/2249: line 18 runs a temporary route via Podwale–Basztowa, past this stop | **could not verify** |
| Tram 9 @ Rondo Mogilskie from 12:00 | 12:05 → Mistrzejowice; 12:12 → Nowy Bieżanów P+R; 12:20 → Mistrzejowice | Line 9 runs its permanent route via Rondo Mogilskie; the direction labels agree | **could not verify** |
| Tram 11 @ Czerwone Maki P+R from 22:30 | none on Monday after 22:30; next Tue 05:01, 05:26, 05:46 | Line 11 is not among the changed lines. The claim of no late-evening departure is **unconfirmed** and the one most worth re-checking. | **could not verify** |

To re-run: open <https://mpk.krakow.pl/rozklady-jazdy> in a browser and compare it with
`python scripts/acceptance.py --date 2026-11-16`.

### Wrocław: the city's timetable pages, `https://www.wroclaw.pl/komunikacja/`

The city publishes one page per line, stop pole and direction
(`przystanek-<stop>-linia-<line>-kierunek-<direction>-slupek-<pole>`). The pole number is the
feed's `stop_code`, so each check compares the same pole. Pages were fetched 2026-11-15 with
`curl -sSL`, and the times read from the embedded weekday table ("w dni robocze").

| Check | Ours | City page (observed) | Verdict |
|---|---|---|---|
| Tram 16 @ Plac Grunwaldzki from 08:00, → Tarnogaj | 08:01, 08:13 [W:4898 = pole 20824] | 08:**01**, 08:**13**, 08:25, 08:37, 08:49 (`…-linia-16-kierunek-tarnogaj-slupek-20824`) | **match** |
| Bus D @ Galeria Dominikańska from 12:00, → Osiedle Sobieskiego | 12:09, 12:24 [W:546 = pole 10111] | 12:**09**, 12:**24**, 12:39, 12:54 (`…-linia-d-kierunek-osiedle-sobieskiego-slupek-10111`) | **match** |
| Tram 23 @ Dworzec Główny from 22:30, → Kowale | 22:36, 22:56, 23:24 (→ Kromera) [W:3523 = pole 10268] | 22:17, 22:37, 22:57, **23:24** (`…-linia-23-kierunek-kowale-slupek-10268`) | **1 min apart** on two of three |

The tram 23 difference is in the sources, not in our processing. The feed's own `stop_times.txt`
has 22:16, 22:36, 22:56 and 23:24 for Monday's trips (service 6, stop 3523), and we report those.
The city's website shows one minute later for the first three. Not checked: which one the trams keep.



### Lublin: ZDiTM Lublin, `https://www.zdtm.lublin.eu/pl/rozklady/<stop>/<line>`

Checked on 2026-11-17 against the city file built that day (mkuran.pl feed `feed_version` 2026-10-31, Last-Modified
2026-11-06; OSM `lubelskie` 2026-11-16T21:22:36Z). The operator publishes one page per stop pole and line, for the
timetable "obowiązujące od 2026-10-31", the same date as the feed. The feed's `stop_id` is the operator's pole number,
so each check compares the same pole. Pages were fetched over HTTPS and the times read from each day type's table
(hour columns, minutes below). Line ids in the URLs are padded to three characters (`045`, `0N1`). Letters after a
minute (`06a`) mark route variants. Queries were for Wednesday 2026-11-18,
Saturday 2026-11-21 and the night from Friday 2026-11-20.

| Check | Ours | Operator page (observed) | Verdict |
|---|---|---|---|
| Trolleybus 150 @ Ogród Saski 03 [L:1003] from 08:00, Wed | 08:11, 08:26, 08:41, 08:56 → Os. Poręba | weekday ("dzień powszedni, rok szkolny"): 07:55, **08:11, 08:26, 08:41, 08:56**, 09:11. All 60 weekday, 35 Saturday and 26 Sunday times on the page equal the feed's. | **match** |
| Trolleybus 150 @ the same pole from 08:00, Sat | 08:22, 08:52, 09:22, 09:52 | Saturday: 07:52, **08:22, 08:52, 09:22, 09:52** | **match** |
| Dworzec Lublin from 12:00, Wed: trolleybus 153 [L:3421], bus 45 and trolleybus 161 [L:3422] | 153: 12:10, 12:40, 13:10 → Węglin · 45: 12:04, 12:34 → Mełgiewska WSEI, 13:06 → Metalurgiczna · 161: 12:04 → Felin | 153: **12:10, 12:40, 13:10** · 45: **12:04, 12:34, 13:06a** ("a" = to Metalurgiczna) · 161: 11:34, **12:04**, 12:34 | **match** |
| Night bus N1 @ Brama Krakowska 02 [L:1032] from 23:00, Fri | 23:25, then 00:25, 01:25, 02:25, 03:25 (Sat) → Żeglarska | "komunikacja nocna": **23:25, 00:25, 01:25, 02:25, 03:25**, 04:25. Runs on Friday and Saturday nights. | **match** |

`plan_trip` Dworzec Lublin → Plac Litewski, Wednesday 2026-11-18 09:00: bus 74 Dworzec Lublin 09:03 → Ogród Saski
09:13, then 705 m on foot, arriving 09:21. The operator's pages list 74 at Dworzec Lublin 52 at **09:03** and at
Ogród Saski 03 at **09:13**: **match**. The second and third options (45 → 17 → 159, and 45 → 17) also match the
pages for 45 at 09:05, 17 at Plac Bychawski 03 at 09:10 and 159 at Zamojska 02 at 09:18.

Not compared: the operator's line list has lines 200–202 (All Saints' Day cemetery lines, 31 October–2 November),
which are not in the feed (SOURCES.md).

**Trains and trips between cities.** The national layer has Lublin Główny as eight stop ids (seven platform and track positions and a fallback). On
Wednesday 2026-11-18 it lists PolRegio regional trains and PKP Intercity trains, for example IC 2520/1 Kopernik
06:27 → Bydgoszcz Główna (pl. 1, tr. 52), IC 1338/9 Wisłok 07:53 → Rzeszów Główny, IC 3538/9 Bystrzyca 08:25 →
Bydgoszcz Główna, IC 2602/3 Bolko 12:36 → Wrocław Główny and IC/MP 67 Kyiv Express 07:44 → Warszawa Zachodnia. Not
checked against PKP Intercity's own pages. With `lublin`, `torun` and `poland` loaded, `plan_trip` with `to_city`:
- Lublin, Plac Litewski → Toruń, Dom Mikołaja Kopernika, depart 2026-11-18 05:30: 06:04 → 11:07 (303 min, 2 changes):
  bus 6 06:12–06:19; IC 2520/1 Kopernik 06:27 Lublin Główny (pl. 1, tr. 52) → 10:49 Toruń Główny (pl. 2, tr. 1); bus 27 10:58–11:02.
- Toruń, Dom Mikołaja Kopernika → Lublin, Plac Litewski, depart 2026-11-18 12:00: 12:59 → 18:01 (302 min, 2 changes):
  bus 12 13:05–13:10; IC 5222/3 Rejewski 13:19 Toruń Główny (pl. 1, tr. 2) → 17:34 Lublin Główny (pl. 1, tr. 52); bus 74 17:43–17:53.

### Zakopane: transcription of the city's PDF timetables

**Two independent passes.** Two readers transcribed all 16 pages (lines 10–18) without seeing each
other's work. Compared cell by cell on 2026-11-16, they gave:
**4,513 cells, 0 differences.** `scripts/zakopane_gtfs.py` refuses to run if the two transcriptions differ. Both passes flagged the same 19 printed misprints.

**Against the printed page** for line 10 (Dworzec st. 17 → Kuźnice), Monday 2026-11-16, after the GTFS was built:

| Check | Ours | Printed (line 10 PDF) | Verdict |
|---|---|---|---|
| Line 10 @ Dworzec st. 17 from 09:00 | 09:00, 09:40, 10:40, 11:30, 12:35 | 9:00, 9:15 Q, 9:40, 10:05 Q, 10:40, 11:05 Q, 11:30, 12:00 Q, 12:35. "Q" means daily 01.05–30.09 only. | **match** (Q trips correctly absent in November) |
| Krupówki → Kuźnice, depart 09:00 | bus 10 Krupówki Pomnik hr. Wł. Zamoyskiego 09:03 → Kuźnice 09:13 | 9:00 trip: stop 3 at 9:03, Kuźnice 9:13 | **match** |
| Lines 15 and 16 on Monday 16 Nov, Saturday 21 Nov, Monday 28 Dec | Mon: 15 runs, 16 doesn't; Sat: 16 only; 28 Dec: 16 only | 15 = "s q" (Mon–Fri 01.09–30.06, not 24.12–06.01); 16 = "y ł" (weekends 01.09–30.06, daily 24.12–06.01) | **match** |

**Not checkable:** whether the timetable printed in September is still in force. The PDFs carry
no validity dates.

## Trip plans

Command: `python scripts/acceptance.py --date 2026-11-16`. Our output:

```text
## gdansk  (OSM 2026-11-13T01:06:06Z; G: ok, published Sat, 14 Nov 2026 01:55:00 GMT)
departures Brama Wyżynna 2026-11-16 from 08:00 line 8: 8 08:08 → Jelitkowo [G:2102 / 01]; 8 08:18 → Jelitkowo [G:2102 / 01]; 8 08:18 → Głęboka [G:2101 / 02] (operator: Zarząd Transportu Miejskiego w Gdańsku)
departures Dworzec Główny 2026-11-16 from 13:00 line 106: 106 13:18 → Plac Solidarności [G:1016 / 07]; 106 13:47 → Muzeum Westerplatte – Terminal Promowy [G:1021 / 08]; 106 14:41 → Muzeum Westerplatte – Terminal Promowy [G:1021 / 08] (operator: Zarząd Transportu Miejskiego w Gdańsku)
departures Oliwa 2026-11-16 from 17:30 line 6: 6 17:30 → Łostowice Świętokrzyska [G:2046 / 02]; 6 17:35 → Jelitkowo [G:2045 / 01]; 6 17:40 → Strzyża PKM [G:2046 / 02] (operator: Zarząd Transportu Miejskiego w Gdańsku)
trip "main station → old town": Dworzec PKP Gdańsk Główny → Ratusz Głównego Miasta, depart 2026-11-16 09:00 — walking (14 min) is listed first: the fastest transit option saves only 3 min
  09:00→09:14 (14 min, 0 transfers): walk 14 min
  09:03→09:14 (11 min, 0 transfers): walk 2 min | bus 189 [Zarząd Transportu Miejskiego w Gdańsku] "Starogardzka" Dworzec Główny 09:05 → Brama Wyżynna 09:07 | walk 7 min
  09:04→09:15 (11 min, 0 transfers): walk 1 min | bus 100 [Zarząd Transportu Miejskiego w Gdańsku] "Targ Rybny" Dworzec Główny 09:06 → Targ Chlebowy 09:12 | walk 3 min
trip "trip needing a transfer": Archikatedra Oliwska → Westerplatte, depart 2026-11-16 10:00
  11:16→12:48 (92 min, 1 transfers): walk 9 min | tram 12 [Zarząd Transportu Miejskiego w Gdańsku] "Ujeścisko" Obrońców Westerplatte 11:25 → Hucisko 11:55 | walk 3 min | bus 106 [Zarząd Transportu Miejskiego w Gdańsku] "Muzeum Westerplatte – Terminal Promowy" Brama Wyżynna 12:00 → Muzeum Westerplatte – Terminal Promowy 12:41 | walk 8 min
  12:24→14:00 (96 min, 1 transfers): walk 9 min | tram 6 [Zarząd Transportu Miejskiego w Gdańsku] "Łostowice Świętokrzyska" Obrońców Westerplatte 12:33 → Śródmieście SKM 13:06 | walk 1 min | bus 138 [Zarząd Transportu Miejskiego w Gdańsku] "Muzeum Westerplatte – Terminal Promowy" Śródmieście SKM 13:17 → Muzeum Westerplatte – Terminal Promowy 13:53 | walk 8 min
  12:48→14:00 (72 min, 2 transfers): walk 9 min | tram 5 [Zarząd Transportu Miejskiego w Gdańsku] "Nowy Port Zajezdnia" Obrońców Westerplatte 12:57 → Galeria Bałtycka 13:10 | walk 2 min | bus 258 [Zarząd Transportu Miejskiego w Gdańsku] "Stogi" Galeria Bałtycka 13:13 → Terminal DCT 13:43 | bus 138 [Zarząd Transportu Miejskiego w Gdańsku] "Muzeum Westerplatte – Terminal Promowy" Terminal DCT 13:47 → Muzeum Westerplatte – Terminal Promowy 13:53 | walk 8 min
trip "late night after 23:30": Dworzec Główny → Zaspa, depart 2026-11-16 23:40
  23:59→2026-11-17 00:22 (24 min, 0 transfers): walk 1 min | bus N1 [Zarząd Transportu Miejskiego w Gdańsku] "Osowa PKP" Dworzec Główny 2026-11-17 00:00 → Startowa 2026-11-17 00:17 | walk 6 min
  2026-11-17 00:29→2026-11-17 01:01 (32 min, 0 transfers): walk 0 min | bus N14 [Zarząd Transportu Miejskiego w Gdańsku] "Żabianka SKM" Dworzec Główny 2026-11-17 00:30 → Kołobrzeska 2026-11-17 00:54 | walk 8 min
  2026-11-17 00:59→2026-11-17 01:22 (24 min, 0 transfers): walk 1 min | bus N1 [Zarząd Transportu Miejskiego w Gdańsku] "Osowa PKP" Dworzec Główny 2026-11-17 01:00 → Startowa 2026-11-17 01:17 | walk 6 min

## krakow  (OSM 2026-11-13T01:06:06Z; A: ok, published Fri, 13 Nov 2026 07:44:37 GMT; T: ok, published Fri, 13 Nov 2026 00:10:15 GMT; M: ok, published Fri, 13 Nov 2026 15:00:31 GMT)
departures Teatr Bagatela 2026-11-16 from 08:00 line 18: 18 08:05 → Górka Narodowa P+R [T:stop_191_7719 / 01]; 18 08:10 → Czerwone Maki P+R [T:stop_191_7729 / 02]; 18 08:20 → Górka Narodowa P+R [T:stop_191_7719 / 01] (operator: ZTP Kraków)
departures Rondo Mogilskie 2026-11-16 from 12:00 line 9: 9 12:05 → Mistrzejowice [T:stop_203_12529 / 02]; 9 12:12 → Nowy Bieżanów P+R [T:stop_203_12539 / 03]; 9 12:20 → Mistrzejowice [T:stop_203_12529 / 02] (operator: ZTP Kraków)
departures Czerwone Maki P+R 2026-11-16 from 22:30 line 11: 11 2026-11-17 05:01 → Mały Płaszów P+R [T:stop_572_303819 / 01]; 11 2026-11-17 05:26 → Mały Płaszów P+R [T:stop_572_303819 / 01]; 11 2026-11-17 05:46 → Mały Płaszów P+R [T:stop_572_303819 / 01] (operator: ZTP Kraków)
trip "main station → old town": Kraków Główny → Rynek Główny, depart 2026-11-16 09:00 — walking (17 min) is listed first: the fastest transit option saves only 3 min
  09:00→09:16 (17 min, 0 transfers): walk 17 min
  09:01→09:14 (14 min, 0 transfers): walk 4 min | tram 3 [ZTP Kraków] "Nowy Bieżanów P+R" Dworzec Główny Zachód 09:05 → Poczta Główna 09:09 | walk 6 min
  09:03→09:18 (15 min, 0 transfers): walk 7 min | tram 12 [ZTP Kraków] "Czerwone Maki P+R" Teatr Słowackiego 09:11 → Poczta Główna 09:13 | walk 6 min
trip "trip needing a transfer": Wieliczka Rynek-Kopalnia → Bronowice Małe, depart 2026-11-16 10:00
  10:04→11:01 (57 min, 1 transfers): walk 3 min | bus 304 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Dworzec Główny Zachód" Wieliczka Kopalnia Soli 10:07 → Plac Inwalidów 10:41 | walk 2 min | bus 704 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Bronowice Małe" Plac Inwalidów 10:46 → Bronowice Małe 10:58 | walk 3 min
  10:14→11:11 (57 min, 1 transfers): walk 3 min | bus 304 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Dworzec Główny Zachód" Wieliczka Kopalnia Soli 10:17 → Plac Inwalidów 10:51 | walk 2 min | bus 704 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Bronowice Małe" Plac Inwalidów 10:56 → Bronowice Małe 11:08 | walk 3 min
  10:24→11:21 (57 min, 1 transfers): walk 3 min | bus 304 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Dworzec Główny Zachód" Wieliczka Kopalnia Soli 10:27 → Plac Inwalidów 11:01 | walk 2 min | bus 704 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Bronowice Małe" Plac Inwalidów 11:06 → Bronowice Małe 11:18 | walk 3 min
trip "late night after 23:30": Rynek Główny → Plac Centralny im. R. Reagana, depart 2026-11-16 23:40
  23:49→2026-11-17 00:28 (39 min, 0 transfers): walk 11 min | bus 662 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Plac Centralny im. R. Reagana" Teatr Słowackiego 2026-11-17 00:00 → Plac Centralny im. R. Reagana 2026-11-17 00:26 | walk 3 min
  23:50→2026-11-17 00:28 (38 min, 1 transfers): walk 9 min | bus 664 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Os. Piastów" Teatr Słowackiego 2026-11-17 00:00 → Rondo Czyżyńskie 2026-11-17 00:14 | walk 3 min | bus 662 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Plac Centralny im. R. Reagana" Rondo Czyżyńskie 2026-11-17 00:21 → Plac Centralny im. R. Reagana 2026-11-17 00:26 | walk 3 min
  23:50→2026-11-17 00:30 (40 min, 0 transfers): walk 9 min | bus 664 [Miejskie Przedsiębiorstwo Komunikacyjne S.A. w Krakowie] "Os. Piastów" Teatr Słowackiego 2026-11-17 00:00 → Rondo Kocmyrzowskie im. ks. Gorzelanego 2026-11-17 00:17 | walk 14 min

## wroclaw  (OSM 2026-11-13T01:06:06Z; W: ok)
departures Plac Grunwaldzki 2026-11-16 from 08:00 line 16: 16 08:01 → TARNOGAJ [W:4898]; 16 08:11 → OSOBOWICE [W:4897]; 16 08:13 → TARNOGAJ [W:4898] (operator: MPK Tramwaje)
departures Galeria Dominikańska 2026-11-16 from 12:00 line D: D 12:09 → OSIEDLE SOBIESKIEGO [W:546 / 05]; D 12:09 → GIEŁDOWA (Centrum Hurtu) [W:1330 / 03]; D 12:24 → OSIEDLE SOBIESKIEGO [W:546 / 05] (operator: MPK Autobusy)
departures Dworzec Główny 2026-11-16 from 22:30 line 23: 23 22:36 → KOWALE [W:3523]; 23 22:56 → KOWALE [W:3523]; 23 23:24 → KROMERA [W:3523] (operator: MPK Tramwaje)
trip "main station → old town": Wrocław Główny → Rynek, depart 2026-11-16 09:00
  09:00→09:18 (18 min, 0 transfers): walk 5 min | tram 8 [MPK Tramwaje] "KARŁOWICE" DWORZEC GŁÓWNY 09:05 → GALERIA DOMINIKAŃSKA 09:10 | walk 8 min
  09:01→09:21 (19 min, 0 transfers): walk 5 min | tram 5 [MPK Tramwaje] "KSIĘŻE MAŁE" DWORZEC GŁÓWNY 09:07 → GALERIA DOMINIKAŃSKA 09:12 | walk 9 min
  09:03→09:23 (20 min, 0 transfers): walk 4 min | bus 106 [MPK Autobusy] "PORT LOTNICZY" DWORZEC GŁÓWNY (Dworcowa) 09:07 → Renoma 09:13 | walk 10 min
trip "trip needing a transfer": PORT LOTNICZY → Hala Stulecia, depart 2026-11-16 10:00
  10:02→11:06 (64 min, 1 transfers): walk 1 min | bus 106 [MPK Autobusy] "DWORZEC GŁÓWNY (Dworcowa)" PORT LOTNICZY 10:03 → DWORZEC GŁÓWNY (Dworcowa) 10:40 | walk 2 min | bus 145 [MPK Autobusy] "BARTOSZOWICE" DWORZEC GŁÓWNY (Dworcowa) 10:48 → Hala Stulecia 11:01 | walk 6 min
  10:08→11:20 (72 min, 1 transfers): walk 0 min | bus 129 [MPK Autobusy] "POŚWIĘCKA (Ośrodek Zdrowia)" PORT LOTNICZY 10:09 → Kwiska 10:40 | walk 0 min | tram 10 [MPK Tramwaje] "BISKUPIN" Kwiska 10:44 → Hala Stulecia 11:15 | walk 6 min
  10:17→11:21 (64 min, 1 transfers): walk 1 min | bus 106 [MPK Autobusy] "DWORZEC GŁÓWNY (Dworcowa)" PORT LOTNICZY 10:18 → DWORZEC GŁÓWNY (Dworcowa) 10:55 | walk 2 min | bus 145 [MPK Autobusy] "BARTOSZOWICE" DWORZEC GŁÓWNY (Dworcowa) 11:03 → Hala Stulecia 11:16 | walk 6 min
trip "late night after 23:30": Rynek → KOZANÓW, depart 2026-11-16 23:40
  2026-11-17 00:04→2026-11-17 00:31 (27 min, 0 transfers): walk 6 min | bus 245 [MPK Autobusy] "PRACZE ODRZAŃSKIE" Rynek 2026-11-17 00:11 → KOZANÓW 2026-11-17 00:31 | walk 0 min
  2026-11-17 00:05→2026-11-17 00:39 (34 min, 0 transfers): walk 6 min | bus 253 [MPK Autobusy] "LEŚNICA" Rynek 2026-11-17 00:12 → Park Zachodni 2026-11-17 00:26 | walk 13 min
  2026-11-17 00:35→2026-11-17 01:14 (39 min, 0 transfers): walk 6 min | bus 243 [MPK Autobusy] "LEŚNICA" Rynek 2026-11-17 00:42 → Szybowcowa 2026-11-17 01:01 | walk 14 min
```

### Verdicts: Gdańsk, first itinerary, every transit leg checked on the operator's per-trip pages

| Trip | Legs checked | Verdict |
|---|---|---|
| Main station → Old Town 09:00 | The first option is a 14-minute walk (1.1 km), which is the natural answer. The alternatives' legs check out: bus 189 09:05 → Brama Wyżynna 09:07 (`kurs-189_20261116-2-2-16`) and bus 100 09:06 → Targ Chlebowy 09:12 (`kurs-100_20261112-2-1-5`). | **match, sensible** |
| Archikatedra Oliwska → Westerplatte 10:00 (transfer) | Tram 12 Obrońców Westerplatte 11:25 → Hucisko 11:55 (`kurs-012_20261116-2-12-41`). Bus 106 Brama Wyżynna 12:00 → Muzeum Westerplatte 12:41 (`kurs-106_20261024-2-3-9`). Departures and arrivals both match. | **match, sensible** |
| Dworzec Główny → Zaspa 23:40 (late night) | N1 Dworzec Główny 00:00 → Startowa 00:17. The page title reads "Rozkład jazdy na noc: Poniedziałek, 16.11.2026 r. Wtorek, 17.11.2026 r." (`rozklad-0N1_20261114-23-1`, `kurs-0N1_20261114-1-23-3`). | **match** |

Why the Westerplatte plan only starts at 11:16: the operator pages show bus 106 leaving Brama
Wyżynna at 10:40 and then not until 12:00. Reaching the 10:40 would mean leaving before 10:00.
Bus 138, the only other line to Westerplatte, leaves Śródmieście SKM at 13:17. So 12:48 is the
earliest arrival, and the planner shows the latest start that still makes it.

Three of these Gdańsk timetables took effect on or just before the query date: tram 12 and bus
189 from 16.11.2026, and N1 from 14.11.2026. Our answers match the new versions, because they come
from the same day's feed.

### Verdicts: Kraków (times not compared, see above)

| Trip | First itinerary | What could be checked | Verdict |
|---|---|---|---|
| Kraków Główny → Rynek Główny 09:00 | a 17-minute walk, listed first because tram 3 (Dworzec Główny Zachód 09:05 → Poczta Główna 09:09, 14 min door to door) saves only 3 min | Line 3 runs its permanent route past both stops (ZTP line list). Walking first is the sensible answer here. | times **could not verify**; route plausible |
| Wieliczka Rynek-Kopalnia → Bronowice Małe 10:00 (transfer) | bus 304 → Plac Inwalidów, then replacement bus 704 → Bronowice Małe, 57 min | ZTP notices 2216/2247 confirm 704 is the tram-replacement bus to Bronowice Małe, every 10 minutes off-peak (our 10:46/10:56/11:06), from the temporary stop "Plac Inwalidów 71" (our boarding platform 71) | times **could not verify**; route and stops consistent |
| Rynek Główny → Plac Centralny 23:40 (late night) | walk 11, night bus 662 Teatr Słowackiego 00:00 → Plac Centralny 00:26 | Line 662 runs to Plac Centralny on that corridor. No page says which nights 662 runs (as opposed to tram 62). | **could not verify** |

### Verdicts: Wrocław, first itinerary, transit legs checked on the city's pages

| Trip | Legs checked | Verdict |
|---|---|---|
| Wrocław Główny → Rynek 09:00 | Tram 8 Dworzec Główny 09:05 → Galeria Dominikańska 09:10. The city page for pole 20268, towards Karłowice, lists 09:**05**, 09:16, 09:30. 18 min door to door against a 23-minute walk. | **match, sensible** |
| Port Lotniczy → Hala Stulecia 10:00 (transfer) | Bus 106 Port Lotniczy 10:03 (page for pole 17530: 10:**03**, 10:18, 10:33). Bus 145 → Hala Stulecia 11:01 (page for pole 24501: 11:**01**, 11:16). The 145's boarding time at Dworzec Główny (Dworcowa), 10:48, is on a pole whose page wasn't fetched; its arrival matches. | **match** |
| Rynek → Kozanów 23:40 (late night) | Night bus 245 from Rynek 00:11, towards Pracze Odrzańskie. The page for pole 10103 lists 23:41, then **24:11** for every night. The 23:41 is out of reach after a 6-minute walk to the stop. | **match** |

## Manual test trip (2026-11-14)

The server was run locally with Claude as the MCP client: Gdańsk Główny → Spichrzowa on Sunday 15 Nov around
19:40, then Spichrzowa → Westerplatte on Monday 16 Nov morning, and both trips were drawn with
`render_map`. What came out of checking it:

| Finding | Verdict |
|---|---|
| Bus M32 from Dworzec Główny platform 18 at 19:42 (Sun) | ZTM's page (`rozklad-M32_20260926-1-1-dzien-20261115`) lists 19:02, **19:42**, 20:02 — **match** |
| Bus 106 from Długie Ogrody platform 02 at 09:25, 10:51, 12:11 (Mon) | ZTM's page (`rozklad-106_20261024-10-2-dzien-20261116`) lists 07:15, 07:52, 08:32, **09:25, 10:51, 12:11** — **match** |
| 106 trip 09:25 → Muzeum Westerplatte 09:55 | ZTM's trip page (`kurs-106_20261024-2-10-7-dzien-20261116`) shows 09:25 → 09:55, via Przeróbka/Siennicka and the Stogi estate — **match**. The long, looping route on the map is the bus's real route. |
| The M32 leg drawn with a loop north of the station | **Bug, fixed.** The shape starts at platform 07, loops to turn round and then passes platform 18; the leg was cut from the shape's first point. Now 539 m along the shape (512 m straight). |
| "Westerplatte" and the stop name printed on top of each other | **Fixed:** labels avoid each other. |
| Bus one stop + 12 min walk ranked above a 20 min walk | **Changed:** when transit saves under 5 min, the walk comes first, with a note. |
| A different first option here than in the first run | Starting point: "Gdańsk Główny" resolves to the station node on the platforms, 3.2 min from platform 18, so the 19:42 M32 is missed; from a point 1 min away the planner returns the first run's M32 option. |
| Walks to Spichrzowa cross the Motława | On mapped bridges (2–3 bridge edges), 0 m in water. |

## Walking honesty

Command: `pytest tests/test_gdansk_data.py -s` on the Gdańsk file.

The test walks each route edge by edge. On every edge not tagged as a bridge or tunnel, it samples a
point every ~5 m and checks it against the OSM water polygons. It fails if any stretch longer than
15 m lies in water, or if a route that must cross water uses no bridge.

As a control, the same checker reports 169 m of water for a straight line from the Nowy Port
lighthouse to Westerplatte, and 152 m for a straight line across the Motława.

| Route | Walking distance | Bridge edges used | Longest stretch in water off a bridge | Result |
|---|---|---|---|---|
| Nowy Port lighthouse → Westerplatte (the ferry is not walkable) | 16,564 m | 20 | 0 m | pass |
| Długie Pobrzeże → Ołowianka (Motława) | 909 m | 3 | 0 m | pass |
| Zielona Brama → Wyspa Spichrzów (Motława) | 318 m | 2 | 0 m | pass |
| Długi Targ → Stogi beach (Motława and Martwa Wisła) | 6,633 m | 9 | 0 m | pass |

The synthetic tests (`tests/test_walk.py`, `tests/test_plan.py`) also check that a river in a grid
is crossed only on its single bridge.

## Weight

Command: `python scripts/measure.py --date 2026-11-16`. It runs the real server (`python -m poland_transit_mcp`) over
stdio with gdansk, wroclaw and krakow loaded. That's 200 random `plan_trip` calls per city: origins and
destinations near random stops, times 05:30–23:50 on 2026-11-16, one in five as `arrive_by`. Then
30 each of `walk_route`, `stops_near` and `departures`. Latency is measured end to end at the MCP
client. Memory is the server process's `VmRSS`/`VmHWM` from `/proc`. The machine is a 4-vCPU Intel
Xeon 2.1 GHz cloud VM running Python 3.13.18, not a laptop.

| | Measured | Budget | |
|---|---|---|---|
| City files | Warszawa 129.9 · Katowice 112.7 · Kraków 77.0 · Poznań 55.9 · Wrocław 45.7 · Gdańsk 35.6 · Toruń 22.9 · Zakopane 5.7 MB | ≤ 150 MB | ok |
| Cold start, 5 runs, Gdańsk + Wrocław + Kraków | 943, 813, 835, 933, 988 ms (max 988) | ≤ 3 s | ok |
| Cold start, 5 runs, Warszawa + Katowice + Kraków | 899, 936, 976, 1,099, 765 ms (max 1,099) | ≤ 3 s | ok |
| Memory after the query mix, Gdańsk + Wrocław + Kraków | 368 MB RSS (peak 368 MB) | ≤ 512 MB | ok |
| Memory after the query mix, Warszawa + Katowice + Kraków | 276 MB RSS (peak 438 MB), Warszawa and Katowice unloaded in turn | ≤ 512 MB | ok |
| `plan_trip`, 600 calls, Gdańsk + Wrocław + Kraków | p50 95 ms · p95 169 ms · max 264 ms | p95 < 300 ms | ok |
| `plan_trip`, 600 calls, Warszawa + Katowice + Kraków | p50 162 ms · p95 377 ms · max 582 ms | p95 < 300 ms | **over** |
| `plan_trip` per city, p95 | Gdańsk 113 · Wrocław 191 · Kraków 157–166 · **Warszawa 370 · Katowice 417** ms | | the two largest are over 300 ms |
| First `plan_trip` after a city loads | Gdańsk 120 · Wrocław 127 · Kraków 183–823 · Warszawa 368 · Katowice 1,891 ms | | includes loading when the city had been unloaded |
| `walk_route` / `stops_near` / `departures` p95 | 118 / 10 / 5 ms (default three) | | |

**All eight cities in one process** (`--cities gdansk,warszawa,krakow,katowice,wroclaw,poznan,torun,zakopane --trips 50 --memory-budget 480`):

| | Measured |
|---|---|
| Peak memory | **420 MB** (cities unloaded in turn: Gdańsk and Warszawa, then Kraków, then Katowice) |
| Cold start, worst of 5 | 977 ms |
| `plan_trip` p95 | Zakopane 16 · Toruń 81 · Gdańsk 115 · Kraków 164 · Wrocław 199 · Poznań 200 · Warszawa 345 · Katowice 460 ms (all 400 calls: 309 ms) |
| First query after a city loads | Zakopane 23 · Gdańsk 121 · Toruń 165 · Warszawa 397 · Wrocław 766 · Kraków 769 · Poznań 929 · Katowice 1,809 ms |
| Errors | 0 |

Measured 2026-11-16 with `scripts/measure.py --memory-budget 480` (`--cities warszawa,katowice,krakow`
for the second set), in the final configuration: cities load on demand, and large frees are not
forced back to the OS. Toruń + Zakopane together: 147 MB, 0 errors. This VM's run-to-run noise is
about ±15 % for latency.

**How memory got here.**
- **Over budget at first.** Warszawa, Kraków and Poznań loaded together first measured **535 MB**.
- **Freed memory kept in the process.** Profiling Warszawa showed that most of the growth during queries was memory already freed, which glibc kept: 60 trip plans took RSS from 231 to 280 MB, and `malloc_trim` brought it back to 220 MB. The server now trims the heap in a background thread after each burst of tool calls (`memory.py`). Trimming before each answer cost Warszawa about 40 ms of p95, so it runs off the response path.
- **On-demand loading.** Cities now load when first asked for and are unloaded least recently used first beyond `--memory-budget` (`pool.py`, [SCALING.md](SCALING.md)). Before that, loading Warszawa, Katowice and Kraków at start reached 597 MB.
- **Large frees returned at once.** Forcing large frees straight back to the OS kept memory lower (peak 456 MB for the three largest, with both large cities loaded together). But trip planning was ~25 % slower: Gdańsk averaged 88 vs 69 ms in three alternating runs. It is opt-in.

The first measurement used **576 MB** of memory, over the budget. The walking graph's k-d
tree was replaced by a grid index, trip times moved from Python lists to int32 arrays, and each city
keeps 2 cached days instead of 3; then it fell to 401 MB (Wrocław still without timetables).

What the 600 plans returned:

| City | Transit option found | Walk only | Nothing |
|---|---|---|---|
| Gdańsk | 191 | 0 | 9 |
| Kraków | 189 | 0 | 11 |
| Wrocław | 197 | 0 | 3 |
| Warszawa | 179 | 0 | 21 |
| Poznań | 168 | 1 | 31 |
| Toruń (100 trips) | 89 | 1 | 10 |
| Zakopane (100 trips) | 92 | 8 | 0 |

The empty answers are trips across the whole agglomeration. They carry the note "no connection
with ≤2 transfers within 4 h". No call returned an error.

Build times, `poland-transit-mcp build <city>` with inputs already on disk:

| City | Build time |
|---|---|
| Zakopane | 15 s |
| Toruń | 25 s |
| Gdańsk | 37 s |
| Wrocław | 44 s |
| Poznań | 49 s |
| Kraków | 75 s |
| Warszawa | 124 s |

Measured 2026-11-15, on the same machine as above.

## Rust engine (optional)

Checked on 2026-11-16.

**Same answers.**
- Every acceptance query (`scripts/acceptance.py`) gives byte-identical output with the engine and with `POLAND_TRANSIT_ENGINE=python`.
- `scripts/compare_engines.py --plans 100` ran on Warszawa, Katowice, Kraków and Gdańsk: **400 / 400** full `plan_trip` answers identical.
- `tests/test_engine.py` compares both engines on the synthetic city. It covers a night line, an arrive-by search, a Saturday timetable, a looping shape and a frequency-only line.
- To get exact equality, the Python search now breaks ties in the order stops were reached, as the engine does. Before, it followed hash-set order. Compared with the earlier Python search, one Kraków night alternative changed to the other route with the same times and the same number of changes.

**Speed, in process** (`compare_engines.py`, the same 100 plans per city on both engines, `plan_trip` time without the MCP transport).

On the 4-vCPU cloud VM used for "Weight" above (the README chart, `docs/engine-speed.svg`, shows these p95 values):

| City | Python p50 / p95 | Rust p50 / p95 |
|---|---|---|
| Warszawa | 223 / 375 ms | 57 / 118 ms |
| Katowice | 217 / 326 ms | 54 / 85 ms |
| Kraków | 85 / 149 ms | 30 / 65 ms |
| Gdańsk | 80 / 117 ms | 20 / 44 ms |

On an Apple Silicon Mac, 2026-11-17: `scripts/compare_engines.py --cities gdansk,warszawa,katowice --plans 100 --date 2026-11-16`
(city files built 2026-11-16; "total" is the Python time over all 100 plans divided by the Rust time):

| City | Identical answers | Python p50 / p95 | Rust p50 / p95 | Total time, Python ÷ Rust |
|---|---|---|---|---|
| Gdańsk | 100 / 100 | 25 / 45 ms | 8 / 30 ms | 2.3× |
| Warszawa | not recorded | 79 / 116 ms | 18 / 36 ms | 3.1× |
| Katowice | not recorded | 78 / 132 ms | 18 / 32 ms | 3.4× |

**End to end over MCP** (`scripts/measure.py --memory-budget 480`, engine installed):

| | Measured | Budget | |
|---|---|---|---|
| Warszawa + Katowice + Kraków | `plan_trip` p95 89 ms (Warszawa 91, Katowice 91, Kraków 84); peak 395 MB; cold start ≤ 797 ms | p95 < 300 ms, ≤ 512 MB | ok |
| Gdańsk + Wrocław + Kraków | `plan_trip` p95 80 ms (Gdańsk 59, Wrocław 91, Kraków 83); peak 358 MB; cold start ≤ 953 ms | | ok |
| All eight cities | `plan_trip` p95 77 ms (Zakopane 15 · Toruń 53 · Gdańsk 58 · Kraków 63 · Wrocław 69 · Poznań 77 · Katowice 83 · Warszawa 91); peak 432 MB; 0 errors | | ok |

**Memory.**
- With the engine, a day's routing tables go to Rust as flat arrays built directly. Warszawa uses 144 MB in all, against 159 MB with the Python search.
- A first version copied the Python arrays and then freed them. It reached 161 MB, and the freed fragments stayed in the process.
- A warm-up that preloaded a second large city while queries ran pushed the peak to 574–575 MB. Warm-up now preloads a city only if its expected size fits.

**Packaging, in clean environments.**
- **CI jobs replayed** from a clean checkout (`git worktree`) on 2026-11-16, with the test suite of that day (it has grown since; current counts are in the README):
  - The plain job, with no Rust on `PATH`, uses the Python search: 50 passed, 7 skipped.
  - The engine job (`uv sync --extra fast`) compiles the engine from `engine/`: 52 passed, 5 skipped.
- **One real problem found and fixed.** The `[fast]` extra made `uv sync` fail while the engine is not on PyPI, because uv resolves every extra. `tool.uv.sources` now points the engine at `engine/` for development.
- **Wheels:** maturin with zig built manylinux2014 wheels for x86-64 (234 KB) and ARM64 (225 KB). Each is one abi3 wheel per platform, for CPython 3.11 and later.
- **`uvx`, fresh isolated environments**, asking `list_cities` and a Gdańsk `plan_trip` over MCP (the engine was version 0.1.0 then):

  | Install | Search used | Answer |
  |---|---|---|
  | main package only | python | 10:36 → 11:28, bus 106 |
  | `--with` the manylinux2014 wheel | rust 0.1.0 | same |
  | `poland-transit-mcp[fast]` with the engine on a package index (`--find-links` standing in for PyPI) | rust 0.1.0 | same |
  | `--with ./engine`, compiled from source | rust 0.1.0 | same |

- **Workflows:** `actionlint` passed on the workflows as they were on 2026-11-16. `city-data.yml` has changed since (its build check) and was only checked as valid YAML.
- **Not checked:** the macOS and Windows wheels, which can only be built on those systems (`engine-wheels.yml` dry run).

## Memory budget for an MCP entry with Gdańsk, Warszawa and Katowice

A check of the MCP entry `--cities gdansk,warszawa,katowice` found first calls of 550–800 ms.
The three files add up to about 276 MB, over the 250 MB of city data that the default 480 MB budget
allows. Measured on 2026-11-16 with `scripts/measure.py --cities gdansk,warszawa,katowice` (Rust engine):

| `--memory-budget` | Cities one after another (100 trips each) | Cities interleaved on every query (`--interleave`, 180 trips) |
|---|---|---|
| 480 (default) | p95 81 ms, peak 445 MB, 7 unloads | **p50 1,085 / p95 1,657 ms**, 120 trips over 500 ms, 125 unloads, peak 550 MB |
| 600 | p95 78 ms, peak 539 MB, 0 unloads | **p50 47 / p95 93 ms**, 1 trip over 500 ms (Katowice's first load), 0 unloads, peak 536 MB |
| 700 | p95 70 ms, peak 575 MB, 0 unloads | — |

- **Interleaving is what an assistant does,** and at 480 MB every switch reloads a city: 1–2 s per trip. Even memory ends higher than at 600 MB, from the churn.
- **600 MB keeps all three loaded,** at 536 MB peak.
- **The server now says this at start:** `memory: these cities need about 600 MB to stay loaded together; … To keep them all: --memory-budget 600`. It prints nothing when the cities fit.
- **The default stays 480 MB,** so that a 512 MB container still works with any set of cities.
- **The MCP entry in AGENTS.md (step 6) passes `--memory-budget 600`** for these three cities, and `700` with `poland`. 700 is the measured value (see "Trips between cities"). The server's own hint depends on the set: it suggested 650 for Gdańsk, Kraków, Warszawa and `poland`. The start-up line prints the number for your set.

## Trips between cities (national layer)

Checked on 2026-11-16. Design and measurements: [INTERCITY.md](INTERCITY.md).

**Train times against an operator's own timetable.**
- The national layer's trains come from PKP PLK's data. Two operators publish GTFS of their own, so `scripts/compare_operator_feed.py` compared them on Wednesday 2026-11-18: trains matched by number, times compared at stations with the same name.
- **Koleje Małopolskie** (its own feed, valid from 2026-10-22): all **202** trains matched; **4,200 of 4,202** stop times identical. The two others are a 5-minute stop at Słomniki that PKP PLK's data does not show.
- **PolRegio:** the only copy available is mkuran.pl's, with files dated **2026-04-28**, half a year older than PKP PLK's data. 2,140 of 2,257 trains matched by number; departures identical at 33 % of stops and within 1 min at 47 %. A six-month-old timetable cannot decide which is right, so this counts as not checked.
- **Not checked:** PKP's own planner (portalpasazera.pl) and intercity.pl, rozklad-pkp.pl, koleo.pl, bilkom.pl and flixbus.pl.

**FlixBus time conversion.**
- Checked against the raw feed: Warszawa Chopin Airport → Białystok leaves 13:05 UTC on summer dates and 14:05 UTC on winter dates. Both are 15:05 in Poland, and the import stores them as one run.
- Synthetic tests check +1 h in winter, +2 h in summer, a run reaching Poland after midnight UTC, that a coach serving Poland keeps its stops abroad on Polish time, that a stop on another clock is dropped, and that a coach that never reaches Poland is dropped.

**Example trips.**

Planned with the national file of 2026-11-16, in process, Rust engine. City legs show line and
times; train and coach legs show category, train, operator, platform and track. Walks are left out.
Times are PKP PLK's and FlixBus's published data, not checked against the operators' websites
(see "Not checked" above), except the two Koleje Małopolskie legs, which match its own
timetable exactly (33226/7 Kraków Główny 12:08 → Grzegórzki 12:10; 39517 12:25 → 12:27).

<details>
<summary>Ten example trips</summary>

- **Gdańsk, Archikatedra Oliwska → Kraków, Rynek Główny** (depart at 2026-11-17 08:00)
  - 08:28 → 14:37 (369 min, 0 changes, walk 2696 m): EIP 8300/1 [PKP Intercity] 08:50 Gdańsk Oliwa (pl. 1, tr. 2) → 14:15 Kraków Główny (pl. 5, tr. 10)
  - 09:22 → 16:22 (420 min, 1 changes, walk 2696 m): EIP 5402/3 [PKP Intercity] 09:44 Gdańsk Oliwa (pl. 1, tr. 2) → 15:05 Katowice (pl. 1, tr. 9); EC/IC 59 Galicja [PKP Intercity] 15:14 Katowice (pl. 2, tr. 1) → 16:00 Kraków Główny (pl. 2, tr. 5)
  - 10:22 → 16:49 (387 min, 0 changes, walk 2696 m): EIP 5302/3 [PKP Intercity] 10:44 Gdańsk Oliwa (pl. 1, tr. 2) → 16:27 Kraków Główny (pl. 5, tr. 10)
- **Warszawa, Pałac Kultury i Nauki → Zakopane, Krupówki** (depart at 2026-11-17 06:00)
  - 07:25 → 12:38 (312 min, 0 changes, walk 2023 m): EIC 1350/1 Tatry [PKP Intercity] 07:40 Warszawa Centralna (pl. 4, tr. 6) → 12:17 Zakopane (pl. 2, tr. 3)
  - 08:25 → 14:06 (340 min, 1 changes, walk 2239 m): EIP 5300/1 [PKP Intercity] 08:40 Warszawa Centralna (pl. 3, tr. 2) → 11:10 Kraków Główny (pl. 3, tr. 2); FlixBus 1257A [FlixBus] 11:45 Krakow, MDA Bus Station → 13:45 Zakopane, Bus station
- **Poznań, Stary Rynek → Toruń, Dom Mikołaja Kopernika** (depart at 2026-11-18 09:00)
  - 10:21 → 12:27 (126 min, 2 changes, walk 1200 m): tram 17 10:26–10:35; IC 7514/5 Gopło [PKP Intercity] 10:46 Poznań Główny (pl. 1, tr. 16) → 12:08 Toruń Główny (pl. 3, tr. 5); bus 27 12:18–12:22
  - 11:32 → 14:13 (160 min, 1 changes, walk 2046 m): R 75316/7 [PolRegio] 11:54 Poznań Garbary (pl. 2, tr. 2) → 13:49 Toruń Kluczyki (pl. 3, tr. 303); bus 21 13:59–14:08
  - 12:21 → 14:29 (128 min, 2 changes, walk 1200 m): tram 17 12:26–12:35; IC 7106/7 Ukiel [PKP Intercity] 12:46 Poznań Główny (pl. 1, tr. 16) → 14:08 Toruń Główny (pl. 3, tr. 5); bus 27 14:20–14:24
- **Wrocław, Hala Stulecia → Poznań, Stary Rynek** (depart at 2026-11-19 07:30)
  - 08:20 → 11:00 (160 min, 1 changes, walk 3163 m): bus 146 08:26–08:44; IC 4510/1 Heweliusz [PKP Intercity] 08:57 Wrocław Główny (pl. 1, tr. 1) → 10:29 Poznań Główny (pl. 11, tr. 56)
  - 09:26 → 12:00 (154 min, 1 changes, walk 2915 m): bus 146 09:32–09:45; IC 3804/5 Malczewski [PKP Intercity] 09:55 Wrocław Główny (pl. 3, tr. 5) → 11:29 Poznań Główny (pl. 10, tr. 54)
  - 10:34 → 13:00 (146 min, 1 changes, walk 3014 m): bus 145 10:40–10:55; EC/IC 264 Baltic Express [PKP Intercity] 11:10 Wrocław Główny (pl. 1, tr. 1) → 12:29 Poznań Główny (pl. 8, tr. 51)
- **Kraków, Rynek Główny → Zakopane, Krupówki** (depart at 2026-11-17 08:00)
  - 08:33 → 11:33 (179 min, 1 changes, walk 1622 m): IC 1312/3 Malinowski [PKP Intercity] 08:56 Kraków Główny (pl. 3, tr. 2) → 11:22 Zakopane (pl. 2, tr. 3); bus 10 11:30–11:33
  - 09:51 → 12:38 (166 min, 0 changes, walk 2646 m): EIC 1350/1 Tatry [PKP Intercity] 10:14 Kraków Główny (pl. 3, tr. 1) → 12:17 Zakopane (pl. 2, tr. 3)
  - 11:22 → 14:06 (164 min, 0 changes, walk 2706 m): FlixBus 1257A [FlixBus] 11:45 Krakow, MDA Bus Station → 13:45 Zakopane, Bus station
- **Toruń, Dom Mikołaja Kopernika → Gdańsk, Dwór Artusa** (depart at 2026-11-20 10:00)
  - 10:02 → 13:00 (177 min, 1 changes, walk 1851 m): bus 27 10:08–10:13; IC 4520/1 Hutnik [PKP Intercity] 10:22 Toruń Główny (pl. 2, tr. 1) → 12:39 Gdańsk Główny (pl. 2, tr. 1)
  - 10:06 → 14:01 (234 min, 1 changes, walk 2777 m): R 55214/5 [PolRegio] 10:30 Toruń Miasto (pl. 1, tr. 2) → 11:07 Inowrocław (pl. 4, tr. 5); IC 4510/1 Heweliusz [PKP Intercity] 11:38 Inowrocław (pl. 2, tr. 4) → 13:40 Gdańsk Główny (pl. 2, tr. 1)
  - 10:29 → 14:01 (211 min, 2 changes, walk 1956 m): bus 12 10:35–10:40; IC 2520/1 Kopernik [PKP Intercity] 10:52 Toruń Główny (pl. 2, tr. 1) → 11:28 Bydgoszcz Główna (pl. 2, tr. 4); IC 4510/1 Heweliusz [PKP Intercity] 12:07 Bydgoszcz Główna (pl. 4, tr. 11) → 13:40 Gdańsk Główny (pl. 2, tr. 1)
- **Gdańsk, Dworzec Główny → Kraków, Wawel** (depart at 2026-11-18 21:00)
  - 23:38 → 2026-11-19 08:34 (536 min, 1 changes, walk 1708 m): TLK 83170/1 Ustronie [PKP Intercity] 23:48 Gdańsk Główny (pl. 1, tr. 2) → 2026-11-19 08:06 Kraków Główny (pl. 4, tr. 6); tram 12 2026-11-19 08:19–2026-11-19 08:25
  - 2026-11-19 05:45 → 2026-11-19 11:37 (351 min, 1 changes, walk 1753 m): EIP 5300/1 [PKP Intercity] 2026-11-19 05:55 Gdańsk Główny (pl. 1, tr. 2) → 2026-11-19 11:10 Kraków Główny (pl. 3, tr. 2); tram 3 2026-11-19 11:20–2026-11-19 11:24
  - 2026-11-19 08:52 → 2026-11-19 14:48 (357 min, 0 changes, walk 2675 m): EIP 8300/1 [PKP Intercity] 2026-11-19 09:02 Gdańsk Główny (pl. 1, tr. 2) → 2026-11-19 14:15 Kraków Główny (pl. 5, tr. 10)
- **Toruń, Dom Mikołaja Kopernika → Zakopane, Krupówki** (depart at 2026-11-20 06:00)
  - 06:02 → 14:23 (501 min, 3 changes, walk 1052 m): bus 22 06:08–06:13; IC 5334/5 Kazimierz [PKP Intercity] 06:21 Toruń Główny (pl. 1, tr. 2) → 11:38 Kraków Główny (pl. 2, tr. 5); FlixBus 1365 [FlixBus] 12:15 Krakow, MDA Bus Station → 14:10 Zakopane, Bus station; bus 14 14:20–14:23
  - 06:58 → 14:46 (468 min, 3 changes, walk 843 m): bus 22 07:04–07:09; IC 8336/7 Noteć [PKP Intercity] 07:16 Toruń Główny (pl. 1, tr. 2) → 09:28 Warszawa Centralna (pl. 1, tr. 7); EIC 5350/1 Kaszub [PKP Intercity] 09:40 Warszawa Centralna (pl. 4, tr. 6) → 14:34 Zakopane (pl. 2, tr. 3); bus 11 14:43–14:46
  - 07:54 → 16:38 (524 min, 4 changes, walk 843 m): bus 80 08:00–08:05; IC 5130/1 Brda [PKP Intercity] 08:16 Toruń Główny (pl. 1, tr. 2) → 10:16 Warszawa Centralna (pl. 1, tr. 5); EIP 1304/5 [PKP Intercity] 10:44 Warszawa Centralna (pl. 3, tr. 2) → 13:13 Kraków Główny (pl. 5, tr. 10); TLK 83106/7 Osterwa [PKP Intercity] 13:42 Kraków Główny (pl. 2, tr. 3) → 16:24 Zakopane (pl. 2, tr. 3); bus 14 16:35–16:38
- **Katowice, Spodek → Warszawa, Pałac Kultury i Nauki** (arrive by 2026-11-19 14:00)
  - 08:44 → 13:34 (290 min, 1 changes, walk 2444 m): IC 6300/1 Siemiradzki [PKP Intercity] 09:11 Katowice (pl. 2, tr. 1) → 09:59 Kraków Główny (pl. 2, tr. 5); EIP 3552/3 [PKP Intercity] 10:51 Kraków Główny (pl. 5, tr. 10) → 13:20 Warszawa Centralna (pl. 1, tr. 5)
  - 08:29 → 11:38 (189 min, 0 changes, walk 2444 m): EIP 4506/7 [PKP Intercity] 08:56 Katowice (pl. 2, tr. 3) → 11:24 Warszawa Centralna (pl. 2, tr. 1)
  - 07:50 → 11:22 (211 min, 2 changes, walk 1630 m): bus M2 07:59–08:24; bus 640 08:27–08:30; IC 111 Silesia [PKP Intercity] 08:37 Dąbrowa Górnicza (pl. 2, tr. 2) → 11:08 Warszawa Centralna (pl. 2, tr. 1)
- **Wrocław, Hala Stulecia → Kraków, Wawel** (depart at 2026-11-21 08:00)
  - 08:13 → 12:32 (260 min, 2 changes, walk 2564 m): bus 146 08:19–08:33; FlixBus 1365 [FlixBus] 08:45 Wroclaw, Bus Station → 12:00 Krakow, MDA Bus Station; SKA1 33226/7 [Koleje Małopolskie] 12:08 Kraków Główny (pl. 1, tr. 11) → 12:10 Kraków Grzegórzki (pl. 1, tr. 1)
  - 08:37 → 12:49 (252 min, 2 changes, walk 2171 m): tram 4 08:43–08:58; IC 8318/9 Mehoffer [PKP Intercity] 09:10 Wrocław Główny (pl. 3, tr. 6) → 12:16 Kraków Główny (pl. 1, tr. 11); SKA2 39517 [Koleje Małopolskie] 12:25 Kraków Główny (pl. 1, tr. 9) → 12:27 Kraków Grzegórzki (pl. 1, tr. 1)
  - 08:49 → 13:37 (289 min, 3 changes, walk 3220 m): bus 145 08:55–09:09; IC 6202/3 Bolko [PKP Intercity] 09:22 Wrocław Główny (pl. 2, tr. 3) → 10:44 Lubliniec (pl. 2, tr. 3); Os 40558 [Koleje Śląskie] 10:51 Lubliniec (pl. 2, tr. 5) → 11:26 Częstochowa (pl. 3, tr. 11); IC 7320/1 Cegielski [PKP Intercity] 11:31 Częstochowa (pl. 2, tr. 5) → 13:04 Kraków Główny (pl. 2, tr. 5)

</details>

What they show:
- **Platforms and tracks** appear on every train leg, and every leg names its operator.
- **The night train:** Gdańsk 21:00 → TLK Ustronie 23:48 → Kraków 08:06, then a tram the next morning. The city part uses the next day's timetable.
- **Regional changes:** PolRegio to Inowrocław, then IC Heweliusz; Koleje Śląskie between two IC trains via Lubliniec and Częstochowa.
- **Train + coach:** Warszawa → Kraków by EIP, then FlixBus to Zakopane; Toruń → Zakopane likewise.
- **arrive_by:** Katowice → Warszawa by 14:00. The latest departure (08:44) goes via Kraków: there is no direct train between 08:56 and 11:21 that arrives in time. Listing the direct trains confirmed this.
- **Observed, not changed:** Wrocław → Wawel's first option ends with a 2-minute Koleje Małopolskie hop to Kraków Grzegórzki, because it arrives 3 minutes earlier than the alternatives. Options are ranked by arrival, then changes; a person might prefer one change fewer for 3 minutes.

**Answers are the same with either search.**
- `tests/test_intercity.py` plans the synthetic trips with the Rust engine and with the Python search, and gets identical answers.
- `tests/test_engine.py` does the same for the one-to-all search.

**Speed and memory over MCP.**

`scripts/measure_intercity.py --cities gdansk,krakow,warszawa --trips 20 --date 2026-11-18`
(120 random trips between them plus the first of each pair, one in five `arrive_by`, Wednesday 2026-11-18):

| | Rust, `--memory-budget 700` | Rust, default 480 | Python search, 700 | Budget |
|---|---|---|---|---|
| Found a train or coach | 126 / 126 | 126 / 126 | 126 / 126 | |
| `plan_trip` p50 / p95 / max | **114 / 372** / 1,045 ms | 234 / 2,583 / 3,070 ms | 859 / 2,013 / 2,249 ms | p95 < 1 s |
| Peak memory | 573 MB | 532 MB | 650 MB | |
| Errors | 0 | 0 | 0 | |

- **Rust at 700 MB meets the 1 s budget.**
- **At the default 480 MB,** the three cities and `poland` do not fit. Warszawa and Kraków were unloaded and reloaded through the run, and the server now says so at start, with the budget that keeps them (here 650 MB).
- **With the Python search,** p95 is 2.0 s: over budget. The one-to-all searches on Warszawa and on the national layer are the cost.
- **Before two optimisations,** the same Rust run gave p50 / p95 143 / 450 ms. Building legs only for the options kept, and choosing the boarding station with one search, made trips from Warszawa 44 % faster in process (mean 391 → 218 ms).
- **The national layer in memory:** 51 MB estimated, 60 MB of RSS for one day with both search tables. That is 3.5× its file, so the pool now reserves 4× its file size for it.

## OpenTelemetry (opt-in)

Checked on 2026-11-16. Full write-up: [OBSERVABILITY.md](OBSERVABILITY.md).

- **One trace per call.**
  - The MCP SDK's `tools/call` span is the parent of the tool span, across the worker thread.
  - Every city load, walking search and RAPTOR run nests under it.
  - Checked in `tests/test_telemetry.py` and on the wire.
- **On the wire:**
  - The real server over stdio (Gdańsk and Kraków files, Rust engine) exported over OTLP/HTTP to a local receiver.
  - Three trip plans by place name and one bad request gave four traces, nine metrics, and the unload events the 330 MB budget caused.
- **No place names or coordinates recorded.**
  - The tests plan trips by place name and by coordinates, make one fail with an unknown place, and search every span, event and status.
  - The wire export was searched the same way.
- **One real problem found and fixed:** histogram buckets. The SDK's defaults are made for milliseconds; with durations in seconds, every call fell in the first bucket. The buckets are now set in seconds, and a test checks them.
- **Cost** (`scripts/measure.py`, 600 trips, two runs each, alternating):

  | | Off | Exporting |
  |---|---|---|
  | `plan_trip` p50 | 21–22 ms | 23 ms |
  | `plan_trip` p95 | 61–66 ms | 64–67 ms |
  | Memory after the queries | 349–351 MB | 351–383 MB |
  | Cold start (max of 5) | 806–837 ms | 881–899 ms |

  Counting the walking nodes each search reached had cost +3–4 ms per plan, so it was removed.
- **Collector down:** every call still succeeds; a few `Transient error` lines go to stderr, and exit takes ~2 s longer.
- **Console exporter over stdio:** spans go to stderr, and the MCP client parsed every reply.
- **Install:**
  - `uvx --from "poland-transit-mcp[otel] @ …"` turns it on.
  - Without the extra, the server logs that the SDK is missing and runs normally.

## Fresh install, offline

1. `git clone` into an empty directory, then `uv sync`. This is online: it fetches `mcp`, `numpy` and `scipy` from PyPI.
2. First start with an empty data directory: `poland-transit-mcp --cities gdansk,krakow --data-dir <empty dir>`. The prebuilt `*.sqlite.gz` files were served from a local directory standing in for GitHub Releases (`POLAND_TRANSIT_DATA_URL`). The server downloaded both, unpacked them byte-identical to the built files (`cmp`), and answered `list_cities`. One command, no separate download step.
3. Inside a network namespace with no network at all (`unshare -n`; `curl https://pypi.org/` → `Could not resolve host`): `python scripts/measure.py --data-dir <dir> --cities gdansk,krakow --trips 100`. Result: **0 errors** in 200 `plan_trip` + 120 other calls, cold start ≤ 1,077 ms, 343 MB RSS, `plan_trip` p95 127 ms.
4. Packaging: `uv build` gives a 56 KB wheel. `uvx --from <wheel> poland-transit-mcp --cities gdansk` installed it with its dependencies and answered `plan_trip` correctly. `uvx --from git+https://github.com/…` uses the same mechanism; it was not tried.

So a fresh clone runs offline with only the prebuilt city files. Installing the dependencies needs
the network once.

## Not run

- **Kraków against the operator:** times not compared: the operator's timetable is served only through a supplier API (see above).
- **Which Wrocław time is right where the feed and the city website differ by a minute** (tram 23, above).
- **Poznań, Toruń and Warszawa against the operators:**
  - ZTM Poznań's timetable is a JavaScript application with no plain pages.
  - ZTM Warszawa's and MZK Toruń's timetable pages: not checked.
  - Their answers come from the official feeds, imported the same way as the checked cities, but no departure has been compared with the operator yet.
- **Comparison with a public journey planner** (e.g. jakdojade.pl): not checked. Trip plans are checked leg by leg against the operators' timetables instead.
