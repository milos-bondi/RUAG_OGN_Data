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


def render_json(snapshot: dict[str, object]) -> str:
    """Render JSON data embedded for client-side map filtering."""
    payload = {
        "densityCells": snapshot["density_cells"],
        "dropoutCandidates": snapshot["dropout_candidates"],
        "dropoutHotspots": snapshot["dropout_hotspots"],
        "gridDegrees": snapshot["grid_degrees"],
    }

    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def render_dashboard_html(snapshot: dict[str, object]) -> str:
    """Render the complete dashboard HTML."""
    summary = snapshot["summary"]
    engineering = snapshot["engineering"]
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
	      color-scheme: light;
	      --ink: #14202b;
	      --muted: #617080;
	      --line: #dce2e8;
	      --bg: #f4f6f8;
	      --panel: #ffffff;
	      --accent: #286fb4;
	      --green: #168253;
	      --red: #bd372d;
	      --amber: #b76b12;
	      --teal: #168a8b;
	      --navy: #1f3448;
	    }}
	    * {{ box-sizing: border-box; }}
	    body {{
	      margin: 0;
	      font-family: "Aptos", "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
	    }}
	    header {{
	      padding: 18px 28px 14px;
	      background: var(--panel);
	      border-bottom: 1px solid var(--line);
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
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      padding: 8px 10px;
	      background: #fbfcfd;
	    }}
	    .chip-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; }}
	    .chip-value {{ font-size: 13px; font-weight: 760; }}
	    .chip-value.ok {{ color: var(--green); }}
	    .chip-value.warn {{ color: var(--amber); }}
	    .nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
	    .nav a {{
	      border: 1px solid var(--line);
	      border-radius: 6px;
	      padding: 7px 10px;
      color: var(--ink);
      background: #fbfcfd;
      text-decoration: none;
	      font-size: 13px;
	      font-weight: 650;
	    }}
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
	    .side-stat {{ padding: 13px 16px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
	    .side-stat:nth-child(2n) {{ border-right: 0; }}
	    .side-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; }}
	    .side-value {{ margin-top: 6px; font-size: 21px; line-height: 1.1; font-weight: 760; }}
	    .metric, .panel {{
	      background: var(--panel);
	      border: 1px solid var(--line);
	      border-radius: 8px;
	    }}
	    .metric {{ padding: 14px 16px; min-width: 0; }}
	    .metric-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
	    .metric-value {{ margin-top: 7px; font-size: 24px; font-weight: 720; line-height: 1.1; }}
	    .layout {{ display: grid; grid-template-columns: minmax(520px, 1.5fr) minmax(360px, .9fr); gap: 18px; align-items: start; }}
	    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }}
	    .panel {{ overflow: hidden; }}
	    .panel h2 {{ margin: 0; padding: 14px 16px; border-bottom: 1px solid var(--line); font-size: 16px; }}
	    .panel-title {{
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      gap: 12px;
	      padding: 14px 16px;
	      border-bottom: 1px solid var(--line);
	    }}
	    .panel-title h2 {{ padding: 0; border: 0; }}
	    .panel-title .subtle {{ text-align: right; }}
	    .panel-body {{ padding: 12px 16px; }}
	    #map {{ width: 100%; height: 690px; background: #eef2f4; }}
	    .filters {{
	      display: grid;
	      grid-template-columns: repeat(5, minmax(140px, 1fr));
	      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }}
    .filters label {{ display: grid; gap: 4px; color: var(--muted); font-size: 12px; font-weight: 650; }}
    .filters select, .filters input, .filters button {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 8px;
      font: inherit;
      color: var(--ink);
      background: var(--panel);
    }}
	    .filters button {{ align-self: end; cursor: pointer; font-weight: 700; }}
	    .filters button.active {{ background: var(--red); color: #fff; border-color: var(--red); }}
	    .view-track {{
	      border: 1px solid var(--accent);
	      border-radius: 6px;
	      padding: 5px 9px;
	      background: #f3f8fd;
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
	    }}
	    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }}
	    .legend-item {{ display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; }}
	    .swatch {{ width: 10px; height: 10px; border-radius: 999px; display: inline-block; }}
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
    th {{ color: var(--muted); font-weight: 700; }}
    tr[data-segment-id] {{ cursor: pointer; }}
    tr[data-segment-id]:hover {{ background: #f2f6f9; }}
    .num {{ text-align: right; }}
    .table-wrap {{ overflow-x: auto; }}
    .bar {{ height: 8px; background: #edf0f3; border-radius: 999px; overflow: hidden; min-width: 120px; }}
    .bar span {{ display: block; height: 100%; background: var(--accent); }}
    .badge {{
      display: inline-block;
      margin-left: 6px;
      padding: 2px 6px;
      border-radius: 999px;
      background: #e5f4ec;
      color: var(--green);
      font-size: 11px;
      font-weight: 700;
    }}
    .note {{ padding: 12px 16px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--line); }}
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
	      <a href="#track-candidates">Track Candidates</a>
	      <a href="#beacon-types">Beacon Types</a>
	    </nav>
	  </header>
	  <main>
	    <section id="live-map" class="hero">
	      <div class="panel">
	        <div class="panel-title">
	          <h2>Observation Density and Dropout Map</h2>
	          <div class="subtle">Interactive layers: {escape(window_label)}. Generated {escape(generated_at)}.</div>
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
	          <button id="hotspotToggle" type="button" aria-pressed="false">Show dropout grid</button>
	        </div>
	        <div id="map" role="application" aria-label="OGN observation density map"></div>
	        <div class="map-footer">
	          <div>
	            Visible cells: <strong id="visibleCellCount">0</strong>.
	            Dropout events: <strong id="visibleDropoutCount">0</strong> / {len(snapshot["dropout_candidates"]):,}.
	            Dropout grid: <strong id="visibleHotspotCount">0</strong> / {len(snapshot["dropout_hotspots"]):,}.
	          </div>
	          <div class="legend" aria-label="Map legend">
	            <span class="legend-item"><span class="swatch" style="background:#286fb4"></span> density</span>
	            <span class="legend-item"><span class="swatch" style="background:#168253"></span> likely unconventional</span>
	            <span class="legend-item"><span class="swatch" style="background:#dc2626"></span> dropout event</span>
	            <span class="legend-item"><span class="swatch" style="background:#f97316"></span> dropout grid</span>
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
	          <div class="explain">The page is generated from cached SQL snapshots. The status totals are all-time; map layers and hotspot tables focus on {escape(window_label)} for fast demo interaction.</div>
	        </div>
	        <div class="panel">
	          <h2>Coverage Signals</h2>
	          <div class="side-metrics">
	            <div class="side-stat"><div class="side-label">Region positions</div><div class="side-value">{fmt_int(summary["region_positions"])}</div></div>
	            <div class="side-stat"><div class="side-label">Unique IDs</div><div class="side-value">{fmt_int(summary["unique_aircraft"])}</div></div>
	            <div class="side-stat"><div class="side-label">Likely unconv. IDs</div><div class="side-value">{fmt_int(summary["unconventional_aircraft"])}</div></div>
	            <div class="side-stat"><div class="side-label">Dropout events</div><div class="side-value">{fmt_int(len(snapshot["dropout_candidates"]))}</div></div>
	            <div class="side-stat"><div class="side-label">Dropout grid cells</div><div class="side-value">{fmt_int(len(snapshot["dropout_hotspots"]))}</div></div>
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
	          <div class="note">Hotspots are grid cells where plausible observation gaps are frequent. The bias hint is a quick interpretation, not a final diagnosis.</div>
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
	      <div class="note">This table supports the map overlay: high dropout rate with many transitions suggests a spatial coverage issue; high receiver or aircraft concentration means the signal needs more careful interpretation.</div>
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
    const gridSizeDeg = payload.gridDegrees;
    const switzerlandCenter = [46.8, 8.2];
    const map = L.map("map", {{ scrollWheelZoom: true }}).setView(switzerlandCenter, 7);
    L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors"
    }}).addTo(map);

	    const densityLayer = L.layerGroup().addTo(map);
	    const dropoutLayer = L.layerGroup();
	    const hotspotLayer = L.layerGroup();
	    const trajectoryLayer = L.layerGroup().addTo(map);
    const aircraftTypeFilter = document.getElementById("aircraftTypeFilter");
    const classFilter = document.getElementById("classFilter");
    const minObs = document.getElementById("minObs");
    const dropoutToggle = document.getElementById("dropoutToggle");
    const hotspotToggle = document.getElementById("hotspotToggle");
	    const visibleCellCount = document.getElementById("visibleCellCount");
	    const visibleDropoutCount = document.getElementById("visibleDropoutCount");
	    const visibleHotspotCount = document.getElementById("visibleHotspotCount");
	    const mapInspector = document.getElementById("mapInspector");
	    const maxObservations = Math.max(...densityCells.map(cell => cell.observations), 1);
	    const maxDropoutGap = Math.max(...dropoutCandidates.map(candidate => candidate.gap_s), 1);
	    const maxHotspotRate = Math.max(...dropoutHotspots.map(cell => cell.dropout_rate), 0.01);
    let dropoutsVisible = false;
    let hotspotsVisible = false;

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

    function hotspotColor(cell) {{
      const intensity = Math.min(cell.dropout_rate / maxHotspotRate, 1);
      if (intensity > 0.75) return "#991b1b";
      if (intensity > 0.5) return "#dc2626";
      if (intensity > 0.25) return "#f97316";
      return "#facc15";
    }}

	    function hotspotPopupHtml(cell) {{
	      return `
	        <strong>Dropout hotspot cell</strong><br>
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

	    function inspectHotspot(cell) {{
	      mapInspector.innerHTML = `
	        <strong>Dropout hotspot</strong><br>
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
	          color: "#ffffff",
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
            fillColor: hotspotColor(cell),
            fillOpacity: 0.42
          }}
	        ).bindPopup(hotspotPopupHtml(cell)).on("click", () => inspectHotspot(cell)).addTo(hotspotLayer);
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
	        color: "#0f5ea8",
	        weight: 4,
	        opacity: 0.92,
	      }}).addTo(trajectoryLayer);
	      L.circleMarker(latLngs[0], {{
	        radius: 6,
	        color: "#0f5ea8",
	        fillColor: "#ffffff",
	        fillOpacity: 1,
	        weight: 3,
	      }}).bindTooltip("trajectory start").addTo(trajectoryLayer);
	      L.circleMarker(latLngs[latLngs.length - 1], {{
	        radius: 7,
	        color: "#0f5ea8",
	        fillColor: "#0f5ea8",
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

    fillFilter(aircraftTypeFilter, [...new Set(densityCells.map(cell => cell.aircraft_type_name || "unknown"))].sort());
    aircraftTypeFilter.addEventListener("change", drawDensity);
    classFilter.addEventListener("change", drawDensity);
    minObs.addEventListener("input", drawDensity);
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
      hotspotToggle.textContent = hotspotsVisible ? "Hide dropout grid" : "Show dropout grid";
      if (hotspotsVisible) hotspotLayer.addTo(map); else map.removeLayer(hotspotLayer);
      drawHotspots();
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
    body {{ margin: 0; font-family: "Aptos", "Segoe UI", sans-serif; background: #f6f7f9; color: #17202a; }}
    main {{ min-height: 100vh; display: grid; place-items: center; }}
    div {{ border: 1px solid #d9dee5; border-radius: 8px; background: #fff; padding: 22px 26px; }}
    p {{ margin: 6px 0 0; color: #5c6875; }}
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
