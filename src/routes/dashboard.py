"""HTML dashboard route."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Return the browser dashboard shell."""
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RUAG OGN Dashboard</title>
  <style>
    :root { color-scheme: light; font-family: Inter, Arial, sans-serif; }
    body { margin: 0; background: #f6f7f9; color: #17202a; }
    header { padding: 20px 28px; background: #ffffff; border-bottom: 1px solid #d9dee7; }
    main { padding: 24px 28px; display: grid; gap: 18px; }
    h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
    h2 { margin: 0 0 12px; font-size: 16px; letter-spacing: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
    .card { background: #ffffff; border: 1px solid #d9dee7; border-radius: 8px; padding: 16px; }
    .metric { font-size: 30px; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 6px; border-bottom: 1px solid #e5e9f0; text-align: left; }
    th { color: #526171; font-weight: 600; }
    #map { height: 480px; position: relative; overflow: hidden; background: #dce7ed; }
    .dot { position: absolute; width: 5px; height: 5px; border-radius: 50%; background: #145c9e; opacity: .7; }
  </style>
</head>
<body>
  <header><h1>RUAG OGN Dashboard</h1></header>
  <main>
    <section class="grid">
      <div class="card"><h2>Raw Messages</h2><div id="raw" class="metric">...</div></div>
      <div class="card"><h2>Position Observations</h2><div id="positions" class="metric">...</div></div>
    </section>
    <section class="card">
      <h2>Recent Observation Map</h2>
      <div id="map"></div>
    </section>
    <section class="grid">
      <div class="card"><h2>Top Aircraft</h2><table id="aircraft"></table></div>
      <div class="card"><h2>Beacon Types</h2><table id="beacons"></table></div>
    </section>
  </main>
  <script>
    function cell(value) { return `<td>${value ?? ""}</td>`; }
    function rows(items, keys) {
      return items.map(item => `<tr>${keys.map(key => cell(item[key])).join("")}</tr>`).join("");
    }
    async function refresh() {
      const [counts, observations, aircraft, beacons] = await Promise.all([
        fetch("/api/counts").then(r => r.json()),
        fetch("/api/observations?limit=400").then(r => r.json()),
        fetch("/api/aircraft").then(r => r.json()),
        fetch("/api/beacons").then(r => r.json())
      ]);
      document.getElementById("raw").textContent = counts.raw_messages.toLocaleString();
      document.getElementById("positions").textContent = counts.position_observations.toLocaleString();
      document.getElementById("aircraft").innerHTML =
        "<tr><th>Aircraft</th><th>Observations</th></tr>" + rows(aircraft, ["aircraft_id", "observations"]);
      document.getElementById("beacons").innerHTML =
        "<tr><th>Beacon</th><th>Observations</th></tr>" + rows(beacons, ["beacon_type", "observations"]);
      const map = document.getElementById("map");
      map.innerHTML = "";
      observations.forEach(item => {
        if (item.latitude === null || item.longitude === null) return;
        const dot = document.createElement("div");
        dot.className = "dot";
        dot.title = `${item.aircraft_id || "unknown"} ${item.timestamp || ""}`;
        dot.style.left = `${((item.longitude + 180) / 360) * 100}%`;
        dot.style.top = `${((90 - item.latitude) / 180) * 100}%`;
        map.appendChild(dot);
      });
    }
    refresh();
    setInterval(refresh, 30000);
  </script>
</body>
</html>
"""
