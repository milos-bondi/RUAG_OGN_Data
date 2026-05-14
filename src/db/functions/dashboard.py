"""Fast dashboard snapshot queries for the OGN application."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy.orm import Session

from src.envs import env
from src.utils.geo import AIRCRAFT_TYPE_NAMES, SWITZERLAND_BBOX, UNCONVENTIONAL_AIRCRAFT_TYPES
from src.utils.geo import altitude_band, cell_center, cell_for, haversine_m
from src.utils.geo import inside_swiss_bbox, parse_iso_timestamp


STATE_KEY = "process_ogn_data.last_position_observation_id"


def aircraft_type_name(code: int | None) -> str:
    """Return a readable aircraft type name."""
    return AIRCRAFT_TYPE_NAMES.get(code, f"Type {code}")


def rows(session: Session, sql: str, params: dict[str, object] | None = None) -> list[object]:
    """Run a driver-level SQL query and return all rows."""
    return list(session.connection().exec_driver_sql(sql, params or {}).fetchall())


def one(session: Session, sql: str, params: dict[str, object] | None = None) -> object:
    """Run a driver-level SQL query and return one scalar value."""
    row = session.connection().exec_driver_sql(sql, params or {}).fetchone()
    if not row:
        return None

    return row[0]


def table_exists(session: Session, table_name: str) -> bool:
    """Return whether a SQLite table exists."""
    return bool(
        one(
            session,
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = :table_name
            """,
            {"table_name": table_name},
        )
    )


def region_sql(prefix: str = "") -> str:
    """Return SQL filtering a coordinate into the Switzerland bbox."""
    lat = f"{prefix}latitude"
    lon = f"{prefix}longitude"
    return (
        f"{lat} BETWEEN :min_lat AND :max_lat "
        f"AND {lon} BETWEEN :min_lon AND :max_lon"
    )


def region_params() -> dict[str, float]:
    """Return Switzerland bbox SQL parameters."""
    return dict(SWITZERLAND_BBOX)


def dashboard_since(session: Session) -> str | None:
    """Return the lower timestamp bound for interactive dashboard layers."""
    latest = one(
        session,
        """
        SELECT MAX(timestamp)
        FROM position_observations
        WHERE timestamp IS NOT NULL
        """,
    )
    parsed = parse_iso_timestamp(latest)
    if not parsed:
        return None

    return (parsed - timedelta(hours=env.dashboard_window_hours)).isoformat()


def window_sql(column: str, since: str | None) -> str:
    """Return SQL that filters the interactive dashboard time window."""
    if not since:
        return ""

    return f"AND {column} >= :dashboard_since"


def window_params(since: str | None) -> dict[str, str]:
    """Return dashboard time-window SQL parameters."""
    if not since:
        return {}

    return {"dashboard_since": since}


def percentile(values: list[float], percentile_value: float) -> float | None:
    """Return a percentile from an in-memory list."""
    if not values:
        return None

    ordered = sorted(values)
    index = math.ceil(percentile_value / 100.0 * len(ordered)) - 1
    index = min(max(index, 0), len(ordered) - 1)

    return ordered[index]


def top_counter_label(counter: Counter) -> str:
    """Return the most frequent counter key."""
    if not counter:
        return ""

    return counter.most_common(1)[0][0]


def top_counter_share(counter: Counter) -> float:
    """Return the share of the most frequent counter key."""
    total = sum(counter.values())
    if not total:
        return 0.0

    return counter.most_common(1)[0][1] / total


def fetch_summary(session: Session) -> dict[str, int | str | None]:
    """Return raw and regional observation summary."""
    raw_count = one(session, "SELECT COUNT(*) FROM raw_messages") or 0
    total_positions = one(session, "SELECT COUNT(*) FROM position_observations") or 0
    row = rows(
        session,
        f"""
        SELECT
            COUNT(*) AS region_positions,
            COUNT(DISTINCT aircraft_id) AS unique_aircraft,
            COUNT(
                DISTINCT CASE
                    WHEN aircraft_type IN (1, 4, 6, 7, 11, 12, 13)
                    THEN aircraft_id
                END
            ) AS unconventional_aircraft,
            MIN(timestamp),
            MAX(timestamp)
        FROM position_observations
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude != 0
          AND longitude != 0
          AND {region_sql()}
        """,
        region_params(),
    )[0]

    return {
        "raw_messages": raw_count,
        "position_observations": total_positions,
        "region_positions": row[0] or 0,
        "unique_aircraft": row[1] or 0,
        "unconventional_aircraft": row[2] or 0,
        "first_timestamp": row[3],
        "last_timestamp": row[4],
    }


def fetch_density_cells(
    session: Session,
    since: str | None,
) -> list[dict[str, float | int | str | bool | None]]:
    """Return all dashboard density cells using the raw observations table."""
    query_params = {**region_params(), **window_params(since), "grid": env.dashboard_grid_degrees}
    result = rows(
        session,
        f"""
        WITH cells AS (
            SELECT
                ROUND(latitude / :grid) * :grid AS lat_cell,
                ROUND(longitude / :grid) * :grid AS lon_cell,
                aircraft_type,
                aircraft_id,
                altitude_m,
                ground_speed_kmh
            FROM position_observations
            WHERE latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND latitude != 0
              AND longitude != 0
              {window_sql("timestamp", since)}
              AND {region_sql()}
        )
        SELECT
            lat_cell,
            lon_cell,
            aircraft_type,
            COUNT(*) AS observations,
            COUNT(DISTINCT aircraft_id) AS unique_aircraft,
            ROUND(AVG(altitude_m), 0) AS avg_altitude_m,
            ROUND(AVG(ground_speed_kmh), 1) AS avg_speed_kmh
        FROM cells
        GROUP BY lat_cell, lon_cell, aircraft_type
        HAVING observations >= 3
        ORDER BY observations DESC
        """,
        query_params,
    )

    return [
        {
            "lat": row[0],
            "lon": row[1],
            "aircraft_type": row[2],
            "aircraft_type_name": aircraft_type_name(row[2]),
            "unconventional": row[2] in UNCONVENTIONAL_AIRCRAFT_TYPES,
            "observations": row[3],
            "unique_aircraft": row[4],
            "avg_altitude_m": row[5],
            "avg_speed_kmh": row[6],
        }
        for row in result
    ]


def fetch_aircraft_type_counts(
    session: Session,
    since: str | None,
) -> list[dict[str, int | str | bool | None]]:
    """Return raw observation counts grouped by aircraft type."""
    result = rows(
        session,
        f"""
        SELECT
            aircraft_type,
            COUNT(*) AS observations,
            COUNT(DISTINCT aircraft_id) AS unique_aircraft
        FROM position_observations
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude != 0
          AND longitude != 0
          {window_sql("timestamp", since)}
          AND {region_sql()}
        GROUP BY aircraft_type
        ORDER BY observations DESC
        """,
        {**region_params(), **window_params(since)},
    )

    return [
        {
            "code": row[0],
            "label": aircraft_type_name(row[0]),
            "observations": row[1],
            "unique_aircraft": row[2],
            "unconventional": row[0] in UNCONVENTIONAL_AIRCRAFT_TYPES,
        }
        for row in result
    ]


def fetch_beacon_counts(session: Session, since: str | None) -> list[dict[str, int | str]]:
    """Return raw observation counts grouped by beacon type."""
    result = rows(
        session,
        f"""
        SELECT COALESCE(beacon_type, 'unknown') AS beacon_type, COUNT(*) AS n
        FROM position_observations
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude != 0
          AND longitude != 0
          {window_sql("timestamp", since)}
          AND {region_sql()}
        GROUP BY COALESCE(beacon_type, 'unknown')
        ORDER BY n DESC
        """,
        {**region_params(), **window_params(since)},
    )

    return [{"beacon_type": row[0], "observations": row[1]} for row in result]


def fetch_top_aircraft(session: Session, since: str | None) -> list[dict[str, int | str]]:
    """Return aircraft IDs with the most regional raw observations."""
    result = rows(
        session,
        f"""
        SELECT aircraft_id, COUNT(*) AS observations
        FROM position_observations
        WHERE aircraft_id IS NOT NULL
          AND latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude != 0
          AND longitude != 0
          {window_sql("timestamp", since)}
          AND {region_sql()}
        GROUP BY aircraft_id
        ORDER BY observations DESC
        LIMIT 20
        """,
        {**region_params(), **window_params(since)},
    )

    return [{"aircraft_id": row[0], "observations": row[1]} for row in result]


def fetch_quality_tracks(
    session: Session,
    since: str | None,
) -> list[dict[str, int | float | str | bool | None]]:
    """Return recent high-continuity tracks derived from raw observations."""
    result = rows(
        session,
        f"""
        WITH base AS (
            SELECT
                aircraft_id,
                COALESCE(beacon_type, 'unknown') AS beacon_type,
                aircraft_type,
                timestamp,
                altitude_m,
                ground_speed_kmh,
                LAG(timestamp) OVER (
                    PARTITION BY aircraft_id
                    ORDER BY timestamp
                ) AS prev_ts
            FROM position_observations
            WHERE aircraft_id IS NOT NULL
              AND timestamp IS NOT NULL
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND latitude != 0
              AND longitude != 0
              {window_sql("timestamp", since)}
              AND {region_sql()}
        ),
        segmented AS (
            SELECT
                *,
                SUM(
                    CASE
                        WHEN prev_ts IS NULL THEN 1
                        WHEN (julianday(timestamp) - julianday(prev_ts)) * 86400.0 > 10 THEN 1
                        ELSE 0
                    END
                ) OVER (
                    PARTITION BY aircraft_id
                    ORDER BY timestamp
                    ROWS UNBOUNDED PRECEDING
                ) AS segment_id
            FROM base
        ),
        gaps AS (
            SELECT
                *,
                (julianday(timestamp) - julianday(prev_ts)) * 86400.0 AS gap_s
            FROM segmented
        )
        SELECT
            aircraft_id,
            segment_id,
            beacon_type,
            aircraft_type,
            COUNT(*) AS points,
            ROUND((julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 1440.0, 1),
            ROUND(AVG(CASE WHEN gap_s IS NOT NULL THEN gap_s END), 1),
            ROUND(MAX(CASE WHEN gap_s IS NOT NULL THEN gap_s END), 1),
            ROUND(AVG(altitude_m), 0),
            ROUND(AVG(ground_speed_kmh), 1),
            MIN(timestamp),
            MAX(timestamp)
        FROM gaps
        GROUP BY aircraft_id, segment_id
        HAVING points >= 120
        ORDER BY MAX(timestamp) DESC, points DESC
        LIMIT 30
        """,
        {**region_params(), **window_params(since)},
    )

    return [
        {
            "aircraft_id": row[0],
            "segment_id": row[1],
            "beacon_type": row[2],
            "aircraft_type": row[3],
            "aircraft_type_name": aircraft_type_name(row[3]),
            "unconventional": row[3] in UNCONVENTIONAL_AIRCRAFT_TYPES,
            "points": row[4],
            "duration_min": row[5],
            "avg_gap_s": row[6],
            "max_gap_s": row[7],
            "avg_altitude_m": row[8],
            "avg_speed_kmh": row[9],
            "first_timestamp": row[10],
            "last_timestamp": row[11],
        }
        for row in result
    ]


def fetch_engineering_summary(session: Session) -> dict[str, object]:
    """Return processed table counts and segment summaries."""
    if not table_exists(session, "cleaned_observations"):
        return {
            "available": False,
            "cleaned_observations": 0,
            "track_points": 0,
            "track_segments": 0,
            "good_segments": 0,
            "last_processed_position_id": 0,
            "type_counts": [],
            "good_type_counts": [],
            "recent_segments": [],
        }

    type_counts = rows(
        session,
        """
        SELECT aircraft_type, aircraft_type_name, COUNT(*), SUM(n_points),
               SUM(CASE WHEN is_likely_unconventional = 1 THEN 1 ELSE 0 END)
        FROM track_segments
        GROUP BY aircraft_type, aircraft_type_name
        ORDER BY SUM(n_points) DESC
        LIMIT 12
        """,
    )
    good_type_counts = rows(
        session,
        """
        SELECT aircraft_type, aircraft_type_name, COUNT(*), SUM(n_points),
               ROUND(AVG(duration_s) / 60.0, 1)
        FROM track_segments
        WHERE n_points >= 20
          AND duration_s >= 120
        GROUP BY aircraft_type, aircraft_type_name
        ORDER BY SUM(n_points) DESC
        LIMIT 12
        """,
    )
    recent_segments = rows(
        session,
        """
        SELECT id, aircraft_id, aircraft_type, aircraft_type_name,
               COALESCE(beacon_type, 'unknown'), n_points,
               ROUND(duration_s / 60.0, 1), ROUND(max_gap_s, 1),
               ROUND(distance_km, 1), ROUND(avg_ground_speed_kmh, 1),
               start_timestamp, end_timestamp, is_likely_unconventional
        FROM track_segments
        WHERE n_points >= 20
          AND duration_s >= 120
        ORDER BY end_timestamp DESC, n_points DESC
        LIMIT 20
        """,
    )

    return {
        "available": True,
        "cleaned_observations": one(session, "SELECT COUNT(*) FROM cleaned_observations") or 0,
        "track_points": one(session, "SELECT COUNT(*) FROM track_points") or 0,
        "track_segments": one(session, "SELECT COUNT(*) FROM track_segments") or 0,
        "good_segments": one(
            session,
            "SELECT COUNT(*) FROM track_segments WHERE n_points >= 20 AND duration_s >= 120",
        )
        or 0,
        "last_processed_position_id": int(
            one(session, "SELECT value FROM processing_state WHERE key = :key", {"key": STATE_KEY})
            or 0
        ),
        "type_counts": [
            {
                "aircraft_type": row[0],
                "aircraft_type_name": row[1],
                "segments": row[2],
                "points": row[3] or 0,
                "unconventional_segments": row[4] or 0,
                "unconventional": row[0] in UNCONVENTIONAL_AIRCRAFT_TYPES,
            }
            for row in type_counts
        ],
        "good_type_counts": [
            {
                "aircraft_type": row[0],
                "aircraft_type_name": row[1],
                "segments": row[2],
                "points": row[3] or 0,
                "avg_duration_min": row[4],
                "unconventional": row[0] in UNCONVENTIONAL_AIRCRAFT_TYPES,
            }
            for row in good_type_counts
        ],
        "recent_segments": [
            {
                "id": row[0],
                "aircraft_id": row[1],
                "aircraft_type": row[2],
                "aircraft_type_name": row[3],
                "beacon_type": row[4],
                "points": row[5],
                "duration_min": row[6],
                "max_gap_s": row[7],
                "distance_km": row[8],
                "avg_speed_kmh": row[9],
                "start_timestamp": row[10],
                "end_timestamp": row[11],
                "unconventional": bool(row[12]),
            }
            for row in recent_segments
        ],
    }


def trajectory_quality_score(segment: dict[str, object], max_gap_s: float) -> float:
    """Return a trajectory quality score matching the old dashboard."""
    points_score = min(float(segment["points"]) / 300.0, 1.0) * 25.0
    distance_score = min(float(segment["distance_km"]) / 25.0, 1.0) * 30.0
    duration_score = min(float(segment["duration_s"]) / 900.0, 1.0) * 20.0
    gap_score = max(0.0, 1.0 - float(segment["max_gap_s"]) / max_gap_s) * 20.0
    speed = float(segment["avg_speed_kmh"] or 0.0)
    speed_score = 5.0 if 5.0 <= speed <= 220.0 else 0.0

    return round(points_score + distance_score + duration_score + gap_score + speed_score, 1)


def fetch_best_trajectories(session: Session) -> list[dict[str, object]]:
    """Return ranked unconventional trajectory candidates."""
    if not table_exists(session, "track_segments"):
        return []

    result = rows(
        session,
        """
        SELECT id, aircraft_id, aircraft_type_name, COALESCE(beacon_type, 'unknown'),
               n_points, duration_s, max_gap_s, distance_km,
               avg_ground_speed_kmh, start_timestamp, end_timestamp
        FROM track_segments
        WHERE is_likely_unconventional = 1
          AND n_points >= 50
          AND duration_s >= 180
          AND distance_km >= 5
          AND max_gap_s <= 15
          AND (avg_ground_speed_kmh IS NULL OR avg_ground_speed_kmh <= 300)
        ORDER BY distance_km DESC, n_points DESC
        LIMIT 200
        """,
    )
    candidates = []

    # Score in Python after prefiltering, matching the old renderer behavior.
    for row in result:
        segment = {
            "id": row[0],
            "aircraft_id": row[1],
            "aircraft_type_name": row[2],
            "beacon_type": row[3],
            "points": row[4],
            "duration_s": row[5],
            "duration_min": row[5] / 60.0 if row[5] is not None else None,
            "max_gap_s": row[6],
            "distance_km": row[7],
            "avg_speed_kmh": row[8],
            "start_timestamp": row[9],
            "end_timestamp": row[10],
        }
        segment["quality_score"] = trajectory_quality_score(segment, 15.0)
        candidates.append(segment)

    candidates.sort(
        key=lambda item: (item["quality_score"], item["distance_km"], item["points"]),
        reverse=True,
    )

    return candidates[:25]


def fetch_dropout_candidates(session: Session, since: str | None) -> list[dict[str, object]]:
    """Return individual trajectory gaps that look like dropout candidates."""
    if not table_exists(session, "cleaned_observations"):
        return []

    type_filter = ""
    if not env.dashboard_include_all_dropout_aircraft:
        type_filter = "AND aircraft_type IN (1, 4, 6, 7, 11, 12, 13)"

    result = rows(
        session,
        f"""
        SELECT aircraft_id, aircraft_type, aircraft_type_name,
               COALESCE(beacon_type, 'unknown'), timestamp, latitude, longitude
        FROM cleaned_observations
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND timestamp IS NOT NULL
          AND aircraft_id IS NOT NULL
          {window_sql("timestamp", since)}
          AND {region_sql()}
          {type_filter}
        ORDER BY aircraft_id, timestamp
        """,
        {**region_params(), **window_params(since)},
    )
    candidates = []
    previous = None

    # Walk ordered points once to find plausible observation gaps.
    for row in result:
        timestamp = parse_iso_timestamp(row[4])
        latitude = row[5]
        longitude = row[6]
        if previous and previous["aircraft_id"] == row[0] and timestamp and previous["timestamp"]:
            gap_s = (timestamp - previous["timestamp"]).total_seconds()
            if 0 < gap_s <= env.dashboard_dropout_max_gap_seconds:
                if inside_swiss_bbox(previous["latitude"], previous["longitude"]) and inside_swiss_bbox(latitude, longitude):
                    distance_km = haversine_m(
                        previous["latitude"],
                        previous["longitude"],
                        latitude,
                        longitude,
                    ) / 1000.0
                    implied_speed_kmh = distance_km / gap_s * 3600.0
                    if (
                        gap_s >= env.segment_gap_seconds
                        and distance_km >= env.dashboard_dropout_min_distance_km
                        and implied_speed_kmh <= env.dashboard_dropout_max_implied_speed_kmh
                    ):
                        candidates.append(
                            {
                                "aircraft_id": row[0],
                                "aircraft_type_name": row[2],
                                "beacon_type": row[3],
                                "gap_s": round(gap_s, 1),
                                "distance_km": round(distance_km, 2),
                                "implied_speed_kmh": round(implied_speed_kmh, 1),
                                "start_timestamp": previous["timestamp_raw"],
                                "end_timestamp": row[4],
                                "lat": round((previous["latitude"] + latitude) / 2.0, 6),
                                "lon": round((previous["longitude"] + longitude) / 2.0, 6),
                            }
                        )
        previous = {
            "aircraft_id": row[0],
            "timestamp": timestamp,
            "timestamp_raw": row[4],
            "latitude": latitude,
            "longitude": longitude,
        }

    candidates.sort(key=lambda item: item["gap_s"], reverse=True)

    return candidates[: env.dashboard_dropout_limit]


def new_hotspot_stats() -> dict[str, object]:
    """Return accumulator state for one dropout hotspot cell."""
    return {
        "transition_count": 0,
        "dropout_count": 0,
        "aircraft": set(),
        "aircraft_counts": Counter(),
        "receivers": Counter(),
        "altitude_bands": Counter(),
        "aircraft_types": Counter(),
        "beacon_types": Counter(),
        "gap_values_s": [],
        "dropout_gap_values_s": [],
        "sum_gap_s": 0.0,
        "sum_dropout_gap_s": 0.0,
        "max_dropout_gap_s": 0.0,
    }


def add_hotspot_transition(
    stats: dict[str, object],
    gap_s: float,
    is_dropout: bool,
    row: object,
    previous: dict[str, object],
) -> None:
    """Add one transition into a hotspot accumulator."""
    stats["transition_count"] += 1
    stats["aircraft"].add(row[0])
    stats["aircraft_counts"][row[0]] += 1
    stats["sum_gap_s"] += gap_s
    stats["gap_values_s"].append(gap_s)
    stats["altitude_bands"][altitude_band(row[7])] += 1
    stats["aircraft_types"][row[1]] += 1
    stats["beacon_types"][row[2] or "unknown"] += 1

    for receiver_name in [previous["receiver_name"], row[3]]:
        if receiver_name:
            stats["receivers"][receiver_name] += 1

    if is_dropout:
        stats["dropout_count"] += 1
        stats["sum_dropout_gap_s"] += gap_s
        stats["dropout_gap_values_s"].append(gap_s)
        stats["max_dropout_gap_s"] = max(stats["max_dropout_gap_s"], gap_s)


def hotspot_bias_hint(stats: dict[str, object], transitions: int, dropouts: int) -> str:
    """Return a compact interpretation hint for a dropout hotspot cell."""
    dropout_rate = dropouts / transitions if transitions else 0.0
    receiver_share = top_counter_share(stats["receivers"])
    aircraft_share = top_counter_share(stats["aircraft_counts"])
    altitude_label = top_counter_label(stats["altitude_bands"])
    altitude_share = top_counter_share(stats["altitude_bands"])

    if receiver_share >= 0.65:
        return "receiver concentrated"

    if aircraft_share >= 0.55:
        return "aircraft concentrated"

    if altitude_label in {"0-500 m", "500-1000 m"} and altitude_share >= 0.45:
        return "low altitude sensitive"

    if dropout_rate >= 0.2 and dropouts >= 20:
        return "persistent spatial hotspot"

    return "mixed coverage signal"


def fetch_dropout_hotspots(session: Session, since: str | None) -> list[dict[str, object]]:
    """Return aggregate dropout hotspot cells."""
    if not table_exists(session, "cleaned_observations"):
        return []

    type_filter = ""
    if not env.dashboard_include_all_dropout_aircraft:
        type_filter = "AND co.aircraft_type IN (1, 4, 6, 7, 11, 12, 13)"

    result = rows(
        session,
        f"""
        SELECT co.aircraft_id, co.aircraft_type_name,
               COALESCE(co.beacon_type, 'unknown'), po.receiver_name,
               co.timestamp, co.latitude, co.longitude, co.altitude_m
        FROM cleaned_observations AS co
        LEFT JOIN position_observations AS po
          ON po.id = co.position_observation_id
        WHERE co.latitude IS NOT NULL
          AND co.longitude IS NOT NULL
          AND co.timestamp IS NOT NULL
          AND co.aircraft_id IS NOT NULL
          {window_sql("co.timestamp", since)}
          AND {region_sql("co.")}
          {type_filter}
        ORDER BY co.aircraft_id, co.timestamp
        """,
        {**region_params(), **window_params(since)},
    )
    grid = defaultdict(new_hotspot_stats)
    previous = None

    # Walk all transitions once and aggregate them into map cells.
    for row in result:
        timestamp = parse_iso_timestamp(row[4])
        latitude = row[5]
        longitude = row[6]
        if previous and previous["aircraft_id"] == row[0] and timestamp and previous["timestamp"]:
            gap_s = (timestamp - previous["timestamp"]).total_seconds()
            if 0 < gap_s <= env.dashboard_dropout_max_gap_seconds:
                distance_km = haversine_m(
                    previous["latitude"],
                    previous["longitude"],
                    latitude,
                    longitude,
                ) / 1000.0
                implied_speed_kmh = distance_km / gap_s * 3600.0
                if implied_speed_kmh <= env.dashboard_dropout_max_implied_speed_kmh:
                    cell = cell_for(
                        (previous["latitude"] + latitude) / 2.0,
                        (previous["longitude"] + longitude) / 2.0,
                        env.dashboard_grid_degrees,
                    )
                    add_hotspot_transition(
                        grid[cell],
                        gap_s,
                        gap_s >= env.segment_gap_seconds
                        and distance_km >= env.dashboard_dropout_min_distance_km,
                        row,
                        previous,
                    )
        previous = {
            "aircraft_id": row[0],
            "timestamp": timestamp,
            "receiver_name": row[3],
            "latitude": latitude,
            "longitude": longitude,
        }

    cells = []
    for cell, stats in grid.items():
        transitions = stats["transition_count"]
        dropouts = stats["dropout_count"]
        if transitions < env.dashboard_dropout_hotspot_min_transitions or dropouts == 0:
            continue

        lat, lon = cell_center(cell, env.dashboard_grid_degrees)
        cells.append(
            {
                "lat": lat,
                "lon": lon,
                "transition_count": transitions,
                "dropout_count": dropouts,
                "dropout_rate": round(dropouts / transitions, 5),
                "bias_hint": hotspot_bias_hint(stats, transitions, dropouts),
                "unique_aircraft": len(stats["aircraft"]),
                "unique_receivers": len(stats["receivers"]),
                "top_receiver": top_counter_label(stats["receivers"]),
                "top_receiver_share": round(top_counter_share(stats["receivers"]), 4),
                "top_aircraft_share": round(top_counter_share(stats["aircraft_counts"]), 4),
                "dominant_altitude_band": top_counter_label(stats["altitude_bands"]),
                "dominant_aircraft_type": top_counter_label(stats["aircraft_types"]),
                "dominant_beacon_type": top_counter_label(stats["beacon_types"]),
                "avg_gap_s": round(stats["sum_gap_s"] / transitions, 1),
                "p95_gap_s": round(percentile(stats["gap_values_s"], 95) or 0, 1),
                "avg_dropout_gap_s": round(stats["sum_dropout_gap_s"] / dropouts, 1),
                "p95_dropout_gap_s": round(percentile(stats["dropout_gap_values_s"], 95) or 0, 1),
                "max_dropout_gap_s": round(stats["max_dropout_gap_s"], 1),
            }
        )

    cells.sort(key=lambda item: (item["dropout_rate"], item["dropout_count"]), reverse=True)

    return cells


def dashboard_snapshot(session: Session) -> dict[str, object]:
    """Collect the complete dashboard snapshot with fast raw SQL queries."""
    since = dashboard_since(session)
    engineering = fetch_engineering_summary(session)
    summary = fetch_summary(session)
    summary.update(
        {
            "cleaned_observations": engineering["cleaned_observations"],
            "track_points": engineering["track_points"],
            "track_segments": engineering["track_segments"],
            "good_segments": engineering["good_segments"],
            "last_processed_position_id": engineering["last_processed_position_id"],
        }
    )

    return {
        "summary": summary,
        "density_cells": fetch_density_cells(session, since),
        "dropout_candidates": fetch_dropout_candidates(session, since),
        "dropout_hotspots": fetch_dropout_hotspots(session, since),
        "aircraft_types": fetch_aircraft_type_counts(session, since),
        "beacons": fetch_beacon_counts(session, since),
        "top_aircraft": fetch_top_aircraft(session, since),
        "quality_tracks": fetch_quality_tracks(session, since),
        "engineering": engineering,
        "best_trajectories": fetch_best_trajectories(session),
        "window_since": since,
        "window_hours": env.dashboard_window_hours,
        "grid_degrees": env.dashboard_grid_degrees,
        "refresh_seconds": env.dashboard_refresh_seconds,
    }
