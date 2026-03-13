import os
import sqlite3
import csv
import io
import time
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production-abc123")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "changeme")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "voters.db"))

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Incorrect password. Please try again."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
        g.db.execute("PRAGMA cache_size=100000")
        g.db.execute("PRAGMA temp_store=MEMORY")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voter_id TEXT, first_name TEXT, last_name TEXT,
                address TEXT, town TEXT, party TEXT
            )
        """)
        for col in ["voter_id","first_name","last_name","address","town","party"]:
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{col} ON voters({col} COLLATE NOCASE)")
        conn.commit()

# ── Column mapping ────────────────────────────────────────────────────────────

COLUMN_MAP = {
    "voter id":"voter_id","voterid":"voter_id","voter_id":"voter_id","id":"voter_id",
    "regnum":"voter_id","registration number":"voter_id","reg_num":"voter_id",
    "first name":"first_name","firstname":"first_name","first_name":"first_name","fname":"first_name","given name":"first_name",
    "last name":"last_name","lastname":"last_name","last_name":"last_name","lname":"last_name","surname":"last_name","family name":"last_name",
    "address":"address","street":"address","street address":"address","street_address":"address","addr":"address","res address":"address",
    "town":"town","city":"town","municipality":"town","city/town":"town","city_town":"town","muni":"town","residence city":"town",
    "party":"party","party affiliation":"party","party_affiliation":"party","affiliation":"party","political party":"party","enrollment":"party",
}

def map_headers(headers):
    mapped = {}
    for i, h in enumerate(headers):
        key = h.strip().lower().replace('"','')
        if key in COLUMN_MAP:
            mapped[i] = COLUMN_MAP[key]
    return mapped

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/status")
@api_login_required
def status():
    count = get_db().execute("SELECT COUNT(*) FROM voters").fetchone()[0]
    return jsonify({"count": count})

@app.route("/api/upload", methods=["POST"])
@api_login_required
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    stream = io.StringIO(f.stream.read().decode("utf-8-sig", errors="replace"))
    reader = csv.reader(stream)
    try:
        headers = next(reader)
    except StopIteration:
        return jsonify({"error": "File is empty"}), 400
    col_map = map_headers(headers)
    if not col_map:
        return jsonify({"error": f"Could not map columns. Found: {', '.join(headers[:10])}"}), 400
    start = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("DELETE FROM voters")
        batch, total = [], 0
        for row in reader:
            rec = {"voter_id":"","first_name":"","last_name":"","address":"","town":"","party":""}
            for idx, col in col_map.items():
                if idx < len(row): rec[col] = row[idx].strip()
            batch.append((rec["voter_id"],rec["first_name"],rec["last_name"],rec["address"],rec["town"],rec["party"]))
            total += 1
            if len(batch) >= 5000:
                conn.executemany("INSERT INTO voters (voter_id,first_name,last_name,address,town,party) VALUES (?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT INTO voters (voter_id,first_name,last_name,address,town,party) VALUES (?,?,?,?,?,?)", batch)
        conn.commit()
    return jsonify({"imported": total, "elapsed": round(time.time()-start,2), "mapped_columns": list(set(col_map.values()))})

@app.route("/api/search")
@api_login_required
def search():
    fields = ["voter_id","first_name","last_name","address","town","party"]
    page = max(1, int(request.args.get("page",1)))
    per_page = min(100, int(request.args.get("per_page",50)))
    conditions, params = [], []
    for f in fields:
        v = request.args.get(f,"").strip()
        if v:
            conditions.append(f"{f} LIKE ? COLLATE NOCASE")
            params.append(f"%{v}%")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM voters {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT voter_id,first_name,last_name,address,town,party FROM voters {where} "
        f"ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE LIMIT ? OFFSET ?",
        params + [per_page, (page-1)*per_page]
    ).fetchall()
    return jsonify({"total":total,"page":page,"per_page":per_page,"pages":max(1,-(-total//per_page)),"results":[dict(r) for r in rows]})

@app.route("/api/clear", methods=["POST"])
@api_login_required
def clear():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM voters")
        conn.commit()
    return jsonify({"ok": True})

if __name__ == "__main__":
    init_db()
    print("\n✅  Voter Search is running → http://localhost:5000\n")
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
