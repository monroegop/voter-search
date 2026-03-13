import os
import sqlite3
import csv
import io
import time
import json
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production-abc123")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "changeme")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "voters.db"))
META_PATH = os.environ.get("META_PATH", os.path.join(os.path.dirname(__file__), "meta.json"))

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
        if request.form.get("password", "") == APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Incorrect password. Please try again."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name):
    """Turn a CSV header into a safe SQLite column name."""
    import re
    s = name.strip().lower()
    s = re.sub(r'[^a-z0-9]+', '_', s)
    s = s.strip('_')
    if not s:
        s = 'col'
    # avoid reserved words
    if s in ('index','select','from','where','table','order','group','by'):
        s = 'col_' + s
    return s

def save_meta(columns):
    with open(META_PATH, 'w') as f:
        json.dump({"columns": columns}, f)

def load_meta():
    try:
        with open(META_PATH) as f:
            return json.load(f).get("columns", [])
    except:
        return []

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

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/status")
@api_login_required
def status():
    columns = load_meta()
    try:
        count = get_db().execute("SELECT COUNT(*) FROM voters").fetchone()[0]
    except:
        count = 0
    return jsonify({"count": count, "columns": columns})

@app.route("/api/upload", methods=["POST"])
@api_login_required
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400

    stream = io.StringIO(f.stream.read().decode("utf-8-sig", errors="replace"))
    reader = csv.reader(stream)

    try:
        raw_headers = next(reader)
    except StopIteration:
        return jsonify({"error": "File is empty"}), 400

    # Build column list: {slug, label}
    seen = {}
    columns = []
    for h in raw_headers:
        label = h.strip()
        if not label:
            continue
        slug = slugify(label)
        # deduplicate
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}_{seen[slug]}"
        else:
            seen[slug] = 0
        columns.append({"slug": slug, "label": label})

    if not columns:
        return jsonify({"error": "No columns found in CSV"}), 400

    # Build dynamic table
    col_defs = ", ".join(f'"{c["slug"]}" TEXT' for c in columns)
    start = time.time()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("DROP TABLE IF EXISTS voters")
        conn.execute(f"CREATE TABLE voters (id INTEGER PRIMARY KEY AUTOINCREMENT, {col_defs})")
        for c in columns:
            conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{c["slug"]}" ON voters("{c["slug"]}" COLLATE NOCASE)')

        slugs = [c["slug"] for c in columns]
        placeholders = ", ".join("?" for _ in slugs)
        col_names = ", ".join(f'"{s}"' for s in slugs)
        insert_sql = f"INSERT INTO voters ({col_names}) VALUES ({placeholders})"

        batch, total = [], 0
        for row in reader:
            vals = []
            for i in range(len(slugs)):
                vals.append(row[i].strip() if i < len(row) else "")
            batch.append(vals)
            total += 1
            if len(batch) >= 5000:
                conn.executemany(insert_sql, batch)
                batch = []
        if batch:
            conn.executemany(insert_sql, batch)
        conn.commit()

    save_meta(columns)
    elapsed = round(time.time() - start, 2)
    return jsonify({"imported": total, "elapsed": elapsed, "columns": columns})

@app.route("/api/search")
@api_login_required
def search():
    columns = load_meta()
    if not columns:
        return jsonify({"total": 0, "page": 1, "per_page": 50, "pages": 1, "results": [], "columns": []})

    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, int(request.args.get("per_page", 50)))

    conditions, params = [], []
    for c in columns:
        v = request.args.get(c["slug"], "").strip()
        if v:
            conditions.append(f'"{c["slug"]}" LIKE ? COLLATE NOCASE')
            params.append(f"%{v}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM voters {where}", params).fetchone()[0]

    # default sort by first column
    sort_col = request.args.get("sort", columns[0]["slug"])
    sort_dir = "DESC" if request.args.get("dir", "asc") == "desc" else "ASC"
    safe_sort = sort_col if any(c["slug"] == sort_col for c in columns) else columns[0]["slug"]

    rows = db.execute(
        f'SELECT * FROM voters {where} ORDER BY "{safe_sort}" COLLATE NOCASE {sort_dir} LIMIT ? OFFSET ?',
        params + [per_page, (page - 1) * per_page]
    ).fetchall()

    return jsonify({
        "total": total, "page": page, "per_page": per_page,
        "pages": max(1, -(-total // per_page)),
        "columns": columns,
        "results": [dict(r) for r in rows]
    })

@app.route("/api/clear", methods=["POST"])
@api_login_required
def clear():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS voters")
        conn.commit()
    save_meta([])
    return jsonify({"ok": True})

if __name__ == "__main__":
    print("\n✅  Voter Search is running → http://localhost:5000\n")
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
