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
POINT_DISTRIBUTIONS = [
    {
        "name": "Ground speed",
        "unit": "km/h",
        "source": "cleaned points",
        "from_sql": "cleaned_observations",
        "type_column": "aircraft_type",
        "time_column": "timestamp",
        "value_sql": "ground_speed_kmh",
        "min_value": 0.0,
        "max_value": 300.0,
        "bin_width": 10.0,
        "min_samples": "points",
        "extra_where": "AND inside_swiss_bbox = 1 AND is_likely_unconventional = 1",
    },
    {
        "name": "Climb rate",
        "unit": "m/s",
        "source": "cleaned points",
        "from_sql": "cleaned_observations",
        "type_column": "aircraft_type",
        "time_column": "timestamp",
        "value_sql": "climb_rate_ms",
        "min_value": -10.0,
        "max_value": 10.0,
        "bin_width": 0.5,
        "min_samples": "points",
        "extra_where": "AND inside_swiss_bbox = 1 AND is_likely_unconventional = 1",
    },
    {
        "name": "Altitude",
        "unit": "m",
        "source": "cleaned points",
        "from_sql": "cleaned_observations",
        "type_column": "aircraft_type",
        "time_column": "timestamp",
        "value_sql": "altitude_m",
        "min_value": 0.0,
        "max_value": 6000.0,
        "bin_width": 250.0,
        "min_samples": "points",
        "extra_where": "AND inside_swiss_bbox = 1 AND is_likely_unconventional = 1",
    },
    {
        "name": "Turn rate",
        "unit": "deg/s",
        "source": "cleaned points",
        "from_sql": "cleaned_observations",
        "type_column": "aircraft_type",
        "time_column": "timestamp",
        "value_sql": "turn_rate_degs",
        "min_value": -90.0,
        "max_value": 90.0,
        "bin_width": 5.0,
        "min_samples": "points",
        "extra_where": "AND inside_swiss_bbox = 1 AND is_likely_unconventional = 1",
    },
]
SEGMENT_DISTRIBUTIONS = [
    {
        "name": "Segment duration",
        "unit": "s",
        "source": "good segments",
        "from_sql": "track_segments",
        "type_column": "aircraft_type",
        "time_column": "end_timestamp",
        "value_sql": "duration_s",
        "min_value": 120.0,
        "max_value": 7200.0,
        "bin_width": 60.0,
        "min_samples": "segments",
        "extra_where": "AND is_likely_unconventional = 1 AND n_points >= 20",
    },
    {
        "name": "Segment distance",
        "unit": "km",
        "source": "good segments",
        "from_sql": "track_segments",
        "type_column": "aircraft_type",
        "time_column": "end_timestamp",
        "value_sql": "distance_km",
        "min_value": 0.0,
        "max_value": 200.0,
        "bin_width": 2.0,
        "min_samples": "segments",
        "extra_where": "AND is_likely_unconventional = 1 AND n_points >= 20 AND duration_s >= 120",
    },
    {
        "name": "Max inter-point gap",
        "unit": "s",
        "source": "good segments",
        "from_sql": "track_segments",
        "type_column": "aircraft_type",
        "time_column": "end_timestamp",
        "value_sql": "max_gap_s",
        "min_value": 0.0,
        "max_value": 120.0,
        "bin_width": 2.0,
        "min_samples": "segments",
        "extra_where": "AND is_likely_unconventional = 1 AND n_points >= 20 AND duration_s >= 120",
    },
]
TRACK_POINT_DISTRIBUTIONS = [
    {
        "name": "Inter-point gap",
        "unit": "s",
        "source": "track points",
        "from_sql": """
            track_points AS tp
            JOIN track_segments AS ts ON ts.id = tp.segment_id
            JOIN cleaned_observations AS co ON co.id = tp.cleaned_observation_id
        """,
        "type_column": "ts.aircraft_type",
        "time_column": "co.timestamp",
        "value_sql": "tp.dt_s",
        "min_value": 0.0,
        "max_value": 120.0,
        "bin_width": 1.0,
        "min_samples": "points",
        "extra_where": "AND ts.is_likely_unconventional = 1 AND ts.n_points >= 20 AND ts.duration_s >= 120",
    },
    {
        "name": "Heading",
        "unit": "deg",
        "source": "track points",
        "from_sql": """
            track_points AS tp
            JOIN track_segments AS ts ON ts.id = tp.segment_id
            JOIN cleaned_observations AS co ON co.id = tp.cleaned_observation_id
        """,
        "type_column": "ts.aircraft_type",
        "time_column": "co.timestamp",
        "value_sql": "tp.estimated_heading_deg",
        "min_value": 0.0,
        "max_value": 360.0,
        "bin_width": 15.0,
        "min_samples": "points",
        "extra_where": "AND ts.is_likely_unconventional = 1 AND ts.n_points >= 20 AND ts.duration_s >= 120",
    },
]


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


def histogram_total(histogram: dict[object, int]) -> int:
    """Return the number of samples represented by a histogram."""
    return sum(histogram.values())


def normalized_histogram(histogram: dict[object, int]) -> dict[object, float]:
    """Return probability mass for every histogram bin."""
    total = histogram_total(histogram)
    if not total:
        return {}

    return {key: value / total for key, value in histogram.items()}


def jensen_shannon_divergence(left: dict[object, int], right: dict[object, int]) -> float | None:
    """Return Jensen-Shannon divergence using log base 2."""
    left_prob = normalized_histogram(left)
    right_prob = normalized_histogram(right)
    keys = set(left_prob) | set(right_prob)
    if not keys:
        return None

    divergence = 0.0

    # Compare both distributions against their midpoint distribution.
    for key in keys:
        left_value = left_prob.get(key, 0.0)
        right_value = right_prob.get(key, 0.0)
        midpoint = (left_value + right_value) / 2.0
        if left_value > 0.0:
            divergence += 0.5 * left_value * math.log(left_value / midpoint, 2)
        if right_value > 0.0:
            divergence += 0.5 * right_value * math.log(right_value / midpoint, 2)

    return round(divergence, 5)


def histogram_wasserstein_distance(
    left: dict[int, int],
    right: dict[int, int],
    bin_width: float,
) -> float | None:
    """Return a one-dimensional Wasserstein distance approximation."""
    left_prob = normalized_histogram(left)
    right_prob = normalized_histogram(right)
    keys = sorted(set(left_prob) | set(right_prob))
    if not keys:
        return None

    distance = 0.0
    cdf_delta = 0.0

    # Summing cumulative mass differences over ordered bins approximates EMD.
    for key in keys:
        cdf_delta += left_prob.get(key, 0.0) - right_prob.get(key, 0.0)
        distance += abs(cdf_delta) * bin_width

    return round(distance, 3)


def stability_status(
    divergence: float | None,
    previous_n: int,
    latest_n: int,
    min_samples: int,
) -> str:
    """Return a compact stability status for a distribution comparison."""
    if previous_n < min_samples or latest_n < min_samples:
        return "thin sample"

    if divergence is None:
        return "unavailable"

    if divergence <= 0.04:
        return "stable"

    if divergence <= 0.10:
        return "watch"

    return "moving"


def binned_histogram(
    session: Session,
    config: dict[str, object],
    start: str,
    end: str,
) -> dict[int, int]:
    """Return SQL-binned counts for one continuous variable and time window."""
    result = rows(
        session,
        f"""
        SELECT
            CAST(({config["value_sql"]} - :min_value) / :bin_width AS INTEGER) AS bin_id,
            COUNT(*) AS observations
        FROM {config["from_sql"]}
        WHERE {config["time_column"]} >= :start
          AND {config["time_column"]} < :end
          AND {config["value_sql"]} IS NOT NULL
          AND {config["value_sql"]} >= :min_value
          AND {config["value_sql"]} < :max_value
          {config["extra_where"]}
        GROUP BY bin_id
        """,
        {
            "start": start,
            "end": end,
            "min_value": config["min_value"],
            "max_value": config["max_value"],
            "bin_width": config["bin_width"],
        },
    )

    return {int(row[0]): int(row[1]) for row in result}


def binned_histograms_by_type(
    session: Session,
    config: dict[str, object],
    start: str,
    end: str,
) -> dict[int, dict[int, int]]:
    """Return SQL-binned counts grouped by aircraft type."""
    result = rows(
        session,
        f"""
        SELECT
            {config["type_column"]} AS aircraft_type,
            CAST(({config["value_sql"]} - :min_value) / :bin_width AS INTEGER) AS bin_id,
            COUNT(*) AS observations
        FROM {config["from_sql"]}
        WHERE {config["time_column"]} >= :start
          AND {config["time_column"]} < :end
          AND {config["value_sql"]} IS NOT NULL
          AND {config["value_sql"]} >= :min_value
          AND {config["value_sql"]} < :max_value
          {config["extra_where"]}
        GROUP BY {config["type_column"]}, bin_id
        """,
        {
            "start": start,
            "end": end,
            "min_value": config["min_value"],
            "max_value": config["max_value"],
            "bin_width": config["bin_width"],
        },
    )
    histograms = defaultdict(dict)

    # Keep the grouped SQL output compact for client-side charts.
    for row in result:
        if row[0] is None:
            continue
        histograms[int(row[0])][int(row[1])] = int(row[2])

    return dict(histograms)


def histogram_bins(histogram: dict[int, int], config: dict[str, object]) -> list[dict[str, object]]:
    """Return chart-ready histogram bins."""
    bin_width = float(config["bin_width"])
    min_value = float(config["min_value"])

    return [
        {
            "bin": key,
            "label": round(min_value + key * bin_width, 3),
            "count": value,
        }
        for key, value in sorted(histogram.items())
    ]


def compare_distribution(
    session: Session,
    config: dict[str, object],
    previous_start: str,
    latest_start: str,
    latest_end: str,
) -> dict[str, object]:
    """Return stability metrics for one configured distribution."""
    previous = binned_histogram(session, config, previous_start, latest_start)
    latest = binned_histogram(session, config, latest_start, latest_end)
    previous_n = histogram_total(previous)
    latest_n = histogram_total(latest)
    minimum = (
        env.dashboard_stability_min_segments
        if config["min_samples"] == "segments"
        else env.dashboard_stability_min_points
    )
    jsd = jensen_shannon_divergence(previous, latest)
    wasserstein = histogram_wasserstein_distance(previous, latest, float(config["bin_width"]))

    return {
        "name": config["name"],
        "unit": config["unit"],
        "source": config["source"],
        "previous_n": previous_n,
        "latest_n": latest_n,
        "jensen_shannon": jsd,
        "wasserstein": wasserstein,
        "status": stability_status(jsd, previous_n, latest_n, minimum),
    }


def compare_precomputed_distribution(
    config: dict[str, object],
    previous: dict[int, int],
    latest: dict[int, int],
) -> dict[str, object]:
    """Return stability metrics for precomputed histograms."""
    previous_n = histogram_total(previous)
    latest_n = histogram_total(latest)
    minimum = (
        env.dashboard_stability_min_segments
        if config["min_samples"] == "segments"
        else env.dashboard_stability_min_points
    )
    jsd = jensen_shannon_divergence(previous, latest)
    wasserstein = histogram_wasserstein_distance(previous, latest, float(config["bin_width"]))

    return {
        "key": str(config["name"]).lower().replace(" ", "_"),
        "name": config["name"],
        "unit": config["unit"],
        "source": config["source"],
        "previous_n": previous_n,
        "latest_n": latest_n,
        "jensen_shannon": jsd,
        "wasserstein": wasserstein,
        "status": stability_status(jsd, previous_n, latest_n, minimum),
        "previous_bins": histogram_bins(previous, config),
        "latest_bins": histogram_bins(latest, config),
    }


def fetch_spatial_density_stability(
    session: Session,
    previous_start: str,
    latest_start: str,
    latest_end: str,
) -> dict[str, object]:
    """Return stability metrics for spatial density cells."""
    def spatial_histogram(start: str, end: str) -> dict[str, int]:
        """Return binned spatial density counts for one time window."""
        result = rows(
            session,
            f"""
            SELECT
                CAST(ROUND(latitude / :grid) AS INTEGER) || ':' ||
                CAST(ROUND(longitude / :grid) AS INTEGER) AS cell_id,
                COUNT(*) AS observations
            FROM cleaned_observations
            WHERE timestamp >= :start
              AND timestamp < :end
              AND latitude IS NOT NULL
              AND longitude IS NOT NULL
              AND inside_swiss_bbox = 1
              AND is_likely_unconventional = 1
              AND {region_sql()}
            GROUP BY cell_id
            """,
            {
                **region_params(),
                "start": start,
                "end": end,
                "grid": env.dashboard_grid_degrees,
            },
        )

        return {str(row[0]): int(row[1]) for row in result}

    previous = spatial_histogram(previous_start, latest_start)
    latest = spatial_histogram(latest_start, latest_end)
    previous_n = histogram_total(previous)
    latest_n = histogram_total(latest)
    jsd = jensen_shannon_divergence(previous, latest)

    return {
        "name": "Spatial density",
        "unit": "grid",
        "source": "cleaned points",
        "previous_n": previous_n,
        "latest_n": latest_n,
        "jensen_shannon": jsd,
        "wasserstein": None,
        "status": stability_status(
            jsd,
            previous_n,
            latest_n,
            env.dashboard_stability_min_points,
        ),
    }


def fetch_transition_stability(
    session: Session,
    previous_start: str,
    latest_start: str,
    latest_end: str,
) -> dict[str, object]:
    """Return stability metrics for altitude-speed-turn state transitions."""
    def transition_histogram(start: str, end: str) -> dict[str, int]:
        """Return transition counts between coarse motion states."""
        result = rows(
            session,
            f"""
            WITH states AS (
                SELECT
                    aircraft_id,
                    timestamp,
                    CASE
                        WHEN altitude_m < 500 THEN 'low'
                        WHEN altitude_m < 1500 THEN 'mid'
                        ELSE 'high'
                    END AS altitude_state,
                    CASE
                        WHEN ground_speed_kmh < 30 THEN 'slow'
                        WHEN ground_speed_kmh < 120 THEN 'cruise'
                        ELSE 'fast'
                    END AS speed_state,
                    CASE
                        WHEN ABS(COALESCE(turn_rate_degs, 0)) < 5 THEN 'straight'
                        WHEN ABS(COALESCE(turn_rate_degs, 0)) < 20 THEN 'turn'
                        ELSE 'sharp'
                    END AS turn_state
                FROM cleaned_observations
                WHERE timestamp >= :start
                  AND timestamp < :end
                  AND aircraft_id IS NOT NULL
                  AND altitude_m IS NOT NULL
                  AND ground_speed_kmh IS NOT NULL
                  AND inside_swiss_bbox = 1
                  AND is_likely_unconventional = 1
                  AND {region_sql()}
            ),
            transitions AS (
                SELECT
                    LAG(altitude_state || '/' || speed_state || '/' || turn_state)
                        OVER (PARTITION BY aircraft_id ORDER BY timestamp) AS previous_state,
                    altitude_state || '/' || speed_state || '/' || turn_state AS current_state
                FROM states
            )
            SELECT previous_state || ' -> ' || current_state AS transition_id, COUNT(*)
            FROM transitions
            WHERE previous_state IS NOT NULL
            GROUP BY transition_id
            """,
            {**region_params(), "start": start, "end": end},
        )

        return {str(row[0]): int(row[1]) for row in result}

    previous = transition_histogram(previous_start, latest_start)
    latest = transition_histogram(latest_start, latest_end)
    previous_n = histogram_total(previous)
    latest_n = histogram_total(latest)
    jsd = jensen_shannon_divergence(previous, latest)

    return {
        "name": "State transitions",
        "unit": "prob.",
        "source": "cleaned points",
        "previous_n": previous_n,
        "latest_n": latest_n,
        "jensen_shannon": jsd,
        "wasserstein": None,
        "status": stability_status(
            jsd,
            previous_n,
            latest_n,
            env.dashboard_stability_min_points,
        ),
    }


def fetch_type_stability(
    session: Session,
    previous_start: str,
    latest_start: str,
    latest_end: str,
) -> list[dict[str, object]]:
    """Return previous/latest modelling volume by aircraft type."""
    result = rows(
        session,
        """
        SELECT
            aircraft_type_name,
            SUM(CASE WHEN end_timestamp >= :previous_start AND end_timestamp < :latest_start THEN 1 ELSE 0 END),
            SUM(CASE WHEN end_timestamp >= :latest_start AND end_timestamp < :latest_end THEN 1 ELSE 0 END),
            SUM(CASE WHEN end_timestamp >= :previous_start AND end_timestamp < :latest_start THEN n_points ELSE 0 END),
            SUM(CASE WHEN end_timestamp >= :latest_start AND end_timestamp < :latest_end THEN n_points ELSE 0 END)
        FROM track_segments
        WHERE is_likely_unconventional = 1
          AND n_points >= 20
          AND duration_s >= 120
          AND end_timestamp >= :previous_start
          AND end_timestamp < :latest_end
        GROUP BY aircraft_type_name
        ORDER BY SUM(n_points) DESC
        LIMIT 10
        """,
        {
            "previous_start": previous_start,
            "latest_start": latest_start,
            "latest_end": latest_end,
        },
    )

    return [
        {
            "aircraft_type_name": row[0] or "unknown",
            "previous_segments": int(row[1] or 0),
            "latest_segments": int(row[2] or 0),
            "previous_points": int(row[3] or 0),
            "latest_points": int(row[4] or 0),
            "status": stability_status(
                0.0,
                int(row[1] or 0),
                int(row[2] or 0),
                env.dashboard_stability_min_segments,
            ),
        }
        for row in result
    ]


def stability_window_edges(latest_dt: object) -> list[tuple[str, str]]:
    """Return adjacent stability window boundaries ending at the latest data point."""
    window_count = max(env.dashboard_stability_history_windows, 2)
    starts = [
        latest_dt - timedelta(days=env.dashboard_stability_window_days * index)
        for index in range(window_count, -1, -1)
    ]

    return [(starts[index].isoformat(), starts[index + 1].isoformat()) for index in range(window_count)]


def fetch_stability_aircraft_types(
    session: Session,
    start: str,
    end: str,
) -> list[dict[str, object]]:
    """Return unconventional aircraft classes available for stability checks."""
    result = rows(
        session,
        """
        SELECT aircraft_type, aircraft_type_name, COUNT(*) AS observations
        FROM cleaned_observations
        WHERE timestamp >= :start
          AND timestamp < :end
          AND is_likely_unconventional = 1
          AND aircraft_type IS NOT NULL
        GROUP BY aircraft_type, aircraft_type_name
        ORDER BY observations DESC
        """,
        {"start": start, "end": end},
    )

    return [
        {
            "code": int(row[0]),
            "label": row[1] or aircraft_type_name(row[0]),
            "observations": int(row[2] or 0),
        }
        for row in result
    ]


def build_class_distribution_stability(
    session: Session,
    windows: list[tuple[str, str]],
) -> list[dict[str, object]]:
    """Return per-aircraft-class distribution stability and histogram memory."""
    configs = POINT_DISTRIBUTIONS + SEGMENT_DISTRIBUTIONS + TRACK_POINT_DISTRIBUTIONS
    first_start = windows[0][0]
    latest_end = windows[-1][1]
    classes = fetch_stability_aircraft_types(session, first_start, latest_end)
    class_codes = {int(item["code"]) for item in classes}
    window_histograms = {}

    # Each SQL query groups by aircraft type, so classes do not multiply query count.
    for config in configs:
        key = str(config["name"]).lower().replace(" ", "_")
        window_histograms[key] = [
            binned_histograms_by_type(session, config, start, end)
            for start, end in windows
        ]

    class_results = []
    for item in classes:
        code = int(item["code"])
        variables = []
        for config in configs:
            key = str(config["name"]).lower().replace(" ", "_")
            previous = window_histograms[key][-2].get(code, {})
            latest = window_histograms[key][-1].get(code, {})
            variable = compare_precomputed_distribution(config, previous, latest)
            history = []

            # Store a rolling memory of adjacent-window changes for trend charts.
            for index in range(1, len(windows)):
                before = window_histograms[key][index - 1].get(code, {})
                after = window_histograms[key][index].get(code, {})
                comparison = compare_precomputed_distribution(config, before, after)
                history.append(
                    {
                        "start": windows[index][0],
                        "end": windows[index][1],
                        "previous_n": comparison["previous_n"],
                        "latest_n": comparison["latest_n"],
                        "jensen_shannon": comparison["jensen_shannon"],
                        "wasserstein": comparison["wasserstein"],
                        "status": comparison["status"],
                    }
                )

            variable["history"] = history
            variables.append(variable)

        counts = Counter(variable["status"] for variable in variables)
        class_results.append(
            {
                "code": code,
                "label": item["label"],
                "observations": item["observations"],
                "stable_count": counts["stable"],
                "watch_count": counts["watch"],
                "moving_count": counts["moving"],
                "thin_count": counts["thin sample"],
                "variables": variables,
            }
        )

    return [item for item in class_results if int(item["code"]) in class_codes]


def fetch_distribution_stability(session: Session) -> dict[str, object]:
    """Return distribution stability metrics for modelling readiness."""
    if not table_exists(session, "cleaned_observations") or not table_exists(session, "track_segments"):
        return {"available": False, "variables": [], "type_readiness": []}

    latest = one(
        session,
        """
        SELECT MAX(timestamp)
        FROM cleaned_observations
        WHERE timestamp IS NOT NULL
        """,
    )
    latest_dt = parse_iso_timestamp(latest)
    if not latest_dt:
        return {"available": False, "variables": [], "type_readiness": []}

    windows = stability_window_edges(latest_dt)
    previous_start = windows[-2][0]
    latest_start = windows[-1][0]
    latest_end = latest_dt.isoformat()
    classes = build_class_distribution_stability(session, windows)
    variables = classes[0]["variables"] if classes else []

    # Keep aggregate spatial and transition checks as context beside class-specific variables.
    aggregate_variables = [
        fetch_spatial_density_stability(session, previous_start, latest_start, latest_end),
        fetch_transition_stability(session, previous_start, latest_start, latest_end),
    ]
    counts = Counter()
    for item in classes:
        counts.update(
            {
                "stable": item["stable_count"],
                "watch": item["watch_count"],
                "moving": item["moving_count"],
                "thin sample": item["thin_count"],
            }
        )

    return {
        "available": True,
        "window_days": env.dashboard_stability_window_days,
        "history_windows": env.dashboard_stability_history_windows,
        "previous_start": previous_start,
        "latest_start": latest_start,
        "latest_end": latest_end,
        "stable_count": counts["stable"],
        "watch_count": counts["watch"],
        "moving_count": counts["moving"],
        "thin_count": counts["thin sample"],
        "variables": variables,
        "aggregate_variables": aggregate_variables,
        "classes": classes,
        "type_readiness": fetch_type_stability(session, previous_start, latest_start, latest_end),
    }


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
        "all_time_dropout_hotspots": fetch_dropout_hotspots(session, None),
        "aircraft_types": fetch_aircraft_type_counts(session, since),
        "beacons": fetch_beacon_counts(session, since),
        "top_aircraft": fetch_top_aircraft(session, since),
        "quality_tracks": fetch_quality_tracks(session, since),
        "engineering": engineering,
        "best_trajectories": fetch_best_trajectories(session),
        "distribution_stability": fetch_distribution_stability(session),
        "window_since": since,
        "window_hours": env.dashboard_window_hours,
        "grid_degrees": env.dashboard_grid_degrees,
        "refresh_seconds": env.dashboard_refresh_seconds,
    }
