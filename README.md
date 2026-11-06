# WxData — RRFS / GFS / RAP forecast viewer

A small, self-maintaining replacement for the model pages of TwisterData,
built after NOAA retired the NAM on 3 Nov 2026. It shows three NOAA models over
CONUS and southern Canada (Toronto, Ottawa, Montreal) as maps and as point
forecasts anywhere you click.

| Model | Role | Runs used | Forecast hours |
|---|---|---|---|
| **RRFS** (13 km) | NAM replacement | 00/06/12/18Z | hourly to 24 h, 3-hourly to 84 h |
| **GFS** (0.25°) | long range | 00/06/12/18Z | 3-hourly to 120 h, 6-hourly to 240 h, 12-hourly to 384 h |
| **RAP** (13 km) | short range | every hour | hourly to 21 h |

**Maps:** surface temperature, dewpoint and wind with MSLP; ceiling,
visibility and freezing level; simulated radar, precipitation, precipitable
water and CAPE; wind, temperature, dewpoint and RH with heights at
925/850/700/500/300/250 mb.

**Point forecast:** click the map, or search an airport (ICAO, FAA or IATA
code, or name) or `lat,lon`. You get three views:
- **Surface table:** T/Td, wind, ceiling, visibility (MVFR/IFR/LIFR colors), MSLP, precipitation, reflectivity, freezing level, CAPE.
- **Winds aloft:** FD-style table at 3,000 ft through FL390.
- **Sounding:** a Skew-T.

## How it works

There is no server to maintain. A GitHub Actions job runs every 30 minutes:

1. It looks for new model output on NOAA's AWS mirrors. NOMADS is the automatic fallback.
2. It downloads only the fields it needs, using the GRIB index files and byte-range requests.
3. It regrids everything onto a common lat/lon grid and rotates grid-relative winds to true north.
4. It packs the data into small compressed files and publishes them with the static site to GitHub Pages.
5. The browser draws the maps, contours, wind barbs, tables and soundings itself.

Reliability features:
- If a model run is late or a download fails, the site keeps showing the last good run.
- The status dot in the top bar turns amber or red when data is stale. Hover over it for details.
- Work in progress is cached between jobs, so nothing is downloaded twice.
- Each job stops starting new work after 35 minutes.
- Only the newest run of each model is kept, plus the previous run while the newest is still arriving. This keeps the site small, well under GitHub Pages' 1 GB limit.
- Each run's log is published to the repo's `status` branch for troubleshooting.

## Setup (one time, about 5 minutes)

1. **Create the repository** on GitHub, for example `ai-takeoff/wxdata`. It must be
   **public**, because GitHub Pages and unlimited Actions minutes are free only for
   public repos. The data is all public NOAA output.
2. **Add these files** to the repository's `main` branch.
3. **Turn on Pages:** go to Settings → Pages → Build and deployment → Source,
   and choose **GitHub Actions**.
4. **Start the first update:** go to Actions → *Update forecast data* → **Run workflow**.
   - The first run has a backlog to work through. It fills in RRFS first, then RAP, then GFS, and can take two or three scheduled runs (about an hour) to be complete.
   - After that, each run only processes what is new.
5. **Open the site** at `https://ai-takeoff.github.io/wxdata/`, or whatever
   your user/repo name is. The URL also appears in the workflow run summary.

GitHub emails the repo owner if a workflow run fails outright. Routine NOAA
delays are not failures: the job simply catches up on the next run.

## Using it

- **Model:** use the RRFS / GFS / RAP buttons. Switching models keeps the same valid time.
- **Run:** the newest run is chosen automatically, and older runs stay selectable. A run still coming in shows how many hours are available.
- **Time:** use the slider, the arrow buttons, or the ← / → keys. Space plays an animation.
- **Location:** search box, click the map, or the CONUS / NE / Lakes shortcuts.
- **Bookmarks:** the URL keeps model, product, view and selected point, so a bookmark reopens exactly where you were.

## Things to verify in the first week

These are the parts that could only be tested against simulated files in NOAA's
documented formats, not against live data:

- **Ceiling.**
  - RRFS has a dedicated `CEIL` field, used as AGL.
  - For GFS and RAP the code detects automatically whether the ceiling field is MSL or AGL. The method used is shown under the point table.
  - Compare it against a few METARs.
- **RRFS file location on AWS** (`noaa-rrfs-ops-pds`). If the 13 km North America files turn out not to be mirrored there, the job falls back to NOMADS automatically.

## Changing things

Everything is configured in `pipeline/config.py`:
- domain, models, forecast hours, levels
- field ranges
- number of runs kept

To change data sources without editing code, set a repository variable and pass it
as an environment variable in the workflow:

```
WX_SOURCES_RRFS="https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/prod/rrfs.{ymd}/{cc}/rrfs.t{cc}z.prslev.13km.f{fff}.na.grib2"
```

Map products, color scales and units are defined near the top of `site/app.js`, in `PRODUCTS` and `CMAPS`.

## Development

```
pip install -r requirements.txt
tools/selftest.sh          # synthetic NOAA-format files -> pipeline -> value checks
```

Other tools:
- `tools/make_test_data.py` generates test GRIB2 files. Set `WX_TEST_STYLE=realistic` for a cyclone/jet scenario that's useful for checking visuals.
- `tools/build_airports.py` refreshes `site/airports.json` from OurAirports (public domain).

## Credits

- Model data: NOAA / NCEP (public domain).
- Basemap: © OpenStreetMap contributors, © CARTO.
- Airports: OurAirports.

This is not an official weather briefing source.
