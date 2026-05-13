# Operational Manual

Run every command from the repository root:

```bash
cd /Users/milos/Ruag/RUAG_OGN_Data
```

## Install Or Refresh The Environment

Use the local virtual environment already created under `support_files/env`:

```bash
support_files/env/bin/python -m pip install .
```

Confirm the required OGN client version:

```bash
support_files/env/bin/python -m pip show ogn-client
```

Expected version:

```text
1.3.2
```

## Configure The Pipeline

Copy the sample environment file when local overrides are needed:

```bash
cp src/.env.sample src/.env
```

Default database:

```text
data/live/ogn_live.db
```

Useful environment variables:

```text
OGN_DATABASE_URL=sqlite:///data/live/ogn_live.db
OGN_APRS_USER=N0CALL
OGN_APRS_FILTER=r/46.8/8.2/250
OGN_APRS_SERVER_HOST=aprs.glidernet.org
OGN_COLLECTION_PROFILE=all
```

## Start Collecting Data

Run the live OGN collector:

```bash
support_files/env/bin/python -m src.cron.collector
```

The collector connects to the OGN APRS stream, stores every raw message in
`raw_messages`, and stores parsed position messages in `position_observations`.

Stop it with:

```text
Ctrl+C
```

## Run Collector In Background

Start the collector in the background:

```bash
nohup support_files/env/bin/python -m src.cron.collector \
  > data/live/collector.log 2>&1 & echo $! > data/live/collector.pid
```

Check the log:

```bash
tail -f data/live/collector.log
```

Stop the background collector:

```bash
kill -INT $(cat data/live/collector.pid)
```

## Start The API And Dashboard

Run the FastAPI application:

```bash
support_files/env/bin/python -m uvicorn src.router:app --host 127.0.0.1 --port 8000 --reload
```

Without auto-reload:

```bash
support_files/env/bin/python -m uvicorn src.router:app --host 127.0.0.1 --port 8000
```

## Open The Dashboard

Open this URL in the browser:

```text
http://127.0.0.1:8000/
```

The dashboard reads directly from SQLite through the API. It refreshes its data
in the browser every 30 seconds.

## Update The Dashboard

No static dashboard rebuild command is needed in the current architecture.

To update the dashboard data:

1. Keep the collector running.
2. Keep the API server running.
3. Refresh the browser, or wait for the browser auto-refresh.

## Check API Health

```bash
curl -s http://127.0.0.1:8000/health
```

## Check Database Counts

```bash
curl -s http://127.0.0.1:8000/api/counts
```

Equivalent direct SQLite check:

```bash
sqlite3 data/live/ogn_live.db \
  "select 'raw_messages', count(*) from raw_messages union all select 'position_observations', count(*) from position_observations;"
```

## Inspect Recent Observations

Through the API:

```bash
curl -s 'http://127.0.0.1:8000/api/observations?limit=10'
```

Directly through SQLite:

```bash
sqlite3 data/live/ogn_live.db \
  "select id, aircraft_id, beacon_type, receiver_name, timestamp, latitude, longitude from position_observations order by id desc limit 10;"
```

## Aircraft And Beacon Summaries

Top aircraft:

```bash
curl -s http://127.0.0.1:8000/api/aircraft
```

Beacon counts:

```bash
curl -s http://127.0.0.1:8000/api/beacons
```

## Processing Data

There is no separate processing command in the current FastAPI architecture.
The collector performs the active processing step by parsing OGN APRS messages
as they arrive and writing parsed rows to `position_observations`.

Current flow:

```text
OGN APRS stream -> src.cron.collector -> raw_messages + position_observations -> FastAPI API -> dashboard
```

If derived tables or batch processing are added later, they should live under
`src/cron/` or `src/db/functions/` and be documented here with their exact
commands.

## Typical Operating Setup

Terminal 1, collector:

```bash
cd /Users/milos/Ruag/RUAG_OGN_Data
support_files/env/bin/python -m src.cron.collector
```

Terminal 2, API and dashboard:

```bash
cd /Users/milos/Ruag/RUAG_OGN_Data
support_files/env/bin/python -m uvicorn src.router:app --host 127.0.0.1 --port 8000 --reload
```

Browser:

```text
http://127.0.0.1:8000/
```

## Troubleshooting

If port `8000` is already used, run the API on another port:

```bash
support_files/env/bin/python -m uvicorn src.router:app --host 127.0.0.1 --port 8001 --reload
```

Then open:

```text
http://127.0.0.1:8001/
```

If SQLite reports that the database is locked, stop extra collector processes
and restart only one collector:

```bash
pgrep -fl src.cron.collector
```

If a background collector was started with the PID file:

```bash
kill -INT $(cat data/live/collector.pid)
```
