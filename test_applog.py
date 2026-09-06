"""
test_applog.py — functional test for the in-app logger's write path.

Runs applog.log_session against a throwaway SQLite DB with the real sets/
bodyweight/exercise_roster schema, and asserts the rows, e1RM, flags and
idempotent re-write. No Hevy, no network, no live user DB touched.

    python test_applog.py
"""
import sqlite3
import tempfile
import os

import applog


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, session_id TEXT,
            date DATE, workout_name TEXT, session_type TEXT, muscle_group TEXT,
            exercise TEXT, is_main_lift INTEGER, is_bodyweight INTEGER,
            is_warmup INTEGER DEFAULT 0, set_number INTEGER, weight_kg REAL,
            reps INTEGER, e1rm REAL, notes TEXT, rpe REAL
        );
        CREATE TABLE bodyweight (date DATE, weight_kg REAL, muscle_mass_kg REAL, body_fat_pct REAL);
        CREATE TABLE exercise_roster (exercise_name TEXT, is_main_lift INTEGER);
    """)
    # Bench Press is a main lift per the roster; bodyweight reading for the Pull Up e1RM.
    con.execute("INSERT INTO exercise_roster VALUES ('Barbell Bench Press', 1)")
    con.execute("INSERT INTO bodyweight (date, weight_kg) VALUES ('2026-09-01', 80.0)")
    con.commit()
    con.close()
    return path


def _rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM sets ORDER BY set_number").fetchall()
    con.close()
    return r


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok:", msg)


def main():
    path = _make_db()
    try:
        payload = {
            "date": "2026-09-06",
            "session_type": "push",
            "workout_name": "Push A",
            "exercises": [
                {"exercise_name": "Barbell Bench Press", "sets": [
                    {"weight_kg": 60, "reps": 8, "rpe": 7, "is_warmup": True},
                    {"weight_kg": 80, "reps": 5, "rpe": 8, "is_warmup": False},
                    {"weight_kg": 80, "reps": 5, "rpe": 9, "is_warmup": False},
                ]},
                {"exercise_name": "Pull Up", "sets": [
                    {"weight_kg": 0, "reps": 10, "rpe": 8, "is_warmup": False},
                ]},
                {"exercise_name": "", "sets": [{"weight_kg": 1, "reps": 1}]},   # skipped: no name
                {"exercise_name": "Cable Fly", "sets": [
                    {"weight_kg": 15, "reps": 0},   # skipped: 0 reps
                ]},
            ],
        }
        res = applog.log_session(path, payload)
        print("result:", res)
        _check(res["sets_written"] == 4, "4 valid sets written (2 empty/zero-rep dropped)")
        _check(res["session_id"] == "app_20260906_push", "deterministic session_id")

        rows = _rows(path)
        _check(len(rows) == 4, "table has 4 rows")
        _check(all(r["source"] == "app" for r in rows), "all rows tagged source='app'")

        bench = [r for r in rows if r["exercise"] == "Barbell Bench Press"]
        _check(all(r["is_main_lift"] == 1 for r in bench), "Bench Press flagged main lift (from roster)")
        _check(bench[0]["is_warmup"] == 1 and bench[1]["is_warmup"] == 0, "warmup flag preserved")
        # Epley on the top working set: 80 * (1 + 5/30) = 93.33
        _check(abs(bench[1]["e1rm"] - 80 * (1 + 5 / 30)) < 0.01, "weighted e1RM via Epley")
        _check(bench[0]["muscle_group"] == "push", "muscle_group bucketed to session (chest→push)")

        pull = [r for r in rows if r["exercise"] == "Pull Up"][0]
        _check(pull["is_bodyweight"] == 1, "Pull Up flagged bodyweight")
        # Bodyweight e1RM folds in the 80kg reading: (0+80) * (1 + 10/30) = 106.67
        _check(abs(pull["e1rm"] - 80 * (1 + 10 / 30)) < 0.01, "bodyweight e1RM folds in bodyweight")
        _check(pull["rpe"] == 8.0, "rpe stored")

        # Idempotent re-write: same (date, session_type) replaces, not appends.
        payload["exercises"] = [{"exercise_name": "Barbell Bench Press", "sets": [
            {"weight_kg": 85, "reps": 5, "is_warmup": False}]}]
        res2 = applog.log_session(path, payload)
        rows2 = _rows(path)
        _check(res2["sets_written"] == 1 and len(rows2) == 1, "re-save replaced the session (no dupes)")
        _check(rows2[0]["weight_kg"] == 85, "re-save persisted the edited weight")

        # Read-back for the editor.
        logged = applog.logged_session(path, "2026-09-06")
        _check(logged is not None and logged["exercises"][0]["exercise_name"] == "Barbell Bench Press",
               "logged_session reads the saved session back")
        _check(applog.logged_session(path, "2020-01-01") is None, "empty date returns None")

        print("\nALL PASSED")
    finally:
        os.remove(path)


if __name__ == "__main__":
    main()
