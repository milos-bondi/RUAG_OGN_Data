"""Live OGN data collection job."""

from __future__ import annotations

from types import SimpleNamespace

from ogn.client import AprsClient
from ogn.client import settings as client_settings
from ogn.parser import AprsParseError, parse
from sqlalchemy.orm import Session

from src.envs import env
from src.db.session import SessionLocal, create_tables
from src.db.models.ogn import PositionObservation, RawMessage
from src.utils.time import utc_now_iso


UNCONVENTIONAL_BEACON_TYPES = {"flarm", "fanet", "naviter"}
UNCONVENTIONAL_PREFIXES = ("FLR", "FNT", "NAV", "XCT", "FDB", "FD9")


def build_client_settings() -> SimpleNamespace:
    """Build ogn-client settings with the configured APRS server host."""
    return SimpleNamespace(
        APRS_SERVER_HOST=env.aprs_server_host,
        APRS_SERVER_PORT_FULL_FEED=client_settings.APRS_SERVER_PORT_FULL_FEED,
        APRS_SERVER_PORT_CLIENT_DEFINED_FILTERS=client_settings.APRS_SERVER_PORT_CLIENT_DEFINED_FILTERS,
        APRS_APP_NAME=client_settings.APRS_APP_NAME,
        APRS_APP_VER=client_settings.APRS_APP_VER,
        APRS_KEEPALIVE_TIME=client_settings.APRS_KEEPALIVE_TIME,
        TELNET_SERVER_HOST=client_settings.TELNET_SERVER_HOST,
        TELNET_SERVER_PORT=client_settings.TELNET_SERVER_PORT,
    )


def is_likely_unconventional(beacon: dict) -> bool:
    """Return whether a parsed beacon matches the unconventional collection profile."""
    if beacon.get("aprs_type") != "position":
        return False

    if beacon.get("beacon_type") == "receiver":
        return False

    if beacon.get("name", "").startswith("ICA"):
        return False

    beacon_type = beacon.get("beacon_type")
    aircraft_id = beacon.get("name", "")

    # The profile keeps FLARM/FANET/Naviter-like traffic and excludes high/fast outliers.
    has_type = beacon_type in UNCONVENTIONAL_BEACON_TYPES
    has_prefix = aircraft_id.startswith(UNCONVENTIONAL_PREFIXES)
    if not has_type and not has_prefix:
        return False

    altitude_m = beacon.get("altitude")
    if altitude_m is not None and altitude_m > env.max_altitude_m:
        return False

    ground_speed_kmh = beacon.get("ground_speed")
    if ground_speed_kmh is not None and ground_speed_kmh > env.max_speed_kmh:
        return False

    return True


def store_raw_message(session: Session, raw_message: str, status: str, error: str | None) -> RawMessage:
    """Store one raw APRS message."""
    row = RawMessage(
        received_at=utc_now_iso(),
        raw_message=raw_message,
        parse_status=status,
        parse_error=error,
    )
    session.add(row)
    session.flush()

    return row


def store_position(session: Session, raw: RawMessage, beacon: dict) -> None:
    """Store one parsed position observation."""
    timestamp = beacon.get("timestamp")
    row = PositionObservation(
        raw_message_id=raw.id,
        aircraft_id=beacon.get("name"),
        device_address=beacon.get("address"),
        beacon_type=beacon.get("beacon_type"),
        receiver_name=beacon.get("receiver_name"),
        timestamp=timestamp.isoformat() if timestamp else None,
        latitude=beacon.get("latitude"),
        longitude=beacon.get("longitude"),
        altitude_m=beacon.get("altitude"),
        ground_speed_kmh=beacon.get("ground_speed"),
        track_deg=beacon.get("track"),
        climb_rate_ms=beacon.get("climb_rate"),
        turn_rate_degs=beacon.get("turn_rate"),
        aircraft_type=beacon.get("aircraft_type"),
        no_tracking=int(beacon["no-tracking"]) if "no-tracking" in beacon else None,
        stealth=int(beacon["stealth"]) if "stealth" in beacon else None,
    )
    session.add(row)


def handle_message(session: Session, counters: dict[str, int], raw_message: str) -> None:
    """Parse and persist one APRS message."""
    counters["seen"] += 1

    try:
        beacon = parse(raw_message)
    except (AprsParseError, ValueError) as exc:
        counters["errors"] += 1
        if env.collection_profile == "all":
            store_raw_message(session, raw_message, "parse_error", str(exc))
    else:
        if env.collection_profile == "unconventional" and not is_likely_unconventional(beacon):
            counters["filtered"] += 1
            return

        raw = store_raw_message(session, raw_message, "parsed", None)
        counters["saved"] += 1
        if beacon.get("aprs_type") == "position":
            counters["positions"] += 1
            store_position(session, raw, beacon)

    # Batching keeps SQLite write overhead controlled during live collection.
    if counters["seen"] % env.commit_every == 0:
        session.commit()
        print(
            f"{utc_now_iso()} seen={counters['seen']} saved={counters['saved']} "
            f"positions={counters['positions']} filtered={counters['filtered']} "
            f"errors={counters['errors']}",
            flush=True,
        )


def run_collector() -> None:
    """Run the live OGN collector until interrupted."""
    create_tables()
    session = SessionLocal()
    counters = {"seen": 0, "saved": 0, "positions": 0, "filtered": 0, "errors": 0}
    client = AprsClient(
        aprs_user=env.aprs_user,
        aprs_filter=env.aprs_filter,
        settings=build_client_settings(),
    )

    try:
        print(
            f"Listening to {env.aprs_server_host} with filter {env.aprs_filter}. "
            f"Writing to {env.database_url}.",
            flush=True,
        )
        client.connect(retries=100, wait_period=15)
        client.run(callback=lambda raw_message: handle_message(session, counters, raw_message), autoreconnect=True)
    except KeyboardInterrupt:
        print("Stopping collector.", flush=True)
    finally:
        session.commit()
        session.close()
        if getattr(client, "sock", None):
            client.disconnect()


if __name__ == "__main__":
    run_collector()
