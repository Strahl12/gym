"""
migrate.py — Create tables, seed exercise_roster from history + Hevy library.

Run once (idempotent):
    python migrate.py

Wipe and reseed the roster (e.g. after changing thresholds):
    python migrate.py --reseed

Star rating determines how often an exercise surfaces:
    5 ★  main lifts — always in rotation          (priority cap 3.0, target ~7d)
    4 ★  heavy rotation accessories               (cap 2.5, target ~14d)
    3 ★  common accessories                       (cap 2.0, target ~21d)
    2 ★  occasional — trained before, rarely now  (cap 1.0, target ~45d)
    1 ★  rare — in Hevy library but seldom/never done (cap 0.5, target ~90d)
    0 ★  excluded — never prescribed

Stars are auto-assigned from training history. Override any exercise with:
    UPDATE exercise_roster SET star_rating = 4 WHERE exercise_name = 'Cable Row';
Manual stars survive --reseed (INSERT OR IGNORE keeps existing rows).
"""
import sys
import re
import sqlite3
from datetime import date as _date, timedelta
import config

# ── Seeding thresholds (historical exercises) ─────────────────────────────
STAR_THRESHOLDS = {
    # (min_sessions, star)  — checked in order, first match wins
    20: 4,
    5:  3,
    1:  2,
}
# Hevy exercises with no personal history get star=1
DEFAULT_STAR = 1

# target_freq_days fallback when there's no training history to compute from
STAR_FREQ_DAYS = {5: 7.0, 4: 14.0, 3: 21.0, 2: 45.0, 1: 90.0}

# Hevy primary_muscle_group → our session type (unmapped = skipped)
MUSCLE_TO_SESSION = {
    "chest":      "push",
    "shoulders":  "push",
    "triceps":    "push",
    "biceps":     "arms",
    "forearms":   "arms",
    "lats":       "pull",
    "upper_back": "pull",
    "traps":      "pull",
    "lower_back": "pull",
    "quadriceps": "legs",
    "hamstrings": "legs",
    "glutes":     "legs",
    "calves":     "legs",
    "abductors":  "legs",
    "adductors":  "legs",
}

# Canonical main lift names (for star=5 auto-assignment)
MAIN_LIFT_NAMES = set(config.MAIN_LIFTS.keys())


def _normalise(name: str) -> str:
    """Lowercase, strip punctuation and extra spaces — for fuzzy name matching."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def migrate(reseed: bool = False) -> None:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row

    # ── Create tables ─────────────────────────────────────────────────────
    con.executescript("""
        CREATE TABLE IF NOT EXISTS exercise_roster (
            exercise_name    TEXT PRIMARY KEY,
            session_type     TEXT NOT NULL,
            is_main_lift     INTEGER NOT NULL DEFAULT 0,
            target_freq_days REAL    NOT NULL DEFAULT 7,
            star_rating      INTEGER NOT NULL DEFAULT 3,
            active           INTEGER NOT NULL DEFAULT 1,
            notes            TEXT
        );

        CREATE TABLE IF NOT EXISTS hevy_exercise_library (
            hevy_id          TEXT PRIMARY KEY,
            title            TEXT NOT NULL,
            primary_muscle   TEXT,
            equipment        TEXT,
            exercise_type    TEXT,
            is_custom        INTEGER DEFAULT 0,
            session_type     TEXT
        );

        CREATE TABLE IF NOT EXISTS blocks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            start_date DATE,
            end_date   DATE,
            status     TEXT NOT NULL DEFAULT 'planned',
            notes      TEXT
        );

        CREATE TABLE IF NOT EXISTS block_overrides (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id         INTEGER NOT NULL REFERENCES blocks(id),
            session_type     TEXT,
            suspend_exercise TEXT,
            add_exercise     TEXT,
            priority_bump    REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS prescribed_sessions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id       INTEGER REFERENCES blocks(id),
            date           DATE NOT NULL,
            session_type   TEXT NOT NULL,
            exercises_json TEXT NOT NULL,
            reasoning      TEXT,
            posted_to_hevy INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Add star_rating column if it was missing from a previous schema version
    try:
        con.execute("ALTER TABLE exercise_roster ADD COLUMN star_rating INTEGER NOT NULL DEFAULT 3")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    print("Tables created / verified.")

    # ── Fetch and store Hevy exercise library ─────────────────────────────
    con.execute("DELETE FROM hevy_exercise_library")
    try:
        from hevy import get_all_templates
        templates = get_all_templates(pages=5)
    except Exception as e:
        print(f"Warning: could not fetch Hevy templates ({e}). Skipping library sync.")
        templates = []

    for t in templates:
        muscle = t.get("primary_muscle_group", "")
        session_type = MUSCLE_TO_SESSION.get(muscle)
        con.execute("""
            INSERT OR REPLACE INTO hevy_exercise_library
                (hevy_id, title, primary_muscle, equipment, exercise_type, is_custom, session_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            t["id"], t["title"],
            muscle, t.get("equipment"), t.get("type"),
            int(t.get("is_custom", False)),
            session_type,
        ))
    con.commit()
    classified = sum(1 for t in templates if MUSCLE_TO_SESSION.get(t.get("primary_muscle_group","")))
    print(f"Hevy library: {len(templates)} total, {classified} classifiable exercises stored.")

    # ── Seed exercise_roster ──────────────────────────────────────────────
    existing = con.execute("SELECT COUNT(*) FROM exercise_roster").fetchone()[0]
    if existing > 0 and not reseed:
        print(f"exercise_roster already has {existing} rows — skipping seed (use --reseed to rebuild).")
        con.close()
        return
    if reseed and existing > 0:
        con.execute("DELETE FROM exercise_roster")
        print(f"Cleared {existing} existing roster rows.")
        con.commit()

    # Build lookup: normalised_name → (session_count, is_main_lift, session_type, avg_freq)
    history_rows = con.execute("""
        SELECT exercise,
               MAX(is_main_lift)       AS is_main_lift,
               COUNT(DISTINCT date)    AS session_count
        FROM sets
        WHERE is_warmup != 1
        GROUP BY exercise
    """).fetchall()

    history: dict[str, dict] = {}
    for row in history_rows:
        name = row["exercise"]
        n = row["session_count"]

        st_row = con.execute("""
            SELECT session_type, COUNT(DISTINCT date) AS cnt
            FROM sets
            WHERE exercise = ? AND session_type != 'unknown' AND is_warmup != 1
            GROUP BY session_type ORDER BY cnt DESC LIMIT 1
        """, (name,)).fetchone()

        dates = [r[0] for r in con.execute("""
            SELECT DISTINCT date FROM sets WHERE exercise = ? AND is_warmup != 1 ORDER BY date
        """, (name,)).fetchall()]

        if len(dates) >= 2:
            gaps = [(_date.fromisoformat(dates[i]) - _date.fromisoformat(dates[i-1])).days
                    for i in range(1, len(dates))]
            freq = round(sum(gaps) / len(gaps), 1)
            freq = max(2.0, min(freq, 21.0))
        else:
            freq = None  # will fall back to star-based default

        history[_normalise(name)] = {
            "canonical_name": name,
            "is_main_lift":   int(row["is_main_lift"] or 0),
            "session_count":  n,
            "session_type":   st_row["session_type"] if st_row else None,
            "avg_freq":       freq,
        }

    def _star_from_count(name: str, n: int) -> int:
        if name in MAIN_LIFT_NAMES:
            return 5
        for threshold, star in sorted(STAR_THRESHOLDS.items(), reverse=True):
            if n >= threshold:
                return star
        return DEFAULT_STAR

    inserted = 0
    skipped  = 0

    # ── Pass 1: exercises in Hevy library (primary source of truth) ───────
    hevy_exercises = con.execute("""
        SELECT title, session_type FROM hevy_exercise_library WHERE session_type IS NOT NULL
    """).fetchall()

    for ex in hevy_exercises:
        title        = ex["title"]
        session_type = ex["session_type"]
        norm         = _normalise(title)
        hist         = history.get(norm)

        if hist:
            n          = hist["session_count"]
            star       = _star_from_count(hist["canonical_name"], n)
            freq       = hist["avg_freq"] or STAR_FREQ_DAYS[star]
            is_main    = hist["is_main_lift"]
            # Prefer session_type from history (more accurate than muscle group)
            stype      = hist["session_type"] or session_type
        else:
            n          = 0
            star       = DEFAULT_STAR
            freq       = STAR_FREQ_DAYS[star]
            is_main    = 0
            stype      = session_type

        con.execute("""
            INSERT OR IGNORE INTO exercise_roster
                (exercise_name, session_type, is_main_lift, target_freq_days, star_rating, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (title, stype, is_main, freq, star))
        inserted += 1

    # ── Pass 2: historical exercises not matched to Hevy library ─────────
    hevy_norms = {_normalise(ex["title"]) for ex in hevy_exercises}
    for norm, hist in history.items():
        if norm in hevy_norms:
            continue  # already handled above
        name  = hist["canonical_name"]
        n     = hist["session_count"]
        stype = hist["session_type"]
        if not stype:
            skipped += 1
            continue
        star = _star_from_count(name, n)
        freq = hist["avg_freq"] or STAR_FREQ_DAYS[star]
        con.execute("""
            INSERT OR IGNORE INTO exercise_roster
                (exercise_name, session_type, is_main_lift, target_freq_days, star_rating, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (name, stype, hist["is_main_lift"], freq, star))
        inserted += 1

    con.commit()
    con.close()
    print(f"Seeded {inserted} exercises into roster ({skipped} skipped — no session type).")
    print()
    print("To adjust a star rating:")
    print("  sqlite3 gym.db \"UPDATE exercise_roster SET star_rating=4 WHERE exercise_name='Cable Row';\"")


if __name__ == "__main__":
    migrate(reseed="--reseed" in sys.argv)
