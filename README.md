# RUAG OGN Data

FastAPI application for collecting Open Glider Network APRS traffic into SQLite
and viewing the latest observations in a simple HTML dashboard.

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
ogn-client==1.3.2
```

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
OGN_HOST=127.0.0.1
OGN_PORT=8000
```

## Run Server And Collector

Start the API, dashboard, and live collector with one command:

```bash
uv run fastapi
```

This command starts the FastAPI server and starts the OGN collection job during
application startup. New raw messages are appended to `raw_messages`; parsed
positions are appended to `position_observations`.

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

The browser fetches fresh data from the API every 30 seconds.

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

## Disable Collection Temporarily

To run only the API and dashboard without starting the collector:

```bash
OGN_COLLECT_ON_STARTUP=false uv run fastapi
```

## Direct Database Check

```bash
sqlite3 data/live/ogn_live.db \
  "select 'raw_messages', count(*) from raw_messages union all select 'position_observations', count(*) from position_observations;"
```
