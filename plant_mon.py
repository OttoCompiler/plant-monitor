#!/usr/bin/env python3
"""
plant_mon.py — Flask Plant Monitoring
"""
from flask import (
    Flask,
    g,
    render_template_string,
    request,
    redirect,
    url_for,
    jsonify,
    send_file,
    abort,
)
import sqlite3
import os
from datetime import datetime, timedelta, timezone
import io
import csv

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_ROOT, "plants.db")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    db.executescript(
        """
    CREATE TABLE IF NOT EXISTS plants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        species TEXT,
        location TEXT,
        water_interval_days INTEGER DEFAULT 7,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS water_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plant_id INTEGER NOT NULL,
        watered_at TEXT NOT NULL,
        note TEXT,
        FOREIGN KEY(plant_id) REFERENCES plants(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_water_logs_plant ON water_logs(plant_id);
    """
    )
    db.commit()


@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, "_db", None)
    if db:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def human_delta(future_dt):
    """Return a short human-friendly delta: 'in 3d', '2d ago', 'today'"""
    try:
        now = datetime.now(timezone.utc)
        diff = future_dt - now
        days = diff.days
        if abs(days) < 1:
            return "today"
        if days > 0:
            return f"in {days}d"
        return f"{abs(days)}d ago"
    except Exception:
        return ""


def get_last_watered(db, plant_id):
    r = db.execute(
        "SELECT watered_at FROM water_logs WHERE plant_id = ? ORDER BY watered_at DESC LIMIT 1",
        (plant_id,),
    ).fetchone()
    return r["watered_at"] if r else None


def compute_next_watering(db, plant_row):
    """
    ISO timestamp for next watering (UTC)
    """
    last_iso = get_last_watered(db, plant_row["id"])
    if last_iso:
        last = parse_iso(last_iso)
    else:
        last = parse_iso(plant_row["created_at"])
    if last is None:
        return None
    interval = plant_row["water_interval_days"] or 7
    next_dt = last + timedelta(days=interval)
    return next_dt.isoformat()


BASE_HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Plant Growth Monitor</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;600;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#fbf6f0;
  --card:#fffaf4;
  --muted:#6b5740;
  --accent:#b07a2f;
  --accent2:#f2c57c;
  --green:#5aa469;
  --danger:#d9534f;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial;
}
*{box-sizing:border-box}
body{margin:0;background:
      radial-gradient(700px 300px at 10% 10%, rgba(176,122,47,0.06), transparent 20%),
      radial-gradient(800px 300px at 90% 90%, rgba(242,197,124,0.04), transparent 20%),
      var(--bg);
      color: #2b2b2b;padding:28px}
.header{display:flex;align-items:center;gap:16px;margin-bottom:20px}
.logo{width:64px;height:64px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;color:#111;font-weight:800;}
h1{margin:0;font-size:22px}
.lead{color:var(--muted);font-size:13px;margin-top:6px}

.controls{margin-left:auto;display:flex;gap:10px;align-items:center}
.btn{background:transparent;border:1px solid rgba(0,0,0,0.06);padding:10px 14px;border-radius:10px;cursor:pointer;font-weight:700}
.btn.primary{background:var(--accent);color:#111;border:none}
.grid{display:grid;grid-template-columns:360px 1fr;gap:22px;align-items:start}
.panel{background:var(--card);border-radius:14px;padding:16px;border:1px solid rgba(0,0,0,0.04);box-shadow:0 10px 30px rgba(0,0,0,0.04)}
.form-row{display:flex;gap:8px;margin-bottom:10px}
label.small{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}
input, select, textarea{width:100%;padding:10px;border-radius:9px;border:1px solid rgba(0,0,0,0.06);font-size:14px}
textarea{min-height:120px;resize:vertical;font-family:monospace}
.list{display:flex;flex-direction:column;gap:12px}
.plant-card{display:flex;justify-content:space-between;align-items:flex-start;padding:12px;border-radius:12px;background:linear-gradient(180deg,#fff,#fffaf4);border:1px solid rgba(0,0,0,0.02)}
.meta{display:flex;gap:10px;align-items:center}
.plant-name{font-weight:800}
.badge{padding:6px 8px;border-radius:999px;font-weight:700;font-size:12px}
.due{background:var(--danger);color:white}
.ok{background:var(--green);color:white}
.small{font-size:12px;color:var(--muted)}
.row{display:flex;gap:8px;align-items:center}
.table{width:100%;border-collapse:collapse;margin-top:12px}
.table th{text-align:left;color:var(--muted);font-size:13px;padding:6px 0}
.table td{padding:8px 0;border-top:1px dashed rgba(0,0,0,0.03);font-size:14px}
.footer{margin-top:18px;color:var(--muted);font-size:13px;text-align:center}

@media(max-width:900px){
  .grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="header">
  
  <div>
    <h1>Plant Growth Monitor</h1>
    <h5>by OttoCompiler</h5>
    <div class="lead">Keep track of watering, locations and notes for your plants.</div>
  </div>

  <div class="controls">
    <a class="btn" href="{{ url_for('index') }}">Dashboard</a>
    <a class="btn" href="{{ url_for('new_plant') }}">New Plant</a>
    <button class="btn" onclick="location.href='{{ url_for('export_csv') }}'">Export CSV</button>
    <button class="btn" onclick="clearAll()">Clear All</button>
  </div>
</div>

<div class="grid">
  <!-- left: quick add & filters -->
  <div>
    <div class="panel">
      <h3 style="margin-top:0">Quick Add Plant</h3>
      <form method="post" action="{{ url_for('create_plant') }}">
        <div class="form-row">
          <div style="flex:1">
            <label class="small">Name</label>
            <input name="name" placeholder="Monstera Deliciosa" required>
          </div>
        </div>
        <div class="form-row">
          <div style="flex:1">
            <label class="small">Species / Variety</label>
            <input name="species" placeholder="Monstera deliciosa">
          </div>
        </div>
        <div class="form-row">
          <div style="flex:1">
            <label class="small">Location (room/shelf)</label>
            <input name="location" placeholder="Living room / East shelf">
          </div>
        </div>

        <div class="form-row">
          <div style="flex:1">
            <label class="small">Water interval (days)</label>
            <input name="water_interval_days" type="number" min="1" value="7">
          </div>
        </div>

        <div class="form-row">
          <div style="flex:1">
            <label class="small">Notes</label>
            <textarea name="notes" placeholder="Light needs, fertilizer, etc"></textarea>
          </div>
        </div>

        <div style="display:flex;gap:8px">
          <button class="btn primary" type="submit">Add Plant</button>
          <a class="btn" href="{{ url_for('index') }}">Cancel</a>
        </div>
      </form>
    </div>

    <div class="panel" style="margin-top:12px">
      <h3 style="margin-top:0">Filters</h3>
      <form id="filterForm" method="get" action="{{ url_for('index') }}">
        <label class="small">Search</label>
        <input name="q" placeholder="name, species, location" value="{{ q|default('') }}">
        <label class="small" style="margin-top:8px">Show</label>
        <select name="show">
          <option value="all" {% if show=='all' %}selected{% endif %}>All plants</option>
          <option value="due" {% if show=='due' %}selected{% endif %}>Due for watering</option>
        </select>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button class="btn" type="submit">Apply</button>
          <a class="btn" href="{{ url_for('index') }}">Reset</a>
        </div>
      </form>
    </div>
  </div>

  <!-- right: list & detail -->
  <div>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <h2 style="margin:0">Plants</h2>
          <div class="small">Total: {{ total }} &middot; Showing: {{ plant_count }}</div>
        </div>
        <div class="small">Updated: {{ now }}</div>
      </div>

      <div style="margin-top:12px" class="list">
        {% for p in plants %}
          <div class="plant-card">
            <div style="flex:1">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
                <div>
                  <div class="plant-name">{{ p.name }}</div>
                  <div class="small">{{ p.species or '' }} {% if p.location %}&middot; {{ p.location }}{% endif %}</div>
                </div>
                <div class="row">
                  {% if p.next_watering %}
                    {% set next_dt = p.next_watering_dt %}
                    {% if next_dt <= now_dt %}
                      <div class="badge due">Water now</div>
                    {% else %}
                      <div class="badge ok">Next: {{ p.next_due_human }}</div>
                    {% endif %}
                  {% else %}
                    <div class="badge ok">No data</div>
                  {% endif %}
                </div>
              </div>

              <div style="margin-top:10px" class="small">
                Last watered: {{ p.last_watered_display or '—' }} · Interval: {{ p.water_interval_days }}d
              </div>
            </div>

            <div style="margin-left:12px;text-align:right">
              <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
                <a class="btn" href="{{ url_for('view_plant', plant_id=p.id) }}">Open</a>
                <a class="btn" href="{{ url_for('edit_plant', plant_id=p.id) }}">Edit</a>
                <form method="post" action="{{ url_for('delete_plant', plant_id=p.id) }}" style="margin:0">
                  <button class="btn" type="submit" onclick="return confirm('Delete plant?');">Delete</button>
                </form>
              </div>
            </div>
          </div>
        {% else %}
          <div class="small">No plants yet — add one with the form at left.</div>
        {% endfor %}
      </div>

    </div>

    {% if detail %}
      <div class="panel" style="margin-top:12px">
        <h3 style="margin-top:0">{{ detail.name }} • {{ detail.species or '' }}</h3>
        <div class="small">Location: {{ detail.location or '—' }} · Added: {{ detail.created_at }}</div>

        <div style="margin-top:12px">
          <h4 style="margin-bottom:6px">Notes</h4>
          <div class="small" style="white-space:pre-wrap">{{ detail.notes or '—' }}</div>
        </div>

        <div style="margin-top:12px">
          <h4 style="margin-bottom:6px">Watering</h4>
          <div class="small">Interval: {{ detail.water_interval_days }} days</div>
          <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
            <form method="post" action="{{ url_for('log_water', plant_id=detail.id) }}">
              <input type="hidden" name="watered_at" value="{{ now_iso }}">
              <input type="text" name="note" placeholder="optional note" style="padding:8px;border-radius:8px;border:1px solid rgba(0,0,0,0.06)">
              <button class="btn primary" type="submit">Log Water Now</button>
            </form>
            <form method="post" action="{{ url_for('log_water_backdate', plant_id=detail.id) }}" style="display:inline">
              <input type="date" name="date" value="{{ today_date }}">
              <button class="btn" type="submit">Log Date</button>
            </form>
          </div>

          <table class="table" style="margin-top:12px">
            <thead><tr><th>Date</th><th>Note</th></tr></thead>
            <tbody>
              {% for w in logs %}
                <tr><td>{{ w.watered_at_display }}</td><td class="small">{{ w.note or '' }}</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

      </div>
    {% endif %}

    <div class="footer">Plant Data saved to Local SQLite • {{ now }}</div>
  </div>
</div>

<script>
async function clearAll(){
  if(!confirm("Clear ALL plants and logs? This cannot be undone.")) return;
  let r = await fetch("/api/clear", {method:"POST"});
  let j = await r.json();
  alert("Deleted: " + (j.deleted_plants || 0) + " plants, " + (j.deleted_logs || 0) + " logs.");
  location.href = "/";
}
</script>

</body>
</html>
"""


@app.route("/")
def index():
    init_db()
    db = get_db()

    # filters
    q = (request.args.get("q") or "").strip()
    show = request.args.get("show") or "all"

    # build base query
    rows = db.execute("SELECT * FROM plants ORDER BY name COLLATE NOCASE").fetchall()
    plants = []
    now = datetime.now(timezone.utc)
    for r in rows:
        p = dict(r)
        # last watered
        last_iso = get_last_watered(db, p["id"])
        last_dt = parse_iso(last_iso) if last_iso else None
        p["last_watered_display"] = last_dt.astimezone().strftime("%b %d, %Y %H:%M") if last_dt else None
        p["created_at"] = parse_iso(p["created_at"]).astimezone().strftime("%b %d, %Y") if p["created_at"] else ""
        # next watering
        next_iso = compute_next_watering(db, p)
        p["next_watering"] = next_iso
        p["next_watering_dt"] = parse_iso(next_iso) if next_iso else None
        p["next_due_human"] = human_delta(p["next_watering_dt"]) if p["next_watering_dt"] else ""
        plants.append(p)

    # apply query filtering
    def matches(p):
        if q:
            ql = q.lower()
            if ql in (p["name"] or "").lower() or ql in (p.get("species") or "").lower() or ql in (p.get("location") or "").lower():
                pass
            else:
                return False
        if show == "due":
            if not p["next_watering_dt"]:
                return False
            if p["next_watering_dt"] > now:
                return False
        return True

    filtered = [p for p in plants if matches(p)]

    # detail view (optional)
    detail = None
    logs = []
    detail_id = request.args.get("detail")
    if detail_id:
        drow = db.execute("SELECT * FROM plants WHERE id = ?", (detail_id,)).fetchone()
        if drow:
            detail = dict(drow)
            detail["created_at"] = parse_iso(detail["created_at"]).astimezone().strftime("%b %d, %Y %H:%M")
            # logs
            wrows = db.execute("SELECT * FROM water_logs WHERE plant_id = ? ORDER BY watered_at DESC", (detail["id"],)).fetchall()
            logs = []
            for w in wrows:
                wd = dict(w)
                wd_dt = parse_iso(wd["watered_at"])
                wd["watered_at_display"] = wd_dt.astimezone().strftime("%b %d, %Y %H:%M") if wd_dt else wd["watered_at"]
                logs.append(wd)

    return render_template_string(
        BASE_HTML,
        plants=filtered,
        total=len(plants),
        plant_count=len(filtered),
        detail=detail,
        logs=logs,
        now=datetime.now().strftime("%b %d, %Y %H:%M"),
        now_iso=now_iso(),
        q=q,
        show=show,
        today_date=datetime.now().strftime("%Y-%m-%d"),
        now_dt=datetime.now(timezone.utc),
    )


@app.route("/plants/new")
def new_plant():
    return redirect(url_for("index") + "#new")


@app.route("/plants/create", methods=["POST"])
def create_plant():
    init_db()
    db = get_db()
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("index"))
    species = (request.form.get("species") or "").strip()
    location = (request.form.get("location") or "").strip()
    try:
        interval = int(request.form.get("water_interval_days") or 7)
        interval = max(1, interval)
    except Exception:
        interval = 7
    notes = request.form.get("notes") or ""
    now = now_iso()
    db.execute(
        "INSERT INTO plants (name,species,location,water_interval_days,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (name, species, location, interval, notes, now, now),
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/plants/<int:plant_id>")
def view_plant(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        abort(404)
    return redirect(url_for("index", detail=plant_id))


@app.route("/plants/<int:plant_id>/edit")
def edit_plant(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        abort(404)
    p = dict(row)
    p["created_at"] = parse_iso(p["created_at"]).astimezone().strftime("%b %d, %Y %H:%M")
    return render_template_string(
        """
        <!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Edit Plant</title>
        <style>
        body{font-family:Inter,system-ui;padding:24px;background:#fbf6f0;color:#333}
        .panel{background:white;padding:18px;border-radius:12px;max-width:720px;margin:24px auto;border:1px solid rgba(0,0,0,0.04)}
        input,textarea{width:100%;padding:10px;border-radius:8px;border:1px solid rgba(0,0,0,0.06);margin-bottom:8px}
        .row{display:flex;gap:8px}
        .btn{padding:10px 12px;border-radius:8px;border:none;cursor:pointer}
        </style>
        </head><body>
        <div class="panel">
        <h2>Edit {{ p.name }}</h2>
        <form method="post" action="{{ url_for('update_plant', plant_id=p.id) }}">
          <label>Name</label>
          <input name="name" value="{{ p.name }}" required>
          <label>Species</label>
          <input name="species" value="{{ p.species or '' }}">
          <label>Location</label>
          <input name="location" value="{{ p.location or '' }}">
          <label>Water interval days</label>
          <input name="water_interval_days" type="number" value="{{ p.water_interval_days }}">
          <label>Notes</label>
          <textarea name="notes">{{ p.notes or '' }}</textarea>
          <div style="display:flex;gap:8px">
            <button class="btn" type="submit" style="background:#b07a2f;color:#111;font-weight:700">Save</button>
            <a class="btn" href="{{ url_for('index') }}">Cancel</a>
            <form method="post" action="{{ url_for('delete_plant', plant_id=p.id) }}" style="margin-left:auto" onsubmit="return confirm('Delete?')">
              <button class="btn" style="background:#ddd">Delete</button>
            </form>
          </div>
        </form>
        </div>
        </body></html>
        """,
        p=p,
    )


@app.route("/plants/<int:plant_id>/update", methods=["POST"])
def update_plant(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        abort(404)
    name = (request.form.get("name") or "").strip()
    species = (request.form.get("species") or "").strip()
    location = (request.form.get("location") or "").strip()
    try:
        interval = int(request.form.get("water_interval_days") or 7)
        interval = max(1, interval)
    except Exception:
        interval = 7
    notes = request.form.get("notes") or ""
    now = now_iso()
    db.execute(
        "UPDATE plants SET name=?,species=?,location=?,water_interval_days=?,notes=?,updated_at=? WHERE id=?",
        (name, species, location, interval, notes, now, plant_id),
    )
    db.commit()
    return redirect(url_for("index", detail=plant_id))


@app.route("/plants/<int:plant_id>/delete", methods=["POST"])
def delete_plant(plant_id):
    init_db()
    db = get_db()
    db.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
    db.execute("DELETE FROM water_logs WHERE plant_id = ?", (plant_id,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/plants/<int:plant_id>/water", methods=["POST"])
def log_water(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        abort(404)
    watered_at = request.form.get("watered_at") or now_iso()
    note = request.form.get("note") or ""
    try:
        _ = parse_iso(watered_at)
    except Exception:
        watered_at = now_iso()
    db.execute("INSERT INTO water_logs (plant_id,watered_at,note) VALUES (?,?,?)", (plant_id, watered_at, note))
    db.commit()
    return redirect(url_for("index", detail=plant_id))


@app.route("/plants/<int:plant_id>/water/date", methods=["POST"])
def log_water_backdate(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        abort(404)
    date_str = request.form.get("date")
    try:
        dt = datetime.fromisoformat(date_str)
        watered_at = dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        watered_at = now_iso()
    note = request.form.get("note") or ("backdated")
    db.execute("INSERT INTO water_logs (plant_id,watered_at,note) VALUES (?,?,?)", (plant_id, watered_at, note))
    db.commit()
    return redirect(url_for("index", detail=plant_id))


@app.route("/export.csv")
def export_csv():
    init_db()
    db = get_db()
    plants = db.execute("SELECT * FROM plants ORDER BY name").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "species", "location", "water_interval_days", "created_at", "updated_at", "last_watered", "next_watering"])
    for p in plants:
        p = dict(p)
        last_iso = get_last_watered(db, p["id"])
        next_iso = compute_next_watering(db, p)
        writer.writerow([p["id"], p["name"], p["species"], p["location"], p["water_interval_days"], p["created_at"], p["updated_at"], last_iso or "", next_iso or ""])
    buf.seek(0)
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="plants_export.csv")


@app.route("/api/plants", methods=["GET", "POST"])
def api_plants():
    init_db()
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM plants ORDER BY name").fetchall()
        out = []
        for r in rows:
            p = dict(r)
            p["last_watered"] = get_last_watered(db, p["id"])
            p["next_watering"] = compute_next_watering(db, p)
            out.append(p)
        return jsonify(out)
    else:
        data = request.get_json(force=True)
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        species = data.get("species")
        location = data.get("location")
        try:
            interval = int(data.get("water_interval_days") or 7)
            interval = max(1, interval)
        except Exception:
            interval = 7
        notes = data.get("notes") or ""
        now = now_iso()
        cur = db.execute("INSERT INTO plants (name,species,location,water_interval_days,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (name, species, location, interval, notes, now, now))
        db.commit()
        pid = cur.lastrowid
        return jsonify({"id": pid}), 201


@app.route("/api/plants/<int:plant_id>", methods=["GET", "PUT", "DELETE"])
def api_plant(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        p = dict(row)
        p["last_watered"] = get_last_watered(db, plant_id)
        p["next_watering"] = compute_next_watering(db, p)
        return jsonify(p)
    if request.method == "DELETE":
        db.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
        db.execute("DELETE FROM water_logs WHERE plant_id = ?", (plant_id,))
        db.commit()
        return jsonify({"ok": True})
    data = request.get_json(force=True)
    name = (data.get("name") or row["name"]).strip()
    species = data.get("species", row["species"])
    location = data.get("location", row["location"])
    try:
        interval = int(data.get("water_interval_days") or row["water_interval_days"])
        interval = max(1, interval)
    except Exception:
        interval = row["water_interval_days"]
    notes = data.get("notes", row["notes"])
    now = now_iso()
    db.execute("UPDATE plants SET name=?,species=?,location=?,water_interval_days=?,notes=?,updated_at=? WHERE id=?",
               (name, species, location, interval, notes, now, plant_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/plants/<int:plant_id>/water", methods=["POST"])
def api_log_water(plant_id):
    init_db()
    db = get_db()
    row = db.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(force=True) if request.is_json else request.form
    watered_at = payload.get("watered_at") or now_iso()
    note = payload.get("note") or ""
    # validate iso
    try:
        _ = parse_iso(watered_at)
    except Exception:
        watered_at = now_iso()
    db.execute("INSERT INTO water_logs (plant_id,watered_at,note) VALUES (?,?,?)", (plant_id, watered_at, note))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    init_db()
    db = get_db()
    deleted_logs = db.execute("DELETE FROM water_logs").rowcount
    deleted_plants = db.execute("DELETE FROM plants").rowcount
    db.commit()
    return jsonify({"status": "ok", "deleted_plants": deleted_plants, "deleted_logs": deleted_logs})


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=5019, debug=True)
