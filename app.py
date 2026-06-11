import os
import psycopg2
import psycopg2.extras
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from flask_cors import CORS

# ── Config ───────────────────────────────────────────────────────────────────
SECRET_KEY   = "golfplaying2026secret"
PASSWORD     = "Golf2026+"
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Please set it to a valid PostgreSQL connection string."
    )

app = Flask(__name__)
app.secret_key = SECRET_KEY
CORS(app, supports_credentials=True)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id   SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rounds (
        id           SERIAL PRIMARY KEY,
        player       TEXT NOT NULL,
        round_date   TEXT NOT NULL,
        club         TEXT NOT NULL,
        cr           REAL,
        slope        REAL,
        par          INTEGER,
        strokes      INTEGER,
        netto_points INTEGER,
        hcp_index    REAL,
        playing_hcp  INTEGER,
        tees         TEXT DEFAULT 'Herren',
        created_at   TEXT
    )""")

    # Add tees column if it doesn't exist (migration for existing DBs)
    cur.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='rounds' AND column_name='tees'
        ) THEN
            ALTER TABLE rounds ADD COLUMN tees TEXT DEFAULT 'Herren';
        END IF;
    END$$;
    """)

    # Seed players
    seed_players = ["Rolf", "Isabelle", "Cedric", "Remo", "Vivien", "Vincent", "Ana"]
    for p in seed_players:
        cur.execute("INSERT INTO players (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (p,))

    db.commit()
    cur.close()
    db.close()

# ── Auth decorator ────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    if data.get("password") == PASSWORD:
        session["logged_in"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Falsches Passwort"}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/check_session")
def check_session():
    return jsonify({"logged_in": bool(session.get("logged_in"))})

# ── Players ───────────────────────────────────────────────────────────────────
@app.route("/api/players", methods=["GET"])
@login_required
def get_players():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT name FROM players ORDER BY name")
    players = [r["name"] for r in cur.fetchall()]
    cur.close()
    db.close()
    return jsonify(players)

@app.route("/api/players", methods=["POST"])
@login_required
def add_player():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name fehlt"}), 400
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO players (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"ok": True})

# ── Rounds ────────────────────────────────────────────────────────────────────
@app.route("/api/rounds", methods=["GET"])
@login_required
def get_rounds():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, player, round_date, club, cr, slope, par,
               strokes, netto_points, hcp_index, playing_hcp, tees, created_at
        FROM rounds
        ORDER BY round_date DESC, created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/rounds", methods=["POST"])
@login_required
def add_round():
    data = request.get_json() or {}
    required = ["player", "round_date", "club", "strokes", "netto_points", "hcp_index", "playing_hcp"]
    for field in required:
        if data.get(field) is None or data.get(field) == "":
            return jsonify({"error": f"Feld '{field}' fehlt"}), 400

    # Auto-save new player name
    player_name = str(data["player"]).strip()
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO players (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (player_name,))

    cur.execute("""
        INSERT INTO rounds
            (player, round_date, club, cr, slope, par, strokes, netto_points, hcp_index, playing_hcp, tees, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        player_name,
        data["round_date"],
        data["club"],
        data.get("cr"),
        data.get("slope"),
        data.get("par"),
        int(data["strokes"]),
        int(data["netto_points"]),
        float(data["hcp_index"]),
        int(data["playing_hcp"]),
        data.get("tees", "Herren"),
        datetime.utcnow().isoformat()
    ))
    new_id = cur.fetchone()[0]
    db.commit()
    cur.close()
    db.close()
    return jsonify({"ok": True, "id": new_id}), 201

@app.route("/api/rounds/<int:round_id>", methods=["DELETE"])
@login_required
def delete_round(round_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM rounds WHERE id = %s", (round_id,))
    db.commit()
    cur.close()
    db.close()
    return jsonify({"ok": True})

# ── Stats ─────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def get_stats():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS total FROM rounds")
    total = cur.fetchone()["total"]

    cur.execute("SELECT MIN(hcp_index) AS best_hcp FROM rounds")
    best_hcp = cur.fetchone()["best_hcp"]

    cur.execute("SELECT AVG(netto_points) AS avg_netto FROM rounds")
    avg_netto = cur.fetchone()["avg_netto"]

    cur.execute("""
        SELECT playing_hcp FROM rounds
        ORDER BY round_date DESC, created_at DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    last_playing_hcp = row["playing_hcp"] if row else None

    cur.close()
    db.close()

    return jsonify({
        "total_rounds": total,
        "best_hcp_index": round(best_hcp, 1) if best_hcp is not None else None,
        "avg_netto_points": round(avg_netto, 1) if avg_netto is not None else None,
        "last_playing_hcp": last_playing_hcp
    })

# ── Init & Run ────────────────────────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
