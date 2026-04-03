"""
dashboard.py — Mini dashboard web para monitorear el sync.
Accesible desde la red local en http://IP:8080
"""

from flask import Flask, render_template_string, jsonify
import sqlite3
import json
from datetime import datetime

app = Flask(__name__)

def get_db():
    cfg = json.load(open("config.json"))
    return sqlite3.connect(cfg.get("db_path", "sync.db"))

TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sync Monitor — Woo ↔ ML</title>
<meta http-equiv="refresh" content="30">
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap');

  :root {
    --bg: #0d0f12;
    --surface: #151820;
    --border: #1e2430;
    --text: #c9d1e0;
    --muted: #5a6478;
    --green: #00e5a0;
    --red: #ff4d6d;
    --yellow: #ffc94d;
    --blue: #4d9eff;
    --mono: 'IBM Plex Mono', monospace;
    --sans: 'IBM Plex Sans', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--sans); min-height: 100vh; }

  header {
    border-bottom: 1px solid var(--border);
    padding: 1.25rem 2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .logo {
    font-family: var(--mono);
    font-size: .75rem;
    font-weight: 600;
    letter-spacing: .15em;
    text-transform: uppercase;
    color: var(--green);
  }
  .pulse {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0,229,160,.4); }
    50% { opacity: .7; box-shadow: 0 0 0 6px rgba(0,229,160,0); }
  }
  .refresh-hint { margin-left: auto; font-size: .7rem; color: var(--muted); font-family: var(--mono); }

  main { padding: 2rem; max-width: 1200px; margin: 0 auto; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.25rem 1.5rem;
  }
  .card-label { font-size: .65rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); font-family: var(--mono); margin-bottom: .5rem; }
  .card-value { font-size: 2rem; font-weight: 600; font-family: var(--mono); }
  .card-value.green { color: var(--green); }
  .card-value.red   { color: var(--red); }
  .card-value.yellow{ color: var(--yellow); }
  .card-value.blue  { color: var(--blue); }

  h2 { font-size: .7rem; letter-spacing: .15em; text-transform: uppercase; color: var(--muted); font-family: var(--mono); margin-bottom: 1rem; }

  table { width: 100%; border-collapse: collapse; font-size: .82rem; }
  th { text-align: left; font-family: var(--mono); font-size: .6rem; letter-spacing: .1em; color: var(--muted); text-transform: uppercase; padding: .5rem 1rem; border-bottom: 1px solid var(--border); }
  td { padding: .6rem 1rem; border-bottom: 1px solid var(--border); font-family: var(--mono); font-size: .78rem; }
  tr:hover td { background: #1a1f2a; }

  .badge {
    display: inline-block;
    padding: .15rem .5rem;
    border-radius: 4px;
    font-size: .65rem;
    font-weight: 600;
    letter-spacing: .05em;
  }
  .badge.ok     { background: rgba(0,229,160,.12); color: var(--green); }
  .badge.error  { background: rgba(255,77,109,.12); color: var(--red); }
  .badge.woo-ml { background: rgba(77,158,255,.1);  color: var(--blue); }
  .badge.ml-woo { background: rgba(255,201,77,.1);  color: var(--yellow); }

  .section { margin-bottom: 2.5rem; }
  .no-data { color: var(--muted); font-family: var(--mono); font-size: .8rem; padding: 1rem; }
</style>
</head>
<body>
<header>
  <div class="pulse"></div>
  <div class="logo">Woo ↔ ML Sync Monitor</div>
  <div class="refresh-hint">auto-refresh 30s · {{ now }}</div>
</header>
<main>

  <div class="grid">
    <div class="card">
      <div class="card-label">Productos mapeados</div>
      <div class="card-value blue">{{ stats.mapped }}</div>
    </div>
    <div class="card">
      <div class="card-label">Sync OK (24h)</div>
      <div class="card-value green">{{ stats.ok_24h }}</div>
    </div>
    <div class="card">
      <div class="card-label">Errores (24h)</div>
      <div class="card-value {% if stats.errors_24h > 0 %}red{% else %}green{% endif %}">{{ stats.errors_24h }}</div>
    </div>
    <div class="card">
      <div class="card-label">Órdenes ML procesadas</div>
      <div class="card-value yellow">{{ stats.orders_processed }}</div>
    </div>
    <div class="card">
      <div class="card-label">Última sync WOO→ML</div>
      <div class="card-value" style="font-size:1rem; color:var(--text)">{{ stats.last_woo_ml or '—' }}</div>
    </div>
    <div class="card">
      <div class="card-label">Última sync ML→WOO</div>
      <div class="card-value" style="font-size:1rem; color:var(--text)">{{ stats.last_ml_woo or '—' }}</div>
    </div>
  </div>

  <div class="section">
    <h2>Log reciente (últimas 50 entradas)</h2>
    {% if logs %}
    <table>
      <thead>
        <tr>
          <th>Timestamp</th>
          <th>Dirección</th>
          <th>Entidad</th>
          <th>Referencia</th>
          <th>Estado</th>
          <th>Detalle</th>
        </tr>
      </thead>
      <tbody>
        {% for row in logs %}
        <tr>
          <td>{{ row[0][:19] }}</td>
          <td><span class="badge {% if 'WOO' in row[1] %}woo-ml{% else %}ml-woo{% endif %}">{{ row[1] }}</span></td>
          <td>{{ row[2] }}</td>
          <td>{{ row[3] }}</td>
          <td><span class="badge {{ row[4] }}">{{ row[4] }}</span></td>
          <td style="color:var(--muted); max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">{{ row[5] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="no-data">Sin registros aún.</p>
    {% endif %}
  </div>

  <div class="section">
    <h2>Mapeo de productos ({{ mappings|length }})</h2>
    {% if mappings %}
    <table>
      <thead>
        <tr><th>WOO ID</th><th>ML ID</th><th>SKU</th><th>Última sync</th></tr>
      </thead>
      <tbody>
        {% for m in mappings %}
        <tr>
          <td>{{ m[0] }}</td>
          <td>{{ m[1] }}</td>
          <td>{{ m[2] or '—' }}</td>
          <td>{{ (m[3] or '—')[:19] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="no-data">Sin mapeos. Ejecutá: python map_products.py</p>
    {% endif %}
  </div>

</main>
</body>
</html>
"""

@app.route("/")
def index():
    con = get_db()

    logs = con.execute(
        "SELECT ts, direction, entity, ref_id, status, detail FROM sync_log ORDER BY id DESC LIMIT 50"
    ).fetchall()

    mappings = con.execute(
        "SELECT woo_id, ml_id, sku, last_synced FROM product_map ORDER BY last_synced DESC"
    ).fetchall()

    stats = {
        "mapped": con.execute("SELECT COUNT(*) FROM product_map").fetchone()[0],
        "ok_24h": con.execute(
            "SELECT COUNT(*) FROM sync_log WHERE status='ok' AND ts > datetime('now','-24 hours')"
        ).fetchone()[0],
        "errors_24h": con.execute(
            "SELECT COUNT(*) FROM sync_log WHERE status='error' AND ts > datetime('now','-24 hours')"
        ).fetchone()[0],
        "orders_processed": con.execute(
            "SELECT COUNT(*) FROM sync_log WHERE direction='ML->WOO' AND entity='order' AND status='ok'"
        ).fetchone()[0],
        "last_woo_ml": con.execute(
            "SELECT ts FROM sync_log WHERE direction='WOO->ML' AND status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone(),
        "last_ml_woo": con.execute(
            "SELECT ts FROM sync_log WHERE direction='ML->WOO' AND status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone(),
    }

    if stats["last_woo_ml"]:
        stats["last_woo_ml"] = stats["last_woo_ml"][0][:19]
    if stats["last_ml_woo"]:
        stats["last_ml_woo"] = stats["last_ml_woo"][0][:19]

    con.close()
    return render_template_string(
        TEMPLATE,
        logs=logs,
        mappings=mappings,
        stats=stats,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@app.route("/api/stats")
def api_stats():
    con = get_db()
    data = {
        "mapped": con.execute("SELECT COUNT(*) FROM product_map").fetchone()[0],
        "errors_24h": con.execute(
            "SELECT COUNT(*) FROM sync_log WHERE status='error' AND ts > datetime('now','-24 hours')"
        ).fetchone()[0],
    }
    con.close()
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
