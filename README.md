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
│   ├── utils/
│   ├── .env.sample
│   └── pyproject.toml
├── AGENTS.md
├── README.md
└── pyproject.toml
```

Local collected data stays under `data/` and is ignored by Git.

## Setup

The local virtual environment is currently at `support_files/env`.

```bash
support_files/env/bin/python -m pip install .
```

The required OGN client package is pinned to `ogn-client==1.3.2`.

## Run The API And Dashboard

```bash
support_files/env/bin/python -m uvicorn src.router:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Useful API routes:

```text
/health
/api/counts
/api/observations
/api/aircraft
/api/beacons
```

## Run The Collector

```bash
support_files/env/bin/python -m src.cron.collector
```

Configuration is loaded with `pydantic-settings` from environment variables.
Copy `src/.env.sample` to `src/.env` when local overrides are needed.

Default database:

```text
data/live/ogn_live.db
```
