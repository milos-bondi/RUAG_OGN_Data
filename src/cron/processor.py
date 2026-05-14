"""Incremental OGN observation cleaning and track processing."""

from __future__ import annotations

import json
import threading
import time

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.envs import env
from src.db.session import SessionLocal, create_tables
from src.db.models.ogn import CleanedObservation, PositionObservation, ProcessingState, TrackPoint
from src.db.models.ogn import TrackSegment
from src.utils.geo import aircraft_type_name, bearing_deg, haversine_m, inside_swiss_bbox
from src.utils.geo import is_likely_unconventional_type, parse_iso_timestamp, seconds_between
from src.utils.geo import speed_limit_for_type
from src.utils.time import utc_now_iso


STATE_KEY = "process_ogn_data.last_position_observation_id"


def get_state(session: Session, key: str, default: str = "0") -> str:
    """Return a processing state value."""
    row = session.get(ProcessingState, key)
    if not row:
        return default

    return row.value


def set_state(session: Session, key: str, value: str) -> None:
    """Persist a processing state value."""
    row = session.get(ProcessingState, key)
    if not row:
        row = ProcessingState(key=key, value=value, updated_at=utc_now_iso())
        session.add(row)
        return

    row.value = value
    row.updated_at = utc_now_iso()


def quality_flags(row: PositionObservation) -> list[str]:
    """Return quality flags for a raw position observation."""
    flags = []
    parsed_timestamp = parse_iso_timestamp(row.timestamp)

    if not row.aircraft_id:
        flags.append("missing_aircraft_id")
    if parsed_timestamp is None:
        flags.append("invalid_timestamp")
    if row.latitude is None or row.longitude is None:
        flags.append("missing_coordinates")
    elif row.latitude < -90 or row.latitude > 90 or row.longitude < -180 or row.longitude > 180:
        flags.append("invalid_coordinates")
    if row.beacon_type == "receiver":
        flags.append("receiver_beacon")
    if row.aircraft_type == 14:
        flags.append("static_object")
    if not inside_swiss_bbox(row.latitude, row.longitude):
        flags.append("outside_swiss_bbox")
    if row.altitude_m is not None and (row.altitude_m < -500 or row.altitude_m > 15000):
        flags.append("altitude_out_of_range")

    # Speed and climb filters are type-aware enough to preserve slow glider traffic.
    if row.ground_speed_kmh is not None:
        if row.ground_speed_kmh < 0:
            flags.append("negative_speed")
        if row.ground_speed_kmh > speed_limit_for_type(row.aircraft_type):
            flags.append("speed_out_of_range_for_type")
    if row.climb_rate_ms is not None and abs(row.climb_rate_ms) > 50:
        flags.append("climb_rate_out_of_range")

    return flags


def should_keep(flags: list[str]) -> bool:
    """Return whether an observation should enter processed tables."""
    blocking = {
        "missing_aircraft_id",
        "invalid_timestamp",
        "missing_coordinates",
        "invalid_coordinates",
    }
    if blocking.intersection(flags):
        return False

    if not env.include_outside_swiss and "outside_swiss_bbox" in flags:
        return False

    if not env.include_receivers and "receiver_beacon" in flags:
        return False

    if not env.include_static and "static_object" in flags:
        return False

    return True


def insert_cleaned_observation(
    session: Session,
    row: PositionObservation,
    flags: list[str],
) -> CleanedObservation | None:
    """Insert a cleaned observation or return the existing row."""
    existing = session.scalar(
        select(CleanedObservation).where(CleanedObservation.position_observation_id == row.id)
    )
    if existing:
        return existing

    if not row.aircraft_id or not row.timestamp or row.latitude is None or row.longitude is None:
        return None

    cleaned = CleanedObservation(
        position_observation_id=row.id,
        raw_message_id=row.raw_message_id,
        aircraft_id=row.aircraft_id,
        device_address=row.device_address,
        beacon_type=row.beacon_type,
        aircraft_type=row.aircraft_type,
        aircraft_type_name=aircraft_type_name(row.aircraft_type),
        timestamp=row.timestamp,
        latitude=row.latitude,
        longitude=row.longitude,
        altitude_m=row.altitude_m,
        ground_speed_kmh=row.ground_speed_kmh,
        track_deg=row.track_deg,
        climb_rate_ms=row.climb_rate_ms,
        turn_rate_degs=row.turn_rate_degs,
        inside_swiss_bbox=int(inside_swiss_bbox(row.latitude, row.longitude)),
        is_likely_unconventional=int(is_likely_unconventional_type(row.aircraft_type)),
        quality_flags=json.dumps(flags),
    )
    session.add(cleaned)
    session.flush()

    return cleaned


def fetch_latest_track_point(
    session: Session,
    aircraft_id: str,
) -> tuple[TrackPoint, CleanedObservation] | None:
    """Return the latest track point and cleaned observation for an aircraft."""
    statement = (
        select(TrackPoint, CleanedObservation)
        .join(CleanedObservation, CleanedObservation.id == TrackPoint.cleaned_observation_id)
        .where(CleanedObservation.aircraft_id == aircraft_id)
        .order_by(desc(CleanedObservation.timestamp), desc(CleanedObservation.id))
        .limit(1)
    )

    return session.execute(statement).first()


def start_segment(session: Session, cleaned: CleanedObservation) -> TrackSegment:
    """Start a new track segment from a cleaned observation."""
    segment = TrackSegment(
        aircraft_id=cleaned.aircraft_id,
        aircraft_type=cleaned.aircraft_type,
        aircraft_type_name=cleaned.aircraft_type_name,
        beacon_type=cleaned.beacon_type,
        start_cleaned_observation_id=cleaned.id,
        end_cleaned_observation_id=cleaned.id,
        start_timestamp=cleaned.timestamp,
        end_timestamp=cleaned.timestamp,
        n_points=1,
        duration_s=0.0,
        max_gap_s=0.0,
        distance_km=0.0,
        min_altitude_m=cleaned.altitude_m,
        max_altitude_m=cleaned.altitude_m,
        avg_ground_speed_kmh=cleaned.ground_speed_kmh,
        max_ground_speed_kmh=cleaned.ground_speed_kmh,
        is_likely_unconventional=cleaned.is_likely_unconventional,
    )
    session.add(segment)
    session.flush()
    session.add(
        TrackPoint(
            segment_id=segment.id,
            cleaned_observation_id=cleaned.id,
            sequence_index=0,
        )
    )

    return segment


def append_to_segment(
    session: Session,
    cleaned: CleanedObservation,
    previous_point: TrackPoint,
    previous_cleaned: CleanedObservation,
) -> None:
    """Append a cleaned observation to an existing track segment."""
    segment = session.get(TrackSegment, previous_point.segment_id)
    if not segment:
        start_segment(session, cleaned)
        return

    dt_s = seconds_between(
        parse_iso_timestamp(previous_cleaned.timestamp),
        parse_iso_timestamp(cleaned.timestamp),
    )
    distance_m = haversine_m(
        previous_cleaned.latitude,
        previous_cleaned.longitude,
        cleaned.latitude,
        cleaned.longitude,
    )
    estimated_speed_kmh = distance_m / dt_s * 3.6 if dt_s and dt_s > 0 else None
    estimated_heading = bearing_deg(
        previous_cleaned.latitude,
        previous_cleaned.longitude,
        cleaned.latitude,
        cleaned.longitude,
    ) if dt_s and dt_s > 0 else None
    estimated_climb = None

    # Climb can only be derived when both observations have altitude.
    if (
        dt_s
        and dt_s > 0
        and previous_cleaned.altitude_m is not None
        and cleaned.altitude_m is not None
    ):
        estimated_climb = (cleaned.altitude_m - previous_cleaned.altitude_m) / dt_s

    session.add(
        TrackPoint(
            segment_id=segment.id,
            cleaned_observation_id=cleaned.id,
            sequence_index=previous_point.sequence_index + 1,
            dt_s=dt_s,
            distance_m=distance_m,
            estimated_ground_speed_kmh=estimated_speed_kmh,
            estimated_heading_deg=estimated_heading,
            estimated_climb_rate_ms=estimated_climb,
        )
    )

    segment.end_cleaned_observation_id = cleaned.id
    segment.end_timestamp = cleaned.timestamp
    segment.n_points += 1
    segment.duration_s = seconds_between(
        parse_iso_timestamp(segment.start_timestamp),
        parse_iso_timestamp(cleaned.timestamp),
    ) or 0.0
    segment.max_gap_s = max(segment.max_gap_s, dt_s or 0.0)
    segment.distance_km += distance_m / 1000.0

    # Segment altitude and speed aggregates are maintained incrementally.
    if cleaned.altitude_m is not None:
        segment.min_altitude_m = cleaned.altitude_m if segment.min_altitude_m is None else min(
            segment.min_altitude_m,
            cleaned.altitude_m,
        )
        segment.max_altitude_m = cleaned.altitude_m if segment.max_altitude_m is None else max(
            segment.max_altitude_m,
            cleaned.altitude_m,
        )
    if cleaned.ground_speed_kmh is not None:
        segment.avg_ground_speed_kmh = (
            cleaned.ground_speed_kmh
            if segment.avg_ground_speed_kmh is None
            else (
                (segment.avg_ground_speed_kmh * (segment.n_points - 1))
                + cleaned.ground_speed_kmh
            )
            / segment.n_points
        )
        segment.max_ground_speed_kmh = (
            cleaned.ground_speed_kmh
            if segment.max_ground_speed_kmh is None
            else max(segment.max_ground_speed_kmh, cleaned.ground_speed_kmh)
        )


def assign_track_point(session: Session, cleaned: CleanedObservation) -> str:
    """Assign a cleaned observation to a track segment."""
    previous = fetch_latest_track_point(session, cleaned.aircraft_id)
    if not previous:
        start_segment(session, cleaned)
        return "new_segment"

    previous_point, previous_cleaned = previous
    dt_s = seconds_between(
        parse_iso_timestamp(previous_cleaned.timestamp),
        parse_iso_timestamp(cleaned.timestamp),
    )
    distance_m = haversine_m(
        previous_cleaned.latitude,
        previous_cleaned.longitude,
        cleaned.latitude,
        cleaned.longitude,
    )
    estimated_speed_kmh = distance_m / dt_s * 3.6 if dt_s and dt_s > 0 else None

    if dt_s is None or dt_s <= 0:
        start_segment(session, cleaned)
        return "new_segment_non_monotonic_time"

    if dt_s > env.segment_gap_seconds:
        start_segment(session, cleaned)
        return "new_segment_gap"

    if estimated_speed_kmh is not None and estimated_speed_kmh > env.max_jump_speed_kmh:
        start_segment(session, cleaned)
        return "new_segment_jump"

    append_to_segment(session, cleaned, previous_point, previous_cleaned)
    return "appended"


def process_once(session: Session) -> dict[str, int]:
    """Process one batch of raw position observations."""
    last_position_id = int(get_state(session, STATE_KEY, "0"))
    statement = (
        select(PositionObservation)
        .where(PositionObservation.id > last_position_id)
        .order_by(PositionObservation.id)
        .limit(env.processor_batch_size)
    )
    rows = list(session.scalars(statement))
    counts = {
        "seen": 0,
        "kept": 0,
        "dropped": 0,
        "new_segments": 0,
        "appended": 0,
        "last_position_id": last_position_id,
    }

    for row in rows:
        counts["seen"] += 1
        flags = quality_flags(row)
        if should_keep(flags):
            cleaned = insert_cleaned_observation(session, row, flags)
            if cleaned:
                action = assign_track_point(session, cleaned)
                counts["kept"] += 1
                if action.startswith("new_segment"):
                    counts["new_segments"] += 1
                elif action == "appended":
                    counts["appended"] += 1
        else:
            counts["dropped"] += 1

        counts["last_position_id"] = row.id

    if rows:
        set_state(session, STATE_KEY, str(counts["last_position_id"]))
        set_state(session, "process_ogn_data.segment_gap_seconds", str(env.segment_gap_seconds))
        set_state(session, "process_ogn_data.max_jump_speed_kmh", str(env.max_jump_speed_kmh))
        session.commit()

    return counts


class ProcessorService:
    """Run incremental processing on a schedule."""

    def __init__(self) -> None:
        """Initialize processor runtime state."""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the processor in a background thread."""
        if self.thread and self.thread.is_alive():
            return

        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        """Run processing batches until stopped."""
        create_tables()

        while not self.stop_event.is_set():
            session = SessionLocal()
            try:
                while not self.stop_event.is_set():
                    counts = process_once(session)
                    if counts["seen"]:
                        print(
                            f"{utc_now_iso()} processed seen={counts['seen']} "
                            f"kept={counts['kept']} dropped={counts['dropped']} "
                            f"new_segments={counts['new_segments']} appended={counts['appended']} "
                            f"last_position_id={counts['last_position_id']}",
                            flush=True,
                        )

                    if counts["seen"] < env.processor_batch_size:
                        break
            except Exception as exc:
                session.rollback()
                print(f"{utc_now_iso()} processor_error={exc}", flush=True)
            finally:
                session.close()

            self.stop_event.wait(env.processor_interval_seconds)

    def stop(self) -> None:
        """Stop the processor thread."""
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
