"""Cached HTML dashboard route."""

from __future__ import annotations

import json
import threading

from html import escape
from time import monotonic

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.envs import env
from src.db.session import SessionLocal
from src.utils.time import utc_now_iso
from src.utils.geo import SWITZERLAND_BBOX
from src.db.functions.dashboard import dashboard_snapshot


router = APIRouter()


def fmt(value: float | int | None, digits: int = 0) -> str:
    """Format a dashboard number."""
    if value is None:
        return ""

    return f"{value:,.{digits}f}"


def fmt_int(value: float | int | None) -> str:
    """Format a dashboard integer."""
    if value is None:
        return "0"

    return f"{value:,.0f}"


def pct_bar(value: float, total: float) -> str:
    """Return a bounded width percentage for bar cells."""
    if total <= 0:
        return "0"

    return f"{min(max(value / total * 100.0, 0.0), 100.0):.1f}"


def metric(label: str, value: object) -> str:
    """Render one dashboard metric."""
    return f"""
      <div class="metric">
        <div class="metric-label">{escape(label)}</div>
        <div class="metric-value">{escape(str(value))}</div>
      </div>
    """


def badge(enabled: bool) -> str:
    """Render the unconventional marker."""
    if not enabled:
        return ""

    return ' <span class="badge">unconv.</span>'


def render_aircraft_type_rows(items: list[dict[str, object]]) -> str:
    """Render raw aircraft type rows."""
    total = sum(int(item["observations"] or 0) for item in items) or 1
    rows = []

    # Keep the same information density as the old generated dashboard.
    for item in items:
        observations = int(item["observations"] or 0)
        code = "unknown" if item["code"] is None else str(item["code"])
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["label"]))}{badge(bool(item["unconventional"]))}</td>
              <td>{escape(code)}</td>
              <td class="num">{fmt_int(observations)}</td>
              <td class="num">{fmt_int(int(item["unique_aircraft"] or 0))}</td>
              <td><div class="bar"><span style="width:{pct_bar(observations, total)}%"></span></div></td>
              <td class="num">{pct_bar(observations, total)}%</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="6" class="empty">No aircraft type data yet.</td></tr>'


def render_beacon_rows(items: list[dict[str, object]]) -> str:
    """Render beacon rows."""
    total = sum(int(item["observations"] or 0) for item in items) or 1
    rows = []

    # Beacon bars preserve proportional context without extra client work.
    for item in items:
        observations = int(item["observations"] or 0)
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["beacon_type"]))}</td>
              <td class="num">{fmt_int(observations)}</td>
              <td><div class="bar"><span style="width:{pct_bar(observations, total)}%"></span></div></td>
              <td class="num">{pct_bar(observations, total)}%</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="4" class="empty">No beacon data yet.</td></tr>'


def render_top_aircraft_rows(items: list[dict[str, object]]) -> str:
    """Render top aircraft rows."""
    rows = [
        f"""
        <tr>
          <td>{escape(str(item["aircraft_id"]))}</td>
          <td class="num">{fmt_int(int(item["observations"] or 0))}</td>
        </tr>
        """
        for item in items
    ]

    return "\n".join(rows) or '<tr><td colspan="2" class="empty">No aircraft data yet.</td></tr>'


def render_engineering_type_rows(items: list[dict[str, object]]) -> str:
    """Render processed aircraft type rows."""
    total = sum(int(item["points"] or 0) for item in items) or 1
    rows = []

    # Processed rows are point-weighted because they represent modelling data volume.
    for item in items:
        points = int(item["points"] or 0)
        code = "unknown" if item["aircraft_type"] is None else str(item["aircraft_type"])
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["aircraft_type_name"]))}{badge(bool(item["unconventional"]))}</td>
              <td>{escape(code)}</td>
              <td class="num">{fmt_int(int(item["segments"] or 0))}</td>
              <td class="num">{fmt_int(points)}</td>
              <td class="num">{fmt_int(int(item["unconventional_segments"] or 0))}</td>
              <td><div class="bar"><span style="width:{pct_bar(points, total)}%"></span></div></td>
              <td class="num">{pct_bar(points, total)}%</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="7" class="empty">No processed segments yet.</td></tr>'


def render_good_type_rows(items: list[dict[str, object]]) -> str:
    """Render good processed segment rows."""
    total = sum(int(item["points"] or 0) for item in items) or 1
    rows = []

    # Good segment rows show the model-ready subset.
    for item in items:
        points = int(item["points"] or 0)
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["aircraft_type_name"]))}{badge(bool(item["unconventional"]))}</td>
              <td class="num">{fmt_int(int(item["segments"] or 0))}</td>
              <td class="num">{fmt_int(points)}</td>
              <td class="num">{fmt(item["avg_duration_min"], 1)}</td>
              <td><div class="bar"><span style="width:{pct_bar(points, total)}%"></span></div></td>
              <td class="num">{pct_bar(points, total)}%</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="6" class="empty">No good segments yet.</td></tr>'


def render_recent_segment_rows(items: list[dict[str, object]]) -> str:
    """Render recent processed segments."""
    rows = []

    # Segment IDs remain clickable for on-demand trajectory point loading.
    for item in items:
        rows.append(
            f"""
            <tr data-segment-id="{int(item["id"])}">
              <td>{int(item["id"])}</td>
              <td>{escape(str(item["aircraft_id"]))}</td>
              <td>{escape(str(item["aircraft_type_name"]))}{badge(bool(item["unconventional"]))}</td>
              <td>{escape(str(item["beacon_type"]))}</td>
              <td class="num">{fmt_int(int(item["points"] or 0))}</td>
              <td class="num">{fmt(item["duration_min"], 1)}</td>
              <td class="num">{fmt(item["max_gap_s"], 1)}</td>
              <td class="num">{fmt(item["distance_km"], 1)}</td>
              <td class="num">{fmt(item["avg_speed_kmh"], 1)}</td>
              <td>{escape(str(item["end_timestamp"] or ""))}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="10" class="empty">No processed segments yet.</td></tr>'


def render_quality_track_rows(items: list[dict[str, object]]) -> str:
    """Render raw quality track rows."""
    rows = []

    # These rows mirror the old raw-observation quality track table.
    for item in items:
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["aircraft_id"]))}</td>
              <td>{escape(str(item["aircraft_type_name"]))}{badge(bool(item["unconventional"]))}</td>
              <td>{escape(str(item["beacon_type"]))}</td>
              <td class="num">{fmt_int(int(item["points"] or 0))}</td>
              <td class="num">{fmt(item["duration_min"], 1)}</td>
              <td class="num">{fmt(item["avg_gap_s"], 1)}</td>
              <td class="num">{fmt(item["max_gap_s"], 1)}</td>
              <td class="num">{fmt(item["avg_altitude_m"], 0)}</td>
              <td class="num">{fmt(item["avg_speed_kmh"], 1)}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="9" class="empty">No raw quality tracks yet.</td></tr>'


def render_best_trajectory_rows(items: list[dict[str, object]]) -> str:
    """Render best trajectory rows."""
    rows = []

    # Rank is precomputed by the snapshot query and shown in stable order.
    for index, item in enumerate(items, start=1):
        segment_id = int(item["id"])
        rows.append(
            f"""
            <tr data-segment-id="{segment_id}">
              <td><button class="view-track" type="button" data-segment-id="{segment_id}">View</button></td>
              <td class="num">{index}</td>
              <td class="num">{fmt(item["quality_score"], 1)}</td>
              <td>{escape(str(item["aircraft_id"]))}</td>
              <td>{escape(str(item["aircraft_type_name"]))}</td>
              <td>{escape(str(item["beacon_type"]))}</td>
              <td class="num">{fmt_int(int(item["points"] or 0))}</td>
              <td class="num">{fmt(item["distance_km"], 1)}</td>
              <td class="num">{fmt(item["duration_min"], 1)}</td>
              <td class="num">{fmt(item["max_gap_s"], 1)}</td>
              <td class="num">{fmt(item["avg_speed_kmh"], 1)}</td>
              <td>{escape(str(item["end_timestamp"] or ""))}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="12" class="empty">No qualifying unconventional trajectories yet.</td></tr>'


def render_hotspot_rows(items: list[dict[str, object]]) -> str:
    """Render dropout hotspot rows."""
    rows = []

    # Hotspots are ranked by the same order used by the snapshot query.
    for item in items[:12]:
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["bias_hint"]))}</td>
              <td class="num">{fmt(float(item["dropout_rate"]) * 100.0, 2)}%</td>
              <td class="num">{fmt_int(int(item["dropout_count"] or 0))}</td>
              <td class="num">{fmt_int(int(item["transition_count"] or 0))}</td>
              <td class="num">{fmt_int(int(item["unique_aircraft"] or 0))}</td>
              <td class="num">{fmt_int(int(item["unique_receivers"] or 0))}</td>
              <td>{escape(str(item["dominant_aircraft_type"]))}</td>
              <td>{escape(str(item["dominant_altitude_band"]))}</td>
              <td>{escape(str(item["top_receiver"] or ""))}</td>
              <td class="num">{fmt(float(item["top_receiver_share"]) * 100.0, 0)}%</td>
              <td class="num">{fmt(item["p95_dropout_gap_s"], 1)}</td>
              <td class="num">{fmt(item["max_dropout_gap_s"], 1)}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="12" class="empty">No dropout hotspots yet.</td></tr>'


def stability_class(status: str) -> str:
    """Return a CSS class for a stability status."""
    if status == "stable":
        return "stable"

    if status == "watch":
        return "watch"

    if status == "moving":
        return "moving"

    return "thin"


def status_pill(status: object) -> str:
    """Render a colored stability status pill."""
    label = str(status or "unavailable")
    return f'<span class="status-pill {stability_class(label)}">{escape(label)}</span>'


def render_stability_rows(items: list[dict[str, object]]) -> str:
    """Render distribution stability rows."""
    rows = []

    # The table compares fixed-bin histograms from adjacent time windows.
    for item in items:
        wasserstein = item["wasserstein"]
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["name"]))}</td>
              <td>{escape(str(item["source"]))}</td>
              <td>{escape(str(item["unit"]))}</td>
              <td class="num">{fmt_int(int(item["previous_n"] or 0))}</td>
              <td class="num">{fmt_int(int(item["latest_n"] or 0))}</td>
              <td class="num">{fmt(item["jensen_shannon"], 5)}</td>
              <td class="num">{fmt(wasserstein, 3) if wasserstein is not None else ""}</td>
              <td>{status_pill(item["status"])}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="8" class="empty">No stability data yet.</td></tr>'


def render_type_stability_rows(items: list[dict[str, object]]) -> str:
    """Render aircraft-type modelling readiness rows."""
    rows = []

    # This is a volume check, not a distribution test.
    for item in items:
        rows.append(
            f"""
            <tr>
              <td>{escape(str(item["aircraft_type_name"]))}</td>
              <td class="num">{fmt_int(int(item["previous_segments"] or 0))}</td>
              <td class="num">{fmt_int(int(item["latest_segments"] or 0))}</td>
              <td class="num">{fmt_int(int(item["previous_points"] or 0))}</td>
              <td class="num">{fmt_int(int(item["latest_points"] or 0))}</td>
              <td>{status_pill(item["status"])}</td>
            </tr>
            """
        )

    return "\n".join(rows) or '<tr><td colspan="6" class="empty">No type stability data yet.</td></tr>'


def render_json(snapshot: dict[str, object]) -> str:
    """Render JSON data embedded for client-side map filtering."""
    payload = {
        "densityCells": snapshot["density_cells"],
        "dropoutCandidates": snapshot["dropout_candidates"],
        "dropoutHotspots": snapshot["dropout_hotspots"],
        "allTimeDropoutHotspots": snapshot["all_time_dropout_hotspots"],
        "gridDegrees": snapshot["grid_degrees"],
        "distributionStability": snapshot["distribution_stability"],
    }

    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def render_dashboard_html(snapshot: dict[str, object]) -> str:
    """Render the complete dashboard HTML."""
    summary = snapshot["summary"]
    engineering = snapshot["engineering"]
    stability = snapshot["distribution_stability"]
    data_json = render_json(snapshot)
    generated_at = utc_now_iso()
    refresh_seconds = int(snapshot["refresh_seconds"])
    window_hours = float(snapshot["window_hours"])
    window_since = snapshot["window_since"]
    window_label = f"last {window_hours:g}h" if window_since else "all available time"
    processor_lag = max(
        int(summary["position_observations"] or 0)
        - int(engineering["last_processed_position_id"] or 0),
        0,
    )
    collection_status = "enabled" if env.collect_on_startup else "paused"
    processor_status = "enabled" if env.process_on_startup else "paused"
    region_label = (
        f"Switzerland bbox {SWITZERLAND_BBOX['min_lat']:.2f}/{SWITZERLAND_BBOX['min_lon']:.2f}"
        f" to {SWITZERLAND_BBOX['max_lat']:.2f}/{SWITZERLAND_BBOX['max_lon']:.2f}"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{refresh_seconds}">
  <meta name="generated-at" content="{escape(generated_at)}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RUAG OGN Dashboard</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
	  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
	  <style>
	    :root {{
	      color-scheme: dark;
	      --ink: #e7edf3;
	      --muted: #8fa1b3;
	      --line: #263648;
	      --line-strong: #38506a;
	      --bg: #08111b;
	      --panel: #101b27;
	      --panel-2: #142334;
	      --panel-3: #0d1722;
	      --accent: #4ea1ff;
	      --accent-soft: rgba(78, 161, 255, 0.16);
	      --green: #4fd18b;
	      --red: #ff5f57;
	      --amber: #f6b34b;
	      --teal: #44d2c8;
	      --navy: #0b1622;
	      --shadow: 0 18px 50px rgba(0, 0, 0, 0.34);
	    }}
	    * {{ box-sizing: border-box; }}
	    body {{
	      margin: 0;
	      font-family: "Aptos", "Segoe UI", sans-serif;
	      color: var(--ink);
	      background:
	        radial-gradient(circle at 15% -10%, rgba(78, 161, 255, 0.18), transparent 34%),
	        radial-gradient(circle at 90% 0%, rgba(68, 210, 200, 0.11), transparent 32%),
	        linear-gradient(180deg, #07111b 0%, #0a1320 46%, #08111b 100%);
	      min-height: 100vh;
	    }}
	    header {{
	      padding: 18px 28px 14px;
	      background: rgba(10, 19, 30, 0.88);
	      border-bottom: 1px solid rgba(78, 161, 255, 0.18);
	      backdrop-filter: blur(18px);
	      box-shadow: 0 10px 36px rgba(0, 0, 0, 0.24);
	      position: sticky;
	      top: 0;
	      z-index: 1000;
	    }}
	    h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 760; letter-spacing: 0; }}
	    .subtle {{ color: var(--muted); font-size: 13px; }}
	    .header-grid {{ display: grid; grid-template-columns: 1fr auto; gap: 18px; align-items: start; }}
	    .status-strip {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
	    .status-chip {{
	      display: grid;
	      gap: 2px;
	      min-width: 118px;
	      border: 1px solid rgba(78, 161, 255, 0.22);
	      border-radius: 8px;
	      padding: 8px 10px;
	      background: linear-gradient(180deg, rgba(20, 35, 52, 0.92), rgba(13, 23, 34, 0.92));
	      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
	    }}
	    .chip-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; }}
	    .chip-value {{ font-size: 13px; font-weight: 760; }}
	    .chip-value.ok {{ color: var(--green); }}
	    .chip-value.warn {{ color: var(--amber); }}
	    .nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
	    .nav a {{
	      border: 1px solid rgba(78, 161, 255, 0.2);
	      border-radius: 6px;
	      padding: 7px 10px;
	      color: var(--ink);
	      background: rgba(20, 35, 52, 0.72);
	      text-decoration: none;
	      font-size: 13px;
	      font-weight: 650;
	      transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
	    }}
	    .nav a:hover {{ border-color: var(--accent); background: var(--accent-soft); color: #ffffff; }}
	    main {{ display: grid; gap: 18px; padding: 18px 28px 34px; }}
	    section {{ scroll-margin-top: 120px; }}
	    .hero {{
	      display: grid;
	      grid-template-columns: minmax(620px, 1fr) minmax(340px, 420px);
	      gap: 18px;
	      align-items: start;
	    }}
	    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 12px; }}
	    .side-metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0; }}
	    .side-stat {{ padding: 13px 16px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); background: rgba(255, 255, 255, 0.012); }}
	    .side-stat:nth-child(2n) {{ border-right: 0; }}
	    .side-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; }}
	    .side-value {{ margin-top: 6px; font-size: 21px; line-height: 1.1; font-weight: 760; }}
	    .metric, .panel {{
	      background: linear-gradient(180deg, rgba(16, 27, 39, 0.98), rgba(13, 23, 34, 0.98));
	      border: 1px solid rgba(78, 161, 255, 0.14);
	      border-radius: 8px;
	      box-shadow: var(--shadow);
	    }}
	    .metric {{ padding: 14px 16px; min-width: 0; position: relative; overflow: hidden; }}
	    .metric::before {{
	      content: "";
	      position: absolute;
	      inset: 0 0 auto;
	      height: 2px;
	      background: linear-gradient(90deg, var(--accent), var(--teal));
	      opacity: 0.76;
	    }}
	    .metric-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
	    .metric-value {{ margin-top: 7px; font-size: 24px; font-weight: 720; line-height: 1.1; }}
	    .layout {{ display: grid; grid-template-columns: minmax(520px, 1.5fr) minmax(360px, .9fr); gap: 18px; align-items: start; }}
	    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }}
	    .panel {{ overflow: hidden; }}
	    .panel h2 {{ margin: 0; padding: 14px 16px; border-bottom: 1px solid var(--line); font-size: 16px; color: #f4f8fc; }}
	    .panel-title {{
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      gap: 12px;
	      padding: 14px 16px;
	      border-bottom: 1px solid var(--line);
	      background: rgba(255, 255, 255, 0.018);
	    }}
	    .panel-title h2 {{ padding: 0; border: 0; }}
	    .panel-title .subtle {{ text-align: right; }}
	    .panel-body {{ padding: 12px 16px; }}
	    #map {{ width: 100%; height: 690px; background: #07111b; }}
	    .leaflet-container {{ background: #07111b; font-family: "Aptos", "Segoe UI", sans-serif; }}
	    .leaflet-popup-content-wrapper, .leaflet-popup-tip {{
	      background: #101b27;
	      color: var(--ink);
	      border: 1px solid var(--line-strong);
	      box-shadow: 0 14px 36px rgba(0, 0, 0, 0.46);
	    }}
	    .leaflet-popup-content {{ color: var(--ink); line-height: 1.45; }}
	    .leaflet-control-zoom a {{
	      background: #101b27 !important;
	      color: var(--ink) !important;
	      border-color: var(--line) !important;
	    }}
	    .leaflet-control-attribution {{
	      background: rgba(8, 17, 27, 0.72) !important;
	      color: var(--muted) !important;
	    }}
	    .leaflet-control-attribution a {{ color: var(--accent) !important; }}
	    .filters {{
	      display: grid;
	      grid-template-columns: repeat(6, minmax(130px, 1fr));
	      gap: 10px;
      padding: 12px 16px;
	      border-bottom: 1px solid var(--line);
	      background: rgba(12, 22, 33, 0.78);
	    }}
    .filters label {{ display: grid; gap: 4px; color: var(--muted); font-size: 12px; font-weight: 650; }}
    .filters select, .filters input, .filters button {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 8px;
      font: inherit;
	      color: var(--ink);
	      background: #0b1622;
	      outline: none;
	    }}
	    .filters select:focus, .filters input:focus, .filters button:focus {{
	      border-color: var(--accent);
	      box-shadow: 0 0 0 3px rgba(78, 161, 255, 0.16);
	    }}
	    .filters button {{ align-self: end; cursor: pointer; font-weight: 700; transition: transform 120ms ease, border-color 120ms ease, background 120ms ease; }}
	    .filters button:hover {{ transform: translateY(-1px); border-color: var(--accent); }}
	    .filters button.active {{ background: linear-gradient(180deg, #ff6b61, #cf362e); color: #fff; border-color: #ff8178; }}
	    .stability-controls {{
	      display: grid;
	      grid-template-columns: repeat(2, minmax(180px, 1fr));
	      gap: 10px;
	      padding: 12px 16px;
	      border-bottom: 1px solid var(--line);
	      background: rgba(12, 22, 33, 0.78);
	    }}
	    .stability-controls label {{ display: grid; gap: 4px; color: var(--muted); font-size: 12px; font-weight: 650; }}
	    .stability-controls select {{
	      width: 100%;
	      border: 1px solid var(--line);
	      border-radius: 6px;
	      padding: 7px 8px;
	      font: inherit;
	      color: var(--ink);
	      background: #0b1622;
	      outline: none;
	    }}
	    .chart-grid {{ display: grid; grid-template-columns: 1.2fr .8fr; gap: 14px; padding: 14px 16px; }}
	    .chart-card {{ border: 1px solid var(--line); border-radius: 8px; background: rgba(255, 255, 255, 0.014); overflow: hidden; }}
	    .chart-title {{ padding: 10px 12px; color: #a9bad0; font-size: 12px; font-weight: 760; border-bottom: 1px solid var(--line); }}
	    .chart-card svg {{ width: 100%; height: 230px; display: block; background: #0b1622; }}
	    .chart-caption {{ min-height: 35px; padding: 9px 12px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }}
	    .view-track {{
	      border: 1px solid var(--accent);
	      border-radius: 6px;
	      padding: 5px 9px;
	      background: rgba(78, 161, 255, 0.12);
	      color: var(--accent);
	      cursor: pointer;
	      font: inherit;
	      font-weight: 760;
	    }}
	    .view-track:hover {{ background: var(--accent); color: #fff; }}
	    .view-track.active {{ background: var(--accent); color: #fff; }}
	    .map-footer {{
	      display: grid;
	      grid-template-columns: 1fr auto;
	      gap: 14px;
	      align-items: center;
	      padding: 12px 16px;
	      color: var(--muted);
	      font-size: 13px;
	      border-top: 1px solid var(--line);
	      background: rgba(8, 17, 27, 0.62);
	    }}
	    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }}
	    .legend-item {{ display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; }}
	    .swatch {{ width: 10px; height: 10px; border-radius: 999px; display: inline-block; box-shadow: 0 0 14px currentColor; }}
	    .side-stack {{ display: grid; gap: 18px; }}
	    .explain {{ padding: 13px 16px; color: var(--muted); font-size: 13px; line-height: 1.45; }}
	    .inspector {{
	      min-height: 122px;
	      padding: 14px 16px;
	      color: var(--muted);
	      line-height: 1.5;
	      border-top: 1px solid var(--line);
	    }}
	    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
	    th, td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }}
	    th {{ color: #a9bad0; font-weight: 760; background: rgba(255, 255, 255, 0.025); position: sticky; top: 0; z-index: 1; }}
	    td {{ color: #d8e2ec; }}
	    tr[data-segment-id] {{ cursor: pointer; }}
	    tbody tr:hover {{ background: rgba(78, 161, 255, 0.075); }}
	    tr[data-segment-id]:hover {{ background: rgba(78, 161, 255, 0.11); }}
	    .num {{ text-align: right; }}
	    .table-wrap {{ overflow-x: auto; }}
	    .table-wrap::-webkit-scrollbar {{ height: 10px; }}
	    .table-wrap::-webkit-scrollbar-track {{ background: #0b1622; }}
	    .table-wrap::-webkit-scrollbar-thumb {{ background: #29405a; border-radius: 999px; }}
	    .bar {{ height: 8px; background: #1b2a3b; border-radius: 999px; overflow: hidden; min-width: 120px; }}
	    .bar span {{ display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--teal)); }}
	    .badge {{
      display: inline-block;
      margin-left: 6px;
      padding: 2px 6px;
      border-radius: 999px;
	      background: rgba(79, 209, 139, 0.16);
	      color: var(--green);
      font-size: 11px;
      font-weight: 700;
    }}
	    .status-pill {{
	      display: inline-block;
	      min-width: 76px;
	      padding: 3px 7px;
	      border-radius: 999px;
	      text-align: center;
	      font-size: 11px;
	      font-weight: 760;
	      text-transform: uppercase;
	    }}
	    .status-pill.stable {{ background: rgba(79, 209, 139, 0.15); color: var(--green); }}
	    .status-pill.watch {{ background: rgba(246, 179, 75, 0.15); color: var(--amber); }}
	    .status-pill.moving {{ background: rgba(255, 95, 87, 0.15); color: var(--red); }}
	    .status-pill.thin {{ background: rgba(143, 161, 179, 0.14); color: var(--muted); }}
	    .note {{ padding: 12px 16px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--line); background: rgba(255, 255, 255, 0.015); }}
    .section-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
	    .section-title h2 {{ margin: 0; font-size: 20px; }}
	    .empty {{ color: var(--muted); padding: 14px 8px; }}
	    #segmentPreview {{ color: var(--muted); line-height: 1.5; }}
	    @media (max-width: 1000px) {{
	      header {{ padding: 16px; position: static; }}
	      main {{ padding: 16px; }}
	      .header-grid, .hero, .metrics, .layout, .two-col, .filters, .map-footer {{ grid-template-columns: 1fr; }}
	      .status-strip, .legend {{ justify-content: flex-start; }}
	      #map {{ height: 500px; }}
	    }}
	  </style>
	</head>
	<body>
	  <header>
	    <div class="header-grid">
	      <div>
	        <h1>RUAG OGN Dashboard</h1>
	        <div class="subtle">Live OGN/APRS collection, trajectory processing, and dropout analysis for {escape(region_label)}.</div>
	      </div>
	      <div class="status-strip" aria-label="Pipeline status">
	        <div class="status-chip"><span class="chip-label">Collection</span><span class="chip-value {'ok' if env.collect_on_startup else 'warn'}">{escape(collection_status)}</span></div>
	        <div class="status-chip"><span class="chip-label">Processor</span><span class="chip-value {'ok' if env.process_on_startup else 'warn'}">{escape(processor_status)}</span></div>
	        <div class="status-chip"><span class="chip-label">Latest OGN</span><span class="chip-value">{escape(str(summary["last_timestamp"] or "none"))}</span></div>
	        <div class="status-chip"><span class="chip-label">Refresh</span><span class="chip-value">{refresh_seconds}s</span></div>
	      </div>
	    </div>
	    <nav class="nav" aria-label="Dashboard sections">
	      <a href="#live-map">Live Map</a>
	      <a href="#dropout-grid">Dropout Grid</a>
	      <a href="#data-engineering">Data Engineering</a>
	      <a href="#distribution-stability">Distribution Stability</a>
	      <a href="#track-candidates">Track Candidates</a>
	      <a href="#beacon-types">Beacon Types</a>
	    </nav>
	  </header>
	  <main>
	    <section id="live-map" class="hero">
	      <div class="panel">
	        <div class="panel-title">
	          <h2>Observation Density and Dropout Map</h2>
	          <div class="subtle">Interactive layers: {escape(window_label)} plus all-time dropout grid. Generated {escape(generated_at)}.</div>
	        </div>
	        <div class="filters">
	          <label>Aircraft type
	            <select id="aircraftTypeFilter"><option value="all">All aircraft types</option></select>
          </label>
          <label>Class filter
            <select id="classFilter">
              <option value="all">All classes</option>
              <option value="unconventional">Likely unconventional</option>
            </select>
          </label>
          <label>Minimum observations
            <input id="minObs" type="number" value="10" min="1" step="1">
          </label>
          <button id="dropoutToggle" type="button" aria-pressed="false">Show dropout events</button>
	          <button id="hotspotToggle" type="button" aria-pressed="false">Show 24h dropout grid</button>
	          <button id="allTimeHotspotToggle" type="button" aria-pressed="false">Show all-time grid</button>
	        </div>
	        <div id="map" role="application" aria-label="OGN observation density map"></div>
	        <div class="map-footer">
	          <div>
	            Visible cells: <strong id="visibleCellCount">0</strong>.
	            Dropout events: <strong id="visibleDropoutCount">0</strong> / {len(snapshot["dropout_candidates"]):,}.
	            24h grid: <strong id="visibleHotspotCount">0</strong> / {len(snapshot["dropout_hotspots"]):,}.
	            All-time grid: <strong id="visibleAllTimeHotspotCount">0</strong> / {len(snapshot["all_time_dropout_hotspots"]):,}.
	          </div>
	          <div class="legend" aria-label="Map legend">
	            <span class="legend-item"><span class="swatch" style="background:#286fb4"></span> density</span>
	            <span class="legend-item"><span class="swatch" style="background:#168253"></span> likely unconventional</span>
	            <span class="legend-item"><span class="swatch" style="background:#dc2626"></span> dropout event</span>
	            <span class="legend-item"><span class="swatch" style="background:#f97316"></span> 24h dropout grid</span>
	            <span class="legend-item"><span class="swatch" style="background:#7f1d1d"></span> all-time dropout grid</span>
	          </div>
	        </div>
	      </div>
	      <div class="side-stack">
	        <div class="panel">
	          <h2>Pipeline Status</h2>
	          <div class="side-metrics">
	            <div class="side-stat"><div class="side-label">Raw messages</div><div class="side-value">{fmt_int(summary["raw_messages"])}</div></div>
	            <div class="side-stat"><div class="side-label">Parsed positions</div><div class="side-value">{fmt_int(summary["position_observations"])}</div></div>
	            <div class="side-stat"><div class="side-label">Cleaned observations</div><div class="side-value">{fmt_int(engineering["cleaned_observations"])}</div></div>
	            <div class="side-stat"><div class="side-label">Processor lag</div><div class="side-value">{fmt_int(processor_lag)}</div></div>
	            <div class="side-stat"><div class="side-label">Track segments</div><div class="side-value">{fmt_int(engineering["track_segments"])}</div></div>
	            <div class="side-stat"><div class="side-label">Good segments</div><div class="side-value">{fmt_int(engineering["good_segments"])}</div></div>
	          </div>
		          <div class="explain">The page is generated from cached SQL snapshots. Status totals are all-time; the recent map layers focus on {escape(window_label)}, and the all-time dropout grid is recomputed on each dashboard refresh.</div>
	        </div>
	        <div class="panel">
	          <h2>Coverage Signals</h2>
	          <div class="side-metrics">
	            <div class="side-stat"><div class="side-label">Region positions</div><div class="side-value">{fmt_int(summary["region_positions"])}</div></div>
	            <div class="side-stat"><div class="side-label">Unique IDs</div><div class="side-value">{fmt_int(summary["unique_aircraft"])}</div></div>
	            <div class="side-stat"><div class="side-label">Likely unconv. IDs</div><div class="side-value">{fmt_int(summary["unconventional_aircraft"])}</div></div>
	            <div class="side-stat"><div class="side-label">Dropout events</div><div class="side-value">{fmt_int(len(snapshot["dropout_candidates"]))}</div></div>
		            <div class="side-stat"><div class="side-label">24h grid cells</div><div class="side-value">{fmt_int(len(snapshot["dropout_hotspots"]))}</div></div>
		            <div class="side-stat"><div class="side-label">All-time grid cells</div><div class="side-value">{fmt_int(len(snapshot["all_time_dropout_hotspots"]))}</div></div>
		            <div class="side-stat"><div class="side-label">Map grid</div><div class="side-value">{fmt(snapshot["grid_degrees"], 2)}°</div></div>
	          </div>
	          <div id="mapInspector" class="inspector">Click a density cell, dropout event, or hotspot grid cell to inspect the signal behind it.</div>
	        </div>
	        <div class="panel">
	          <h2>Top Dropout Hotspots</h2>
	          <div class="table-wrap">
	            <table>
	              <thead><tr><th>Bias hint</th><th class="num">Rate</th><th class="num">Drops</th><th class="num">Trans.</th><th class="num">Aircraft</th><th class="num">Receivers</th><th>Type</th><th>Altitude</th><th>Top receiver</th><th class="num">Recv. share</th><th class="num">P95 gap</th><th class="num">Max gap</th></tr></thead>
	              <tbody>{render_hotspot_rows(snapshot["dropout_hotspots"])}</tbody>
	            </table>
	          </div>
	          <div class="note">This table uses the recent dashboard window. Use the all-time grid button to see persistent coverage-bias areas since collection started.</div>
	        </div>
	      </div>
	    </section>

	    <section id="dropout-grid" class="panel">
	      <h2>Dropout Grid Analysis</h2>
	      <div class="table-wrap">
	        <table>
	          <thead><tr><th>Bias hint</th><th class="num">Rate</th><th class="num">Drops</th><th class="num">Transitions</th><th class="num">Aircraft</th><th class="num">Receivers</th><th>Dominant type</th><th>Altitude band</th><th>Top receiver</th><th class="num">Receiver share</th><th class="num">P95 dropout s</th><th class="num">Max dropout s</th></tr></thead>
	          <tbody>{render_hotspot_rows(snapshot["dropout_hotspots"])}</tbody>
	        </table>
	      </div>
	      <div class="note">This table supports the recent-window map overlay: high dropout rate with many transitions suggests a spatial coverage issue; high receiver or aircraft concentration means the signal needs more careful interpretation.</div>
	    </section>

	    <section class="panel">
	      <h2>All-Time Dropout Grid Analysis</h2>
	      <div class="table-wrap">
	        <table>
	          <thead><tr><th>Bias hint</th><th class="num">Rate</th><th class="num">Drops</th><th class="num">Transitions</th><th class="num">Aircraft</th><th class="num">Receivers</th><th>Dominant type</th><th>Altitude band</th><th>Top receiver</th><th class="num">Receiver share</th><th class="num">P95 dropout s</th><th class="num">Max dropout s</th></tr></thead>
	          <tbody>{render_hotspot_rows(snapshot["all_time_dropout_hotspots"])}</tbody>
	        </table>
	      </div>
	      <div class="note">This all-time view is recomputed on every dashboard refresh and shows where signal loss has accumulated since data collection began.</div>
	    </section>

	    <section id="data-engineering" class="section-title">
	      <h2>Data Engineering</h2>
      <div class="subtle">Processed layer built from cleaned observations, track segments, and track points.</div>
    </section>
    <section class="metrics">
      {metric("Cleaned observations", fmt_int(engineering["cleaned_observations"]))}
      {metric("Track points", fmt_int(engineering["track_points"]))}
      {metric("Track segments", fmt_int(engineering["track_segments"]))}
      {metric("Good segments", fmt_int(engineering["good_segments"]))}
      {metric("Last processed ID", fmt_int(engineering["last_processed_position_id"]))}
    </section>

	    <section id="distribution-stability" class="section-title">
	      <h2>Distribution Stability</h2>
	      <div class="subtle">Previous {int(stability.get("window_days", 3))} days vs latest {int(stability.get("window_days", 3))} days, ending {escape(str(stability.get("latest_end", "not available")))}.</div>
	    </section>
	    <section class="metrics">
	      {metric("Stable variables", fmt_int(stability.get("stable_count", 0)))}
	      {metric("Watch variables", fmt_int(stability.get("watch_count", 0)))}
	      {metric("Moving variables", fmt_int(stability.get("moving_count", 0)))}
	      {metric("Thin samples", fmt_int(stability.get("thin_count", 0)))}
	      {metric("Window days", fmt_int(stability.get("window_days", 0)))}
	    </section>
	    <section class="panel">
	      <div class="panel-title">
	        <h2>Class-Specific Distribution Convergence</h2>
	        <div class="subtle">Choose an unconventional class and parameter. Histograms and memory update in the browser from the cached snapshot.</div>
	      </div>
	      <div class="stability-controls">
	        <label>Unconventional aircraft class
	          <select id="stabilityClassSelect"></select>
	        </label>
	        <label>Parameter
	          <select id="stabilityVariableSelect"></select>
	        </label>
	      </div>
	      <div class="chart-grid">
	        <div class="chart-card">
	          <div class="chart-title">Latest vs previous histogram</div>
	          <svg id="stabilityHistogram" viewBox="0 0 720 230" role="img" aria-label="Distribution histogram"></svg>
	          <div id="stabilityHistogramCaption" class="chart-caption"></div>
	        </div>
	        <div class="chart-card">
	          <div class="chart-title">Rolling three-day divergence memory</div>
	          <svg id="stabilityTrend" viewBox="0 0 440 230" role="img" aria-label="Distribution stability trend"></svg>
	          <div id="stabilityTrendCaption" class="chart-caption"></div>
	        </div>
	      </div>
	      <div class="table-wrap">
	        <table>
	          <thead><tr><th>Variable</th><th>Source</th><th>Unit</th><th class="num">Prev n</th><th class="num">Latest n</th><th class="num">JSD</th><th class="num">Wasserstein</th><th>Status</th></tr></thead>
	          <tbody id="stabilityRows">{render_stability_rows(stability.get("variables", []))}</tbody>
	        </table>
	      </div>
	      <div class="note">Each row is now class-specific: for example, glider speed is compared against glider speed only, balloon speed against balloon speed only, and so on. The trend chart shows how the same metric changed across rolling {int(stability.get("window_days", 3))}-day windows kept in the dashboard snapshot.</div>
	    </section>
	    <section class="two-col">
	      <div class="panel">
	        <h2>Aggregate Stability Context</h2>
	        <div class="table-wrap">
	          <table>
	            <thead><tr><th>Variable</th><th>Source</th><th>Unit</th><th class="num">Prev n</th><th class="num">Latest n</th><th class="num">JSD</th><th class="num">Wasserstein</th><th>Status</th></tr></thead>
	            <tbody>{render_stability_rows(stability.get("aggregate_variables", []))}</tbody>
	          </table>
	        </div>
	        <div class="note">Spatial density and state transitions are still shown as aggregate context. They are useful coverage and dynamics signals, but the modelling distributions above are split by unconventional aircraft class.</div>
	      </div>
	      <div class="panel">
	        <h2>Aircraft-Type Modelling Volume</h2>
	        <div class="table-wrap">
	          <table>
	            <thead><tr><th>Aircraft type</th><th class="num">Prev seg.</th><th class="num">Latest seg.</th><th class="num">Prev pts</th><th class="num">Latest pts</th><th>Status</th></tr></thead>
	            <tbody>{render_type_stability_rows(stability.get("type_readiness", []))}</tbody>
	          </table>
	        </div>
	        <div class="note">This table checks whether each unconventional aircraft class has enough recent good segments. It is a volume signal, not yet a full per-class convergence test.</div>
	      </div>
	    </section>

	    <section class="two-col">
	      <div class="panel">
	        <h2>Processed Segments by Aircraft Type</h2>
	        <div class="table-wrap">
	          <table>
	            <thead><tr><th>Aircraft type</th><th>Code</th><th class="num">Segments</th><th class="num">Points</th><th class="num">Unconv.</th><th></th><th class="num">% pts</th></tr></thead>
            <tbody>{render_engineering_type_rows(engineering["type_counts"])}</tbody>
          </table>
        </div>
	      </div>
	      <div class="panel">
	        <h2>Aircraft Type Counts</h2>
	        <div class="table-wrap">
	          <table>
	            <thead><tr><th>Aircraft type</th><th>Code</th><th class="num">Obs.</th><th class="num">IDs</th><th></th><th class="num">%</th></tr></thead>
	            <tbody>{render_aircraft_type_rows(snapshot["aircraft_types"])}</tbody>
	          </table>
	        </div>
	      </div>
	    </section>

	    <section class="panel">
	      <h2>Good Segments for Modelling</h2>
	      <div class="table-wrap">
	        <table>
	          <thead><tr><th>Aircraft type</th><th class="num">Segments</th><th class="num">Points</th><th class="num">Avg min</th><th></th><th class="num">% pts</th></tr></thead>
	          <tbody>{render_good_type_rows(engineering["good_type_counts"])}</tbody>
	        </table>
	      </div>
	    </section>

    <section id="track-candidates" class="panel">
      <h2>Best Unconventional Trajectories</h2>
      <div class="table-wrap">
	        <table>
	          <thead><tr><th></th><th class="num">Rank</th><th class="num">Score</th><th>Aircraft</th><th>Type</th><th>Beacon</th><th class="num">Pts</th><th class="num">Distance km</th><th class="num">Duration min</th><th class="num">Max gap s</th><th class="num">Avg speed</th><th>End time</th></tr></thead>
	          <tbody>{render_best_trajectory_rows(snapshot["best_trajectories"])}</tbody>
	        </table>
	      </div>
	      <div class="note">Use View to draw a trajectory directly on the map. Score favours long, dense, continuous unconventional segments.</div>
	    </section>

    <section class="layout">
      <div class="panel">
        <h2>Recent Processed Track Segments</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>ID</th><th>Aircraft</th><th>Type</th><th>Beacon</th><th class="num">Pts</th><th class="num">Duration min</th><th class="num">Max gap</th><th class="num">Distance km</th><th class="num">Avg speed</th><th>End time</th></tr></thead>
            <tbody>{render_recent_segment_rows(engineering["recent_segments"])}</tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h2>Segment Preview</h2>
        <div id="segmentPreview" class="panel-body">Click a processed segment or trajectory candidate.</div>
      </div>
    </section>

    <section class="panel">
      <h2>Recent Good Raw Tracks</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Aircraft</th><th>Type</th><th>Beacon</th><th class="num">Pts</th><th class="num">Duration min</th><th class="num">Avg gap s</th><th class="num">Max gap s</th><th class="num">Avg alt m</th><th class="num">Avg speed</th></tr></thead>
          <tbody>{render_quality_track_rows(snapshot["quality_tracks"])}</tbody>
        </table>
      </div>
    </section>

    <section id="beacon-types" class="two-col">
      <div class="panel">
        <h2>Beacon Type Counts</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Beacon</th><th class="num">Observations</th><th></th><th class="num">%</th></tr></thead>
            <tbody>{render_beacon_rows(snapshot["beacons"])}</tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h2>Top Aircraft</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Aircraft</th><th class="num">Observations</th></tr></thead>
            <tbody>{render_top_aircraft_rows(snapshot["top_aircraft"])}</tbody>
          </table>
        </div>
      </div>
    </section>
  </main>

  <script id="dashboard-data" type="application/json">{data_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById("dashboard-data").textContent);
    const densityCells = payload.densityCells;
    const dropoutCandidates = payload.dropoutCandidates;
    const dropoutHotspots = payload.dropoutHotspots;
    const allTimeDropoutHotspots = payload.allTimeDropoutHotspots;
    const distributionStability = payload.distributionStability || {{}};
    const gridSizeDeg = payload.gridDegrees;
    const switzerlandCenter = [46.8, 8.2];
    const map = L.map("map", {{ scrollWheelZoom: true }}).setView(switzerlandCenter, 7);
    L.tileLayer("https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png", {{
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
    }}).addTo(map);

	    const densityLayer = L.layerGroup().addTo(map);
	    const dropoutLayer = L.layerGroup();
	    const hotspotLayer = L.layerGroup();
	    const allTimeHotspotLayer = L.layerGroup();
	    const trajectoryLayer = L.layerGroup().addTo(map);
    const aircraftTypeFilter = document.getElementById("aircraftTypeFilter");
    const classFilter = document.getElementById("classFilter");
    const minObs = document.getElementById("minObs");
	    const dropoutToggle = document.getElementById("dropoutToggle");
	    const hotspotToggle = document.getElementById("hotspotToggle");
	    const allTimeHotspotToggle = document.getElementById("allTimeHotspotToggle");
	    const visibleCellCount = document.getElementById("visibleCellCount");
	    const visibleDropoutCount = document.getElementById("visibleDropoutCount");
	    const visibleHotspotCount = document.getElementById("visibleHotspotCount");
	    const visibleAllTimeHotspotCount = document.getElementById("visibleAllTimeHotspotCount");
	    const mapInspector = document.getElementById("mapInspector");
	    const stabilityClassSelect = document.getElementById("stabilityClassSelect");
	    const stabilityVariableSelect = document.getElementById("stabilityVariableSelect");
	    const stabilityRows = document.getElementById("stabilityRows");
	    const stabilityHistogram = document.getElementById("stabilityHistogram");
	    const stabilityTrend = document.getElementById("stabilityTrend");
	    const stabilityHistogramCaption = document.getElementById("stabilityHistogramCaption");
	    const stabilityTrendCaption = document.getElementById("stabilityTrendCaption");
	    const maxObservations = Math.max(...densityCells.map(cell => cell.observations), 1);
	    const maxDropoutGap = Math.max(...dropoutCandidates.map(candidate => candidate.gap_s), 1);
	    const maxHotspotRate = Math.max(...dropoutHotspots.map(cell => cell.dropout_rate), 0.01);
	    const maxAllTimeHotspotRate = Math.max(...allTimeDropoutHotspots.map(cell => cell.dropout_rate), 0.01);
    let dropoutsVisible = false;
    let hotspotsVisible = false;
    let allTimeHotspotsVisible = false;

    function fillFilter(select, values) {{
      for (const value of values) {{
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = value;
        select.appendChild(opt);
      }}
    }}

    function colorFor(cell) {{
      if (cell.aircraft_type === 1) return "#1f8a5b";
      if (cell.aircraft_type === 7) return "#0ea5a4";
      if (cell.aircraft_type === 6) return "#7c3aed";
      if (cell.aircraft_type === 13) return "#e11d48";
      if (cell.aircraft_type === 8) return "#2563eb";
      if (cell.aircraft_type === 9) return "#475569";
      return "#f97316";
    }}

    function filteredCells() {{
      const typeValue = aircraftTypeFilter.value;
      const classValue = classFilter.value;
      const minValue = Number(minObs.value || 1);
      return densityCells.filter(cell => {{
        if (cell.observations < minValue) return false;
        if (typeValue !== "all" && (cell.aircraft_type_name || "unknown") !== typeValue) return false;
        if (classValue === "unconventional" && !cell.unconventional) return false;
        return true;
      }});
    }}

	    function popupHtml(cell) {{
	      return `
	        <strong>${{cell.aircraft_type_name}}</strong><br>
        observations ${{cell.observations.toLocaleString()}}<br>
        unique IDs ${{cell.unique_aircraft.toLocaleString()}}<br>
        avg alt ${{cell.avg_altitude_m || ""}} m · avg speed ${{cell.avg_speed_kmh || ""}} km/h<br>
        cell ${{Number(cell.lat).toFixed(3)}}, ${{Number(cell.lon).toFixed(3)}}
	      `;
	    }}

	    function inspectDensity(cell) {{
	      mapInspector.innerHTML = `
	        <strong>Density cell</strong><br>
	        ${{cell.aircraft_type_name}}${{cell.unconventional ? " · likely unconventional" : ""}}<br>
	        ${{cell.observations.toLocaleString()}} observations from ${{cell.unique_aircraft.toLocaleString()}} IDs<br>
	        Avg altitude ${{cell.avg_altitude_m || ""}} m · avg speed ${{cell.avg_speed_kmh || ""}} km/h<br>
	        ${{Number(cell.lat).toFixed(3)}}, ${{Number(cell.lon).toFixed(3)}}
	      `;
	    }}

	    function dropoutPopupHtml(candidate) {{
	      return `
        <strong>Dropout candidate</strong><br>
        ${{candidate.aircraft_type_name}} · ${{candidate.beacon_type}}<br>
        gap ${{candidate.gap_s.toLocaleString()}} s · distance ${{candidate.distance_km.toLocaleString()}} km<br>
        implied speed ${{candidate.implied_speed_kmh.toLocaleString()}} km/h<br>
        ID ${{candidate.aircraft_id}}<br>
        ${{candidate.start_timestamp}} to ${{candidate.end_timestamp}}
	      `;
	    }}

	    function inspectDropout(candidate) {{
	      mapInspector.innerHTML = `
	        <strong>Dropout event</strong><br>
	        ${{candidate.aircraft_type_name}} · ${{candidate.beacon_type}} · ID ${{candidate.aircraft_id}}<br>
	        Gap ${{candidate.gap_s.toLocaleString()}} s over ${{candidate.distance_km.toLocaleString()}} km<br>
	        Implied speed ${{candidate.implied_speed_kmh.toLocaleString()}} km/h<br>
	        ${{candidate.start_timestamp}} to ${{candidate.end_timestamp}}
	      `;
	    }}

    function hotspotColor(cell, maxRate) {{
      const intensity = Math.min(cell.dropout_rate / maxRate, 1);
      if (intensity > 0.75) return "#991b1b";
      if (intensity > 0.5) return "#dc2626";
      if (intensity > 0.25) return "#f97316";
      return "#facc15";
    }}

	    function hotspotPopupHtml(cell, scopeLabel) {{
	      return `
	        <strong>${{scopeLabel}} hotspot cell</strong><br>
	        ${{cell.bias_hint}}<br>
	        dropout rate ${{(cell.dropout_rate * 100).toFixed(2)}}%<br>
	        dropouts ${{cell.dropout_count.toLocaleString()}} / transitions ${{cell.transition_count.toLocaleString()}}<br>
	        avg gap ${{cell.avg_gap_s.toLocaleString()}} s · p95 gap ${{cell.p95_gap_s.toLocaleString()}} s<br>
	        avg dropout ${{cell.avg_dropout_gap_s.toLocaleString()}} s · max dropout ${{cell.max_dropout_gap_s.toLocaleString()}} s<br>
	        aircraft ${{cell.unique_aircraft.toLocaleString()}} · receivers ${{cell.unique_receivers.toLocaleString()}}<br>
	        top receiver ${{cell.top_receiver || ""}} (${{(cell.top_receiver_share * 100).toFixed(0)}}%)<br>
	        ${{cell.dominant_aircraft_type}} · ${{cell.dominant_beacon_type}} · ${{cell.dominant_altitude_band}}
	      `;
	    }}

	    function inspectHotspot(cell, scopeLabel) {{
	      mapInspector.innerHTML = `
	        <strong>${{scopeLabel}} dropout hotspot</strong><br>
	        <strong>${{cell.bias_hint}}</strong><br>
	        ${{(cell.dropout_rate * 100).toFixed(2)}}% dropout rate · ${{cell.dropout_count.toLocaleString()}} / ${{cell.transition_count.toLocaleString()}} transitions<br>
	        ${{cell.unique_aircraft.toLocaleString()}} aircraft · ${{cell.unique_receivers.toLocaleString()}} receivers<br>
	        Top receiver ${{cell.top_receiver || ""}} (${{(cell.top_receiver_share * 100).toFixed(0)}}%)<br>
	        Aircraft concentration ${{(cell.top_aircraft_share * 100).toFixed(0)}}%<br>
	        ${{cell.dominant_aircraft_type}} · ${{cell.dominant_beacon_type}} · ${{cell.dominant_altitude_band}}<br>
	        Avg gap ${{cell.avg_gap_s.toLocaleString()}} s · P95 gap ${{cell.p95_gap_s.toLocaleString()}} s<br>
	        Avg dropout ${{cell.avg_dropout_gap_s.toLocaleString()}} s · max dropout ${{cell.max_dropout_gap_s.toLocaleString()}} s
	      `;
	    }}

    function drawDensity() {{
      densityLayer.clearLayers();
      const selected = filteredCells();
      visibleCellCount.textContent = selected.length.toLocaleString();
      const bounds = [];
      for (const cell of selected) {{
        const radius = 4 + 24 * Math.sqrt(cell.observations / maxObservations);
	        L.circleMarker([cell.lat, cell.lon], {{
	          radius,
	          stroke: true,
	          color: "#d8f3ff",
	          weight: 1,
	          fillColor: colorFor(cell),
	          fillOpacity: cell.unconventional ? 0.55 : 0.35
	        }}).bindPopup(popupHtml(cell)).on("click", () => inspectDensity(cell)).addTo(densityLayer);
	        bounds.push([cell.lat, cell.lon]);
	      }}
	      if (bounds.length) map.fitBounds(bounds, {{ padding: [24, 24], maxZoom: 9 }});
	    }}

    function drawDropouts() {{
      dropoutLayer.clearLayers();
      visibleDropoutCount.textContent = dropoutsVisible ? dropoutCandidates.length.toLocaleString() : "0";
      if (!dropoutsVisible) return;
      for (const candidate of dropoutCandidates) {{
        const radius = 4 + 12 * Math.sqrt(candidate.gap_s / maxDropoutGap);
        L.circleMarker([candidate.lat, candidate.lon], {{
          radius,
          stroke: true,
          color: "#7f1d1d",
          weight: 1.5,
          fillColor: "#dc2626",
          fillOpacity: 0.78
	        }}).bindPopup(dropoutPopupHtml(candidate)).on("click", () => inspectDropout(candidate)).addTo(dropoutLayer);
	      }}
	    }}

    function drawHotspots() {{
      hotspotLayer.clearLayers();
      visibleHotspotCount.textContent = hotspotsVisible ? dropoutHotspots.length.toLocaleString() : "0";
      if (!hotspotsVisible) return;
      const half = gridSizeDeg / 2;
      for (const cell of dropoutHotspots) {{
        L.rectangle(
          [[cell.lat - half, cell.lon - half], [cell.lat + half, cell.lon + half]],
          {{
            stroke: true,
            color: "#7f1d1d",
            weight: 1,
            fillColor: hotspotColor(cell, maxHotspotRate),
            fillOpacity: 0.42
          }}
	        ).bindPopup(hotspotPopupHtml(cell, "24h")).on("click", () => inspectHotspot(cell, "24h")).addTo(hotspotLayer);
	      }}
	    }}

	    function drawAllTimeHotspots() {{
	      allTimeHotspotLayer.clearLayers();
	      visibleAllTimeHotspotCount.textContent = allTimeHotspotsVisible ? allTimeDropoutHotspots.length.toLocaleString() : "0";
	      if (!allTimeHotspotsVisible) return;
	      const half = gridSizeDeg / 2;
	      for (const cell of allTimeDropoutHotspots) {{
	        L.rectangle(
	          [[cell.lat - half, cell.lon - half], [cell.lat + half, cell.lon + half]],
	          {{
	            stroke: true,
	            color: "#450a0a",
	            weight: 1.2,
	            fillColor: hotspotColor(cell, maxAllTimeHotspotRate),
	            fillOpacity: 0.28
	          }}
	        ).bindPopup(hotspotPopupHtml(cell, "All-time")).on("click", () => inspectHotspot(cell, "All-time")).addTo(allTimeHotspotLayer);
	      }}
	    }}

	    function drawTrajectory(segmentId, points) {{
	      trajectoryLayer.clearLayers();
	      document.querySelectorAll(".view-track.active").forEach(button => button.classList.remove("active"));
	      document.querySelectorAll(`.view-track[data-segment-id="${{segmentId}}"]`).forEach(button => button.classList.add("active"));
	      const latLngs = points
	        .filter(point => point.latitude !== null && point.longitude !== null)
	        .map(point => [point.latitude, point.longitude]);
	      if (!latLngs.length) return;

	      const line = L.polyline(latLngs, {{
	        color: "#4ea1ff",
	        weight: 4,
	        opacity: 0.92,
	      }}).addTo(trajectoryLayer);
	      L.circleMarker(latLngs[0], {{
	        radius: 6,
	        color: "#4ea1ff",
	        fillColor: "#0b1622",
	        fillOpacity: 1,
	        weight: 3,
	      }}).bindTooltip("trajectory start").addTo(trajectoryLayer);
	      L.circleMarker(latLngs[latLngs.length - 1], {{
	        radius: 7,
	        color: "#4ea1ff",
	        fillColor: "#4ea1ff",
	        fillOpacity: 1,
	        weight: 2,
	      }}).bindTooltip("trajectory end").addTo(trajectoryLayer);
	      map.fitBounds(line.getBounds(), {{ padding: [34, 34], maxZoom: 11 }});
	      document.getElementById("live-map").scrollIntoView({{ behavior: "smooth", block: "start" }});
	    }}

	    async function previewSegment(segmentId, showOnMap = false) {{
	      const target = document.getElementById("segmentPreview");
	      target.textContent = "Loading segment " + segmentId;
	      const points = await fetch(`/api/segments/${{segmentId}}/points`).then(response => response.json());
	      if (!points.length) {{
	        target.textContent = "No trajectory points found for segment " + segmentId;
	        return;
	      }}
	      if (showOnMap) drawTrajectory(segmentId, points);
	      const first = points[0];
	      const last = points[points.length - 1];
	      target.innerHTML = `
	        <strong>Segment #${{segmentId}}</strong><br>
	        Points: ${{points.length.toLocaleString()}}<br>
        Start: ${{first.timestamp || ""}}<br>
        End: ${{last.timestamp || ""}}<br>
        <div style="margin-top:10px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px;">
          ${{points.slice(0, 24).map(point => `${{point.timestamp || ""}} · ${{Number(point.latitude).toFixed(4)}}, ${{Number(point.longitude).toFixed(4)}} · ${{point.altitude_m || ""}}m`).join("<br>")}}
        </div>
      `;
	    }}

	    function fmtJs(value, digits = 0) {{
	      if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
	      return Number(value).toLocaleString(undefined, {{ maximumFractionDigits: digits, minimumFractionDigits: digits }});
	    }}

	    function stabilityPill(status) {{
	      const label = status || "unavailable";
	      const cls = label === "stable" ? "stable" : label === "watch" ? "watch" : label === "moving" ? "moving" : "thin";
	      return `<span class="status-pill ${{cls}}">${{label}}</span>`;
	    }}

	    function selectedStabilityClass() {{
	      const classes = distributionStability.classes || [];
	      return classes.find(item => String(item.code) === stabilityClassSelect.value) || classes[0] || null;
	    }}

	    function selectedStabilityVariable(stabilityClass) {{
	      const variables = stabilityClass ? stabilityClass.variables || [] : [];
	      return variables.find(item => item.key === stabilityVariableSelect.value) || variables[0] || null;
	    }}

	    function fillStabilitySelectors() {{
	      const classes = distributionStability.classes || [];
	      stabilityClassSelect.innerHTML = "";
	      for (const item of classes) {{
	        const option = document.createElement("option");
	        option.value = item.code;
	        option.textContent = `${{item.label}} (${{Number(item.observations || 0).toLocaleString()}} pts)`;
	        stabilityClassSelect.appendChild(option);
	      }}
	      fillStabilityVariables();
	    }}

	    function fillStabilityVariables() {{
	      const stabilityClass = selectedStabilityClass();
	      const currentValue = stabilityVariableSelect.value;
	      stabilityVariableSelect.innerHTML = "";
	      for (const item of (stabilityClass ? stabilityClass.variables || [] : [])) {{
	        const option = document.createElement("option");
	        option.value = item.key;
	        option.textContent = item.name;
	        stabilityVariableSelect.appendChild(option);
	      }}
	      if ([...stabilityVariableSelect.options].some(option => option.value === currentValue)) {{
	        stabilityVariableSelect.value = currentValue;
	      }}
	    }}

	    function renderStabilityRows() {{
	      const stabilityClass = selectedStabilityClass();
	      const variables = stabilityClass ? stabilityClass.variables || [] : [];
	      if (!variables.length) {{
	        stabilityRows.innerHTML = '<tr><td colspan="8" class="empty">No class-specific stability data yet.</td></tr>';
	        return;
	      }}
	      stabilityRows.innerHTML = variables.map(item => `
	        <tr>
	          <td>${{item.name}}</td>
	          <td>${{item.source}}</td>
	          <td>${{item.unit}}</td>
	          <td class="num">${{fmtJs(item.previous_n, 0)}}</td>
	          <td class="num">${{fmtJs(item.latest_n, 0)}}</td>
	          <td class="num">${{fmtJs(item.jensen_shannon, 5)}}</td>
	          <td class="num">${{item.wasserstein === null ? "" : fmtJs(item.wasserstein, 3)}}</td>
	          <td>${{stabilityPill(item.status)}}</td>
	        </tr>
	      `).join("");
	    }}

	    function drawStabilityHistogram(variable) {{
	      stabilityHistogram.innerHTML = "";
	      if (!variable) {{
	        stabilityHistogramCaption.textContent = "No histogram available.";
	        return;
	      }}
	      const latest = new Map((variable.latest_bins || []).map(item => [item.bin, item]));
	      const previous = new Map((variable.previous_bins || []).map(item => [item.bin, item]));
	      const keys = [...new Set([...latest.keys(), ...previous.keys()])].sort((a, b) => a - b);
	      if (!keys.length) {{
	        stabilityHistogramCaption.textContent = "No samples in one or both comparison windows.";
	        return;
	      }}
	      const width = 720;
	      const height = 230;
	      const pad = 34;
	      const chartWidth = width - pad * 2;
	      const chartHeight = height - pad * 2;
	      const maxCount = Math.max(...keys.map(key => Math.max(latest.get(key)?.count || 0, previous.get(key)?.count || 0)), 1);
	      const barStep = chartWidth / keys.length;
	      const barWidth = Math.max(2, barStep * 0.36);
	      const bars = [];
	      for (let index = 0; index < keys.length; index += 1) {{
	        const key = keys[index];
	        const x = pad + index * barStep + barStep * 0.12;
	        const previousHeight = chartHeight * ((previous.get(key)?.count || 0) / maxCount);
	        const latestHeight = chartHeight * ((latest.get(key)?.count || 0) / maxCount);
	        bars.push(`<rect x="${{x}}" y="${{height - pad - previousHeight}}" width="${{barWidth}}" height="${{previousHeight}}" fill="#8fa1b3" opacity="0.62"></rect>`);
	        bars.push(`<rect x="${{x + barWidth}}" y="${{height - pad - latestHeight}}" width="${{barWidth}}" height="${{latestHeight}}" fill="#4ea1ff" opacity="0.88"></rect>`);
	      }}
	      stabilityHistogram.innerHTML = `
	        <line x1="${{pad}}" y1="${{height - pad}}" x2="${{width - pad}}" y2="${{height - pad}}" stroke="#38506a"></line>
	        <line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{height - pad}}" stroke="#38506a"></line>
	        ${{bars.join("")}}
	        <text x="${{pad}}" y="20" fill="#8fa1b3" font-size="12">previous</text>
	        <rect x="${{pad + 58}}" y="11" width="12" height="8" fill="#8fa1b3" opacity="0.62"></rect>
	        <text x="${{pad + 86}}" y="20" fill="#8fa1b3" font-size="12">latest</text>
	        <rect x="${{pad + 127}}" y="11" width="12" height="8" fill="#4ea1ff" opacity="0.88"></rect>
	      `;
	      stabilityHistogramCaption.textContent = `${{variable.name}} · previous n=${{fmtJs(variable.previous_n)}} · latest n=${{fmtJs(variable.latest_n)}} · status=${{variable.status}}`;
	    }}

	    function drawStabilityTrend(variable) {{
	      stabilityTrend.innerHTML = "";
	      const history = variable ? variable.history || [] : [];
	      if (!history.length) {{
	        stabilityTrendCaption.textContent = "No rolling history available.";
	        return;
	      }}
	      const width = 440;
	      const height = 230;
	      const pad = 34;
	      const chartWidth = width - pad * 2;
	      const chartHeight = height - pad * 2;
	      const values = history.map(item => item.jensen_shannon === null ? 0 : Number(item.jensen_shannon));
	      const maxValue = Math.max(...values, 0.01);
	      const points = values.map((value, index) => {{
	        const x = pad + (history.length === 1 ? chartWidth / 2 : index * chartWidth / (history.length - 1));
	        const y = height - pad - chartHeight * (value / maxValue);
	        return [x, y, value];
	      }});
	      const line = points.map(point => `${{point[0]}},${{point[1]}}`).join(" ");
	      const dots = points.map((point, index) => `<circle cx="${{point[0]}}" cy="${{point[1]}}" r="4" fill="#4ea1ff"><title>${{history[index].start}} to ${{history[index].end}}: ${{fmtJs(point[2], 5)}}</title></circle>`);
	      stabilityTrend.innerHTML = `
	        <line x1="${{pad}}" y1="${{height - pad}}" x2="${{width - pad}}" y2="${{height - pad}}" stroke="#38506a"></line>
	        <line x1="${{pad}}" y1="${{pad}}" x2="${{pad}}" y2="${{height - pad}}" stroke="#38506a"></line>
	        <polyline points="${{line}}" fill="none" stroke="#4ea1ff" stroke-width="2.5"></polyline>
	        ${{dots.join("")}}
	        <text x="${{pad}}" y="20" fill="#8fa1b3" font-size="12">JSD trend</text>
	        <text x="${{width - pad - 72}}" y="20" fill="#8fa1b3" font-size="12">max ${{fmtJs(maxValue, 5)}}</text>
	      `;
	      stabilityTrendCaption.textContent = `Lower is better. Each dot compares one ${{distributionStability.window_days}}-day window with the previous one.`;
	    }}

	    function refreshStabilityPanel() {{
	      fillStabilityVariables();
	      renderStabilityRows();
	      const stabilityClass = selectedStabilityClass();
	      const variable = selectedStabilityVariable(stabilityClass);
	      drawStabilityHistogram(variable);
	      drawStabilityTrend(variable);
	    }}

    fillFilter(aircraftTypeFilter, [...new Set(densityCells.map(cell => cell.aircraft_type_name || "unknown"))].sort());
	    fillStabilitySelectors();
    aircraftTypeFilter.addEventListener("change", drawDensity);
    classFilter.addEventListener("change", drawDensity);
    minObs.addEventListener("input", drawDensity);
	    stabilityClassSelect.addEventListener("change", refreshStabilityPanel);
	    stabilityVariableSelect.addEventListener("change", () => {{
	      const stabilityClass = selectedStabilityClass();
	      const variable = selectedStabilityVariable(stabilityClass);
	      drawStabilityHistogram(variable);
	      drawStabilityTrend(variable);
	    }});
    dropoutToggle.addEventListener("click", () => {{
      dropoutsVisible = !dropoutsVisible;
      dropoutToggle.classList.toggle("active", dropoutsVisible);
      dropoutToggle.textContent = dropoutsVisible ? "Hide dropout events" : "Show dropout events";
      if (dropoutsVisible) dropoutLayer.addTo(map); else map.removeLayer(dropoutLayer);
      drawDropouts();
    }});
    hotspotToggle.addEventListener("click", () => {{
      hotspotsVisible = !hotspotsVisible;
      hotspotToggle.classList.toggle("active", hotspotsVisible);
      hotspotToggle.textContent = hotspotsVisible ? "Hide 24h dropout grid" : "Show 24h dropout grid";
      if (hotspotsVisible) hotspotLayer.addTo(map); else map.removeLayer(hotspotLayer);
      drawHotspots();
    }});
	    allTimeHotspotToggle.addEventListener("click", () => {{
	      allTimeHotspotsVisible = !allTimeHotspotsVisible;
	      allTimeHotspotToggle.classList.toggle("active", allTimeHotspotsVisible);
	      allTimeHotspotToggle.textContent = allTimeHotspotsVisible ? "Hide all-time grid" : "Show all-time grid";
	      if (allTimeHotspotsVisible) allTimeHotspotLayer.addTo(map); else map.removeLayer(allTimeHotspotLayer);
	      drawAllTimeHotspots();
	    }});
	    document.querySelectorAll("tr[data-segment-id]").forEach(row => {{
	      row.addEventListener("click", () => previewSegment(row.dataset.segmentId));
	    }});
	    document.querySelectorAll(".view-track").forEach(button => {{
	      button.addEventListener("click", event => {{
	        event.stopPropagation();
	        previewSegment(button.dataset.segmentId, true);
	      }});
	    }});
	    refreshStabilityPanel();
	    drawDensity();
  </script>
</body>
</html>
"""


def loading_html() -> str:
    """Return a light page while the first dashboard snapshot is building."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="2">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RUAG OGN Dashboard</title>
  <style>
    body {{ margin: 0; font-family: "Aptos", "Segoe UI", sans-serif; background: #08111b; color: #e7edf3; }}
    main {{ min-height: 100vh; display: grid; place-items: center; }}
    div {{ border: 1px solid #263648; border-radius: 8px; background: #101b27; padding: 22px 26px; box-shadow: 0 18px 50px rgba(0, 0, 0, .34); }}
    p {{ margin: 6px 0 0; color: #8fa1b3; }}
  </style>
</head>
<body><main><div><strong>Building dashboard snapshot</strong><p>Refreshing automatically every 2 seconds.</p></div></main></body>
</html>"""


class DashboardCache:
    """Background-refreshed HTML dashboard cache."""

    def __init__(self) -> None:
        """Initialize cache state."""
        self.html: str | None = None
        self.error: str | None = None
        self.last_refresh_s: float = 0.0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start background dashboard rendering."""
        if self.thread and self.thread.is_alive():
            return

        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        """Refresh the dashboard until stopped."""
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(env.dashboard_refresh_seconds)

    def refresh(self) -> None:
        """Render one dashboard snapshot into the cache."""
        session = SessionLocal()
        try:
            html = render_dashboard_html(dashboard_snapshot(session))
        except Exception as exc:
            session.rollback()
            with self.lock:
                self.error = str(exc)
            print(f"{utc_now_iso()} dashboard_error={exc}", flush=True)
        else:
            with self.lock:
                self.html = html
                self.error = None
                self.last_refresh_s = monotonic()
        finally:
            session.close()

    def get_html(self) -> str:
        """Return cached dashboard HTML or a light loading page."""
        with self.lock:
            html = self.html
            error = self.error

        if html:
            return html

        if error:
            return loading_html().replace(
                "Building dashboard snapshot",
                f"Dashboard snapshot error: {escape(error)}",
            )

        return loading_html()

    def stop(self) -> None:
        """Stop background dashboard rendering."""
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)


dashboard_cache = DashboardCache()


@router.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    """Return the cached browser dashboard page."""
    return HTMLResponse(dashboard_cache.get_html())
