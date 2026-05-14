# OGN Pipeline Process

This document explains what happens when you run:

```bash
uv run fastapi
```

The command starts one Python process that contains three runtime parts:

1. the FastAPI web server
2. the live OGN/APRS collector
3. the incremental processor that cleans observations and builds tracks

The dashboard is served by the same FastAPI app and is refreshed from a background HTML cache.

## Startup

The command is defined in `pyproject.toml`:

```toml
[project.scripts]
fastapi = "src.cli:main"
```

So `uv run fastapi` calls `src.cli.main()`. That function starts Uvicorn with:

```python
uvicorn.run("src.router:app", host=env.host, port=env.port)
```

The `env` object is created in `src/envs.py` with `pydantic-settings`:

```python
env = Settings()
```

Settings are loaded from environment variables prefixed with `OGN_` and from `src/.env` when that file exists. For example:

```bash
OGN_PORT=8001 uv run fastapi
```

overrides the default server port.

## Application Lifespan

FastAPI loads `src.router:app`. During the app lifespan startup, `src/router.py` does this:

1. creates database tables if they do not exist
2. starts the dashboard cache thread
3. starts the OGN collector thread when `OGN_COLLECT_ON_STARTUP=true`
4. starts the processor thread when `OGN_PROCESS_ON_STARTUP=true`

By default both collection and processing start automatically.

On shutdown, FastAPI stops the processor, stops the collector, and stops the dashboard cache.

## Database

The default database is SQLite:

```text
sqlite:///data/live/ogn_live.db
```

The database setup lives in `src/db/session.py`.

When SQLite is used, the application:

- creates the parent database directory automatically
- enables WAL mode so the dashboard/API can read while the collector writes
- sets a busy timeout to reduce lock errors during concurrent access
- enables foreign key checks

The main tables are:

| Table | Purpose |
| --- | --- |
| `raw_messages` | Every saved APRS message exactly as received from OGN, plus parse status. |
| `position_observations` | Parsed OGN position messages with aircraft ID, timestamp, coordinates, speed, altitude, type, receiver, and flags. |
| `cleaned_observations` | Quality-filtered position observations ready for analysis. |
| `track_segments` | Continuous aircraft trajectories built from cleaned observations. |
| `track_points` | Individual cleaned observations assigned to a segment, with derived gap, distance, speed, heading, and climb. |
| `processing_state` | Incremental state, especially the last processed `position_observations.id`. |

## OGN Connection

The live collector is implemented in `src/cron/collector.py`.

It uses `ogn-client`, specifically:

```python
from ogn.client import AprsClient
```

At startup it builds an `AprsClient` with:

- `env.aprs_user`, default `N0CALL`
- `env.aprs_filter`, default `r/46.8/8.2/250`
- `env.aprs_server_host`, default `aprs.glidernet.org`

The default APRS filter:

```text
r/46.8/8.2/250
```

means a server-side regional filter centered around latitude `46.8`, longitude `8.2`, with radius `250 km`. In practice this asks the APRS/OGN server to stream traffic around Switzerland instead of the full global feed.

The collector connects with:

```python
self.client.connect(retries=100, wait_period=15)
```

Then it enters the `ogn-client` socket loop:

```python
self.client.run(callback=..., autoreconnect=True)
```

`ogn-client` owns the network socket. Every time a raw APRS line arrives, it calls our callback.

## Raw Collection Flow

For every raw APRS message, `handle_message()` runs this process:

1. increment the `seen` counter
2. parse the raw message with `ogn.parser.parse()`
3. if parsing fails, optionally save the raw message with `parse_status="parse_error"`
4. if parsing succeeds, optionally apply the collection profile filter
5. save the raw message in `raw_messages`
6. if the parsed message is a position beacon, save a row in `position_observations`
7. commit every `OGN_COMMIT_EVERY` messages

The default collection profile is:

```text
OGN_COLLECTION_PROFILE=all
```

That means the collector stores all parsable traffic from the configured APRS filter region.

If the profile is changed to:

```text
OGN_COLLECTION_PROFILE=unconventional
```

then the collector keeps only messages that look like unconventional FLARM/FANET/Naviter-style traffic. That filter excludes receiver beacons, excludes IDs that start with `ICA`, checks beacon type or ID prefix, and rejects very high or very fast outliers using:

- `OGN_MAX_ALTITUDE_M`
- `OGN_MAX_SPEED_KMH`

## Timestamp Handling

OGN parser timestamps can be timezone-aware or timezone-naive depending on the input. The collector normalizes timestamps before saving them.

`src/utils/time.py` converts timestamps to UTC ISO strings. If a timestamp is timezone-naive, the code treats it as UTC. If it is timezone-aware, it converts it to UTC.

This avoids errors like:

```text
can't subtract offset-naive and offset-aware datetimes
```

when the processor computes gaps between observations.

## Processing Flow

The processor is implemented in `src/cron/processor.py`.

It runs in a background thread when:

```text
OGN_PROCESS_ON_STARTUP=true
```

The processor is incremental. It reads:

```text
process_ogn_data.last_position_observation_id
```

from `processing_state`, then processes only `position_observations` with a higher ID.

Each processing cycle:

1. loads up to `OGN_PROCESSOR_BATCH_SIZE` new position observations
2. computes quality flags
3. decides whether each observation should be kept
4. writes valid rows to `cleaned_observations`
5. assigns each cleaned observation to a track segment
6. updates `processing_state`
7. commits the batch

If a full batch was processed, it immediately continues with another batch. When it catches up, it waits:

```text
OGN_PROCESSOR_INTERVAL_SECONDS
```

before checking for new data again.

## Quality Filtering

The processor gives each raw position observation quality flags.

Blocking flags always prevent an observation from entering `cleaned_observations`:

- `missing_aircraft_id`
- `invalid_timestamp`
- `missing_coordinates`
- `invalid_coordinates`

Other flags depend on settings:

- `outside_swiss_bbox`
- `receiver_beacon`
- `static_object`

By default, the processed layer keeps observations inside the Switzerland bounding box and excludes receivers and static objects.

Speed, altitude, and climb sanity checks add quality flags such as:

- `altitude_out_of_range`
- `negative_speed`
- `speed_out_of_range_for_type`
- `climb_rate_out_of_range`

Speed limits are aircraft-type aware, so slow glider traffic is preserved while obvious outliers are marked.

## Track Segmentation

After a row enters `cleaned_observations`, the processor assigns it to a trajectory.

For each aircraft ID, it finds the latest existing track point. Then it compares the new observation with the previous one.

A new segment starts when:

- there is no previous point for that aircraft
- the timestamp is non-monotonic
- the time gap is greater than `OGN_SEGMENT_GAP_SECONDS`
- the implied jump speed is greater than `OGN_MAX_JUMP_SPEED_KMH`

Otherwise the new point is appended to the existing segment.

For appended points, the processor calculates:

- time delta in seconds
- distance in meters
- estimated ground speed
- estimated heading
- estimated climb rate when both altitudes are available

The parent `track_segments` row is updated incrementally with:

- start and end timestamp
- number of points
- duration
- maximum gap
- distance
- min/max altitude
- average/max speed
- likely-unconventional marker

## Dropout Events and Dropout Grid

Dropout events and dropout grids are still computed.

They are not stored in separate tables. They are calculated for the dashboard snapshot from `cleaned_observations`.

The dashboard computes two related outputs:

1. `dropout_candidates`: individual gaps between consecutive observations from the same aircraft
2. `dropout_hotspots`: grid cells where dropout-like gaps are frequent

A candidate dropout must satisfy:

- same aircraft ID
- valid consecutive timestamps
- gap greater than or equal to `OGN_SEGMENT_GAP_SECONDS`
- gap less than or equal to `OGN_DASHBOARD_DROPOUT_MAX_GAP_SECONDS`
- distance at least `OGN_DASHBOARD_DROPOUT_MIN_DISTANCE_KM`
- implied speed less than or equal to `OGN_DASHBOARD_DROPOUT_MAX_IMPLIED_SPEED_KMH`
- both endpoints inside the configured Switzerland region

By default dropout analysis is limited to likely unconventional aircraft types. Set:

```text
OGN_DASHBOARD_INCLUDE_ALL_DROPOUT_AIRCRAFT=true
```

to include all aircraft types.

The dropout hotspot grid aggregates transitions into cells using:

```text
OGN_DASHBOARD_GRID_DEGREES
```

Each hotspot includes:

- grid center latitude/longitude
- transition count
- dropout count
- dropout rate
- unique aircraft count
- unique receiver count
- top receiver
- top receiver share
- top aircraft share
- dominant altitude band
- dominant aircraft type
- dominant beacon type
- average gap
- p95 gap
- average dropout gap
- p95 dropout gap
- max dropout gap
- a compact `bias_hint`

The `bias_hint` is not a final diagnosis. It is an interview/demo-friendly interpretation that helps explain what kind of missingness the grid cell may represent:

| Bias hint | Meaning |
| --- | --- |
| `receiver concentrated` | One receiver dominates the observations in that cell. The dropout pattern may be tied to receiver geometry, receiver uptime, or receiver placement. |
| `aircraft concentrated` | One aircraft explains a large share of transitions. The cell should be interpreted carefully because it may not generalize to the whole region. |
| `low altitude sensitive` | Most transitions are in low altitude bands, where terrain and line-of-sight coverage matter more. |
| `persistent spatial hotspot` | The cell has both many dropouts and a high dropout rate, making it a stronger candidate coverage-bias signal. |
| `mixed coverage signal` | No single simple explanation dominates. The cell may need deeper analysis. |

The dashboard shows dropout grids in three places:

1. as a map overlay
2. in the side-panel `Top Dropout Hotspots` table
3. in the dedicated `Dropout Grid Analysis` table

This is useful for an interview because it moves the dashboard from "there are missing points" to "here is where missingness may be spatial, receiver-driven, altitude-sensitive, or aircraft-specific."

## Dashboard

The dashboard route is:

```text
/
```

It is implemented in `src/routes/dashboard.py`.

The dashboard is not rebuilt on every browser request. Instead, a background `DashboardCache` thread periodically:

1. opens a database session
2. builds a full dashboard snapshot with fast SQL queries
3. renders complete HTML
4. stores the HTML in memory

The browser receives the cached HTML immediately.

This is controlled by:

```text
OGN_DASHBOARD_REFRESH_SECONDS=30
```

The dashboard keeps all-time status totals visible, but the interactive map,
aircraft mix, beacon mix, raw quality tracks, and dropout overlays use a recent
time window for speed:

```text
OGN_DASHBOARD_WINDOW_HOURS=24
```

The HTML page also contains:

```html
<meta http-equiv="refresh" content="30">
```

So the browser reloads the page automatically at the same interval. The dashboard is therefore auto-updating as long as the FastAPI process is running.

If the first dashboard snapshot is still being built, `/` returns a small loading page that refreshes every two seconds. Once the cache has a complete HTML snapshot, the full dashboard appears.

The dashboard includes:

- live collection/processor status
- latest OGN timestamp
- processor lag
- raw collection summary
- parsed position counts
- unique aircraft counts
- likely unconventional aircraft counts
- map-first observation density view
- browser-side aircraft type and class filters
- aircraft type counts
- beacon type counts
- cleaned observation counts
- track point and track segment counts
- good segment counts
- recent processed segments
- best unconventional trajectory candidates
- raw quality tracks
- dropout event overlay
- dropout hotspot grid overlay
- top dropout hotspot table
- dedicated dropout grid analysis table
- map inspector for clicked cells, dropout events, and hotspot grids

The map uses client-side JavaScript and Leaflet. The data needed by the map is embedded into the cached HTML snapshot, so filter changes in the browser do not call the server again.

Clicking a processed segment row calls:

```text
/api/segments/{segment_id}/points
```

to load the points for that segment preview.

The `Best Unconventional Trajectories` table also has a `View` button for each ranked trajectory. Pressing `View`:

1. fetches `/api/segments/{segment_id}/points`
2. draws the trajectory directly on the Leaflet map
3. adds start and end markers
4. zooms the map to the trajectory bounds
5. scrolls back to the map
6. updates the segment preview panel

The `View` buttons are generated automatically from the current best-trajectory rows. If a new processed segment becomes one of the best unconventional trajectories, it appears after the collector, processor, dashboard cache, and browser refresh cycles complete.

The best unconventional trajectory ranking is currently all-time. It scans processed `track_segments` globally so strong examples remain visible even if they did not happen in the most recent dashboard window.

## Dashboard Snapshot Queries

The dashboard query layer is in:

```text
src/db/functions/dashboard.py
```

It uses SQL directly through SQLAlchemy connections for speed. This avoids loading millions of ORM objects just to render the page.

The snapshot includes:

- `fetch_summary()`: raw and regional counts
- `fetch_density_cells()`: map grid cells from raw position observations
- `fetch_aircraft_type_counts()`: aircraft type table
- `fetch_beacon_counts()`: beacon type table
- `fetch_top_aircraft()`: busiest aircraft IDs
- `fetch_quality_tracks()`: recent high-continuity raw tracks
- `fetch_engineering_summary()`: processed table counts and segment summaries
- `fetch_best_trajectories()`: ranked unconventional trajectory candidates
- `fetch_dropout_candidates()`: individual dropout-like gaps
- `fetch_dropout_hotspots()`: aggregate dropout grid cells with receiver, aircraft, altitude, gap, and bias-hint fields

Most map-facing dashboard queries use the recent `OGN_DASHBOARD_WINDOW_HOURS` window. This keeps the dashboard responsive on a large historical SQLite database. The main status totals remain all-time, and best unconventional trajectories remain all-time.

## JSON API

The JSON API is mounted under:

```text
/api
```

Useful endpoints:

| Endpoint | Description |
| --- | --- |
| `/api/counts` | High-level database counts. |
| `/api/observations` | Latest parsed observations. |
| `/api/cleaned-observations` | Latest cleaned observations. |
| `/api/aircraft` | Aircraft ranked by observation count. |
| `/api/beacons` | Beacon type counts. |
| `/api/aircraft-types` | Aircraft type counts. |
| `/api/quality` | Processed quality and coverage summary. |
| `/api/density` | Geographic density cells. |
| `/api/coverage-gaps` | Coverage gap proxy cells. |
| `/api/segments` | Top processed track segments. |
| `/api/segments/{segment_id}/points` | Points for one processed segment. |

## Common Run Modes

Run everything with default settings:

```bash
uv run fastapi
```

Run on a different port:

```bash
OGN_PORT=8001 uv run fastapi
```

Run only the API and dashboard, without collecting new OGN data:

```bash
OGN_COLLECT_ON_STARTUP=false uv run fastapi
```

Run only the API and dashboard, without background processing:

```bash
OGN_PROCESS_ON_STARTUP=false uv run fastapi
```

Run the API/dashboard without collector or processor:

```bash
OGN_COLLECT_ON_STARTUP=false OGN_PROCESS_ON_STARTUP=false uv run fastapi
```

Use a different database:

```bash
OGN_DATABASE_URL=sqlite:///data/live/another_ogn.db uv run fastapi
```

Use a tighter dashboard refresh interval:

```bash
OGN_DASHBOARD_REFRESH_SECONDS=10 uv run fastapi
```

## Main Configuration Values

| Setting | Default | Purpose |
| --- | --- | --- |
| `OGN_DATABASE_URL` | `sqlite:///data/live/ogn_live.db` | Database location. |
| `OGN_APRS_USER` | `N0CALL` | APRS login user sent to the OGN server. |
| `OGN_APRS_FILTER` | `r/46.8/8.2/250` | Server-side APRS filter. |
| `OGN_APRS_SERVER_HOST` | `aprs.glidernet.org` | OGN/APRS server host. |
| `OGN_COLLECT_ON_STARTUP` | `true` | Start collector with FastAPI. |
| `OGN_PROCESS_ON_STARTUP` | `true` | Start processor with FastAPI. |
| `OGN_COMMIT_EVERY` | `100` | Collector commit batch size. |
| `OGN_PROCESSOR_INTERVAL_SECONDS` | `15` | Processor wait time after catching up. |
| `OGN_PROCESSOR_BATCH_SIZE` | `50000` | Max observations processed per batch. |
| `OGN_SEGMENT_GAP_SECONDS` | `60` | Gap threshold that starts a new segment and marks dropout-like gaps. |
| `OGN_MAX_JUMP_SPEED_KMH` | `1200` | Maximum plausible jump speed for segment continuity. |
| `OGN_DASHBOARD_REFRESH_SECONDS` | `30` | Dashboard cache and browser refresh interval. |
| `OGN_DASHBOARD_WINDOW_HOURS` | `24` | Recent time window used by interactive dashboard layers. |
| `OGN_DASHBOARD_GRID_DEGREES` | `0.05` | Map and dropout hotspot grid size. |
| `OGN_DASHBOARD_DROPOUT_LIMIT` | `3000` | Maximum individual dropout events embedded in dashboard HTML. |
| `OGN_DASHBOARD_DROPOUT_MIN_DISTANCE_KM` | `0.2` | Minimum distance between two observations for a gap to count as a dropout candidate. |
| `OGN_DASHBOARD_DROPOUT_MAX_GAP_SECONDS` | `600` | Maximum gap duration considered plausible for dropout analysis. |
| `OGN_DASHBOARD_DROPOUT_MAX_IMPLIED_SPEED_KMH` | `300` | Maximum implied speed allowed between the two points around a dropout gap. |
| `OGN_DASHBOARD_DROPOUT_HOTSPOT_MIN_TRANSITIONS` | `30` | Minimum transition count before a grid cell can appear as a dropout hotspot. |
| `OGN_DASHBOARD_INCLUDE_ALL_DROPOUT_AIRCRAFT` | `false` | Include all aircraft types in dropout analysis. |

## What To Watch In The Terminal

Collector messages look like:

```text
Listening to aprs.glidernet.org with filter r/46.8/8.2/250. Writing to sqlite:///data/live/ogn_live.db.
```

Then every collector commit batch prints counts:

```text
seen=... saved=... positions=... filtered=... errors=...
```

Processor messages look like:

```text
processed seen=... kept=... dropped=... new_segments=... appended=... last_position_id=...
```

Dashboard errors, if any, look like:

```text
dashboard_error=...
```

Processor errors, if any, look like:

```text
processor_error=...
```

## End-To-End Data Path

The full path from live OGN traffic to the dashboard is:

```text
OGN/APRS server
  -> ogn-client socket connection
  -> raw APRS message callback
  -> ogn.parser.parse()
  -> raw_messages
  -> position_observations
  -> processor quality checks
  -> cleaned_observations
  -> track_points and track_segments
  -> dashboard SQL snapshot
  -> cached rendered HTML
  -> browser dashboard at /
```

The collector keeps adding new raw and parsed rows. The processor keeps catching up with new parsed positions. The dashboard cache keeps rebuilding the HTML snapshot. The browser keeps refreshing the page. Together, that makes the displayed dashboard update automatically while `uv run fastapi` is running.
