# Commands

Common commands for running the RUAG OGN data collector, processor, API, and dashboard.

## Install

Install the Python environment with `uv`:

```bash
uv sync
```

If `uv run fastapi` opens the FastAPI package CLI and prints `please install
fastapi[standard]`, recreate the virtual environment so the project command is
installed:

```bash
rm -rf .venv
uv sync
uv run fastapi
```

Fallback command if you need to start the app before fixing the environment:

```bash
uv run python -m src.cli
```

Check that `ogn-client` is installed:

```bash
uv run python -m pip show ogn-client
```

## Configure

Create a local environment file when you need overrides:

```bash
cp src/.env.sample src/.env
```

Default database path:

```text
data/live/ogn_live.db
```

Important environment variables:

```bash
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
OGN_HOST=127.0.0.1
OGN_PORT=8000
```

## Start Everything

Start the FastAPI server, live OGN collector, background processor, API, and dashboard:

```bash
uv run fastapi
```

Open the dashboard:

```text
http://127.0.0.1:8000/
```

Stop the server, collector, and processor:

```text
Ctrl+C
```

## Start API And Dashboard Only

Use this when you want to inspect existing data without collecting new APRS traffic:

```bash
OGN_COLLECT_ON_STARTUP=false uv run fastapi
```

Use this when you also want to disable background processing:

```bash
OGN_COLLECT_ON_STARTUP=false OGN_PROCESS_ON_STARTUP=false uv run fastapi
```

## Collect Data

Collection runs automatically when `OGN_COLLECT_ON_STARTUP=true`:

```bash
uv run fastapi
```

To collect only likely FLARM/FANET/Naviter-like traffic:

```bash
OGN_COLLECTION_PROFILE=unconventional uv run fastapi
```

To collect all parsed APRS traffic:

```bash
OGN_COLLECTION_PROFILE=all uv run fastapi
```

## Process Data

Processing runs automatically when `OGN_PROCESS_ON_STARTUP=true`:

```bash
uv run fastapi
```

Tune processor batch size and interval:

```bash
OGN_PROCESSOR_BATCH_SIZE=10000 OGN_PROCESSOR_INTERVAL_SECONDS=10 uv run fastapi
```

The processor writes:

```text
cleaned_observations
track_segments
track_points
processing_state
```

## API Checks

Health:

```bash
curl -s http://127.0.0.1:8000/health
```

Database counts:

```bash
curl -s http://127.0.0.1:8000/api/counts
```

Latest raw parsed observations:

```bash
curl -s 'http://127.0.0.1:8000/api/observations?limit=10'
```

Latest cleaned observations:

```bash
curl -s 'http://127.0.0.1:8000/api/cleaned-observations?limit=10'
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

Density grid:

```bash
curl -s 'http://127.0.0.1:8000/api/density?cell_size_deg=0.1&limit=50'
```

Coverage dropout proxy:

```bash
curl -s 'http://127.0.0.1:8000/api/coverage-gaps?dropout_gap_seconds=60&limit=20'
```

Track segments:

```bash
curl -s 'http://127.0.0.1:8000/api/segments?limit=10'
```

Track points for one segment:

```bash
curl -s http://127.0.0.1:8000/api/segments/1/points
```

## Database Checks

Open SQLite:

```bash
sqlite3 data/live/ogn_live.db
```

Show tables:

```bash
sqlite3 data/live/ogn_live.db '.tables'
```

Count key tables:

```bash
sqlite3 data/live/ogn_live.db \
  "select 'raw_messages', count(*) from raw_messages union all select 'position_observations', count(*) from position_observations union all select 'cleaned_observations', count(*) from cleaned_observations union all select 'track_segments', count(*) from track_segments union all select 'track_points', count(*) from track_points;"
```

Show latest observations:

```bash
sqlite3 data/live/ogn_live.db \
  "select aircraft_id, timestamp, latitude, longitude, altitude_m, ground_speed_kmh from position_observations order by id desc limit 10;"
```

Show latest processed segments:

```bash
sqlite3 data/live/ogn_live.db \
  "select id, aircraft_id, aircraft_type_name, n_points, duration_s, distance_km from track_segments order by id desc limit 10;"
```

## Local Verification

Compile the source:

```bash
uv run python -m compileall src
```

If `uv` is not available but the existing local virtualenv is present:

```bash
support_files/env/bin/python -m compileall src
```
