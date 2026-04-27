"""
migrate.py — Create block/roster tables and seed exercise_roster from history.

Run once (idempotent):
    python migrate.py

To wipe and reseed the roster (e.g. after changing thresholds):
    python migrate.py --reseed
"""
import sys
import sqlite3
from datetime import date as _date, timedelta
import config

# Only exercises meeting both criteria enter the roster.
# Raise these if you want a tighter list; lower to include more obscure movements.
MIN_SESSIONS  = 5      # must have been done at least this many distinct session-days
MAX_STALE_DAYS = 365   # must have been trained within this many days


def migrate(reseed: bool = False) -> None:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row

    con.executescript("""
        CREATE TABLE IF NOT EXISTS exercise_roster (
            exercise_name    TEXT PRIMARY KEY,
            session_type     TEXT NOT NULL,
            is_main_lift     INTEGER NOT NULL DEFAULT 0,
            target_freq_days REAL    NOT NULL DEFAULT 7,
            active           INTEGER NOT NULL DEFAULT 1,
            notes            TEXT
        );

        CREATE TABLE IF NOT EXISTS blocks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            start_date DATE,
            end_date   DATE,
            status     TEXT NOT NULL DEFAULT 'planned',
            notes      TEXT
        );

        -- Each row is one override action for a block.
        -- suspend_exercise: remove this exercise from the session roster.
        -- add_exercise:     add this exercise (even if not in base roster).
        -- priority_bump:    add this value to add_exercise's computed priority.
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
    print("Tables created / verified.")

    # ── Seed exercise_roster ──────────────────────────────────────────────────
    existing = con.execute("SELECT COUNT(*) FROM exercise_roster").fetchone()[0]
    if existing > 0 and not reseed:
        print(f"exercise_roster already has {existing} rows — skipping seed (use --reseed to rebuild).")
        con.close()
        return
    if reseed and existing > 0:
        con.execute("DELETE FROM exercise_roster")
        print(f"Cleared {existing} existing roster rows.")

    cutoff = (_date.today() - timedelta(days=MAX_STALE_DAYS)).isoformat()

    # Only exercises trained frequently enough and recently enough.
    ex_rows = con.execute("""
        SELECT exercise,
               MAX(is_main_lift) AS is_main_lift,
               COUNT(DISTINCT date) AS session_count,
               MAX(date) AS last_date
        FROM sets
        WHERE session_type != 'unknown'
          AND is_warmup != 1
        GROUP BY exercise
        HAVING session_count >= ?
           AND last_date >= ?
        ORDER BY session_count DESC
    """, (MIN_SESSIONS, cutoff)).fetchall()

    inserted = 0
    for ex_row in ex_rows:
        exercise = ex_row["exercise"]
        is_main  = int(ex_row["is_main_lift"] or 0)

        # Most common session_type for this exercise
        st_row = con.execute("""
            SELECT session_type, COUNT(DISTINCT date) AS n
            FROM sets
            WHERE exercise = ?
              AND session_type != 'unknown'
              AND is_warmup != 1
            GROUP BY session_type
            ORDER BY n DESC LIMIT 1
        """, (exercise,)).fetchone()
        if not st_row:
            continue
        session_type = st_row["session_type"]

        # Compute average inter-session gap for target_freq_days
        dates = [r[0] for r in con.execute("""
            SELECT DISTINCT date FROM sets
            WHERE exercise = ? AND is_warmup != 1
            ORDER BY date
        """, (exercise,)).fetchall()]

        if len(dates) < 2:
            freq = 7.0
        else:
            gaps = []
            for i in range(1, len(dates)):
                d1 = _date.fromisoformat(dates[i - 1])
                d2 = _date.fromisoformat(dates[i])
                gaps.append((d2 - d1).days)
            freq = round(sum(gaps) / len(gaps), 1)
            freq = max(2.0, min(freq, 21.0))   # clamp to sane range

        con.execute("""
            INSERT OR IGNORE INTO exercise_roster
                (exercise_name, session_type, is_main_lift, target_freq_days, active)
            VALUES (?, ?, ?, ?, 1)
        """, (exercise, session_type, is_main, freq))
        inserted += 1

    con.commit()
    con.close()
    print(f"Seeded {inserted} exercises into exercise_roster.")


if __name__ == "__main__":
    migrate(reseed="--reseed" in sys.argv)
