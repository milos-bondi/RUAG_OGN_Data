# RUAG OGN Data

FastAPI application for collecting Open Glider Network APRS traffic into SQLite,
cleaning and processing observations into track segments, and viewing the data
in a browser dashboard.

## Structure

```text
.
├── src/
│   ├── envs.py
│   ├── router.py
│   ├── cron/
│   ├── db/
│   ├── dtypes/
│   ├── routes/
│   ├── static/
│   ├── utils/
│   ├── .env.sample
│   └── pyproject.toml
├── AGENTS.md
├── README.md
└── pyproject.toml
```

Local collected data stays under `data/` and is ignored by Git.

## Install

This project is intended to be run with `uv`.

Install dependencies:

```bash
uv sync
```

Confirm the OGN client package:

```bash
uv run python -m pip show ogn-client
```

The project currently pins:

```text
fastapi>=0.100.0,<0.111.0
ogn-client==1.3.2
```

FastAPI is pinned below the FastAPI CLI package range so the repository's own
`uv run fastapi` entrypoint starts this application instead of the external
FastAPI CLI.

## Configure

Copy the sample environment file when local overrides are needed:

```bash
cp src/.env.sample src/.env
```

Default database:

```text
data/live/ogn_live.db
```

Important settings:

```text
OGN_DATABASE_URL=sqlite:///data/live/ogn_live.db
OGN_APRS_USER=N0CALL
OGN_APRS_FILTER=r/46.8/8.2/250
OGN_APRS_SERVER_HOST=aprs.glidernet.org
OGN_COLLECT_ON_STARTUP=true
OGN_PROCESS_ON_STARTUP=true
OGN_PROCESSOR_INTERVAL_SECONDS=15
OGN_PROCESSOR_BATCH_SIZE=5000
OGN_SEGMENT_GAP_SECONDS=60
OGN_MAX_JUMP_SPEED_KMH=1200
OGN_INCLUDE_OUTSIDE_SWISS=false
OGN_INCLUDE_RECEIVERS=false
OGN_INCLUDE_STATIC=false
OGN_HOST=127.0.0.1
OGN_PORT=8000
```

## Run Server, Collector, And Processor

Start the API, dashboard, live collector, and processing job with one command:

```bash
uv run fastapi
```

This command starts the FastAPI server, the OGN collection job, and the
incremental processor during application startup.

New APRS messages are appended to `raw_messages`; parsed positions are appended
to `position_observations`. The processor then creates:

```text
cleaned_observations
track_segments
track_points
processing_state
```

By default, cleaned observations keep valid Swiss-bbox aircraft positions and
exclude receivers, static objects, invalid coordinates, implausible timestamps,
and type-aware speed/climb outliers.

Stop everything with:

```text
Ctrl+C
```

## Open Dashboard

Open:

```text
http://127.0.0.1:8000/
```

The dashboard HTML lives in:

```text
src/static/index.html
```

The browser fetches fresh data from the API every 30 seconds. It shows raw and
processed counts, Swiss density cells, a trajectory-interruption coverage proxy,
aircraft type mix, top aircraft, and processed track segments. Click a segment
row to inspect its trajectory.

## API Routes

Health:

```bash
curl -s http://127.0.0.1:8000/health
```

Database counts:

```bash
curl -s http://127.0.0.1:8000/api/counts
```

Recent observations:

```bash
curl -s 'http://127.0.0.1:8000/api/observations?limit=10'
```

Top aircraft:

```bash
curl -s http://127.0.0.1:8000/api/aircraft
```

Beacon counts:

```bash
curl -s http://127.0.0.1:8000/api/beacons
```

Aircraft type counts:

```bash
curl -s http://127.0.0.1:8000/api/aircraft-types
```

Processed quality summary:

```bash
curl -s http://127.0.0.1:8000/api/quality
```

Density cells:

```bash
curl -s 'http://127.0.0.1:8000/api/density?cell_size_deg=0.1&limit=50'
```

Coverage dropout proxy:

```bash
curl -s 'http://127.0.0.1:8000/api/coverage-gaps?dropout_gap_seconds=60&limit=20'
```

Track segments and segment points:

```bash
curl -s 'http://127.0.0.1:8000/api/segments?limit=10'
curl -s http://127.0.0.1:8000/api/segments/1/points
```

## Disable Collection Temporarily

To run only the API and dashboard without starting the collector:

```bash
OGN_COLLECT_ON_STARTUP=false uv run fastapi
```

To also disable processing:

```bash
OGN_COLLECT_ON_STARTUP=false OGN_PROCESS_ON_STARTUP=false uv run fastapi
```

## Direct Database Check

```bash
sqlite3 data/live/ogn_live.db \
  "select 'raw_messages', count(*) from raw_messages union all select 'position_observations', count(*) from position_observations union all select 'cleaned_observations', count(*) from cleaned_observations union all select 'track_segments', count(*) from track_segments;"
```
