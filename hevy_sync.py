"""
hevy_sync.py — Syncs completed Hevy workouts into the sets table.

Called from run.py alongside the Withings sync. Fetches workouts from the
last N days and writes new sets to the DB. Already-synced sessions (matched
by Hevy workout ID) are skipped, so re-running is safe.

Run standalone to backfill:
    python hevy_sync.py           # last 14 days
    python hevy_sync.py --days 90 # backfill further
"""
import sys
import sqlite3
import requests
from datetime import date, datetime, timedelta
from typing import Optional
import config

BASE_URL = "https://api.hevyapp.com/v1"

# Hevy title → canonical name. Derived from exercises.json — edit that file, not this.
import exercise_lib as _elib
HEVY_ALIASES: dict[str, str] = {
    ex["hevy_title"]: ex["canonical"]
    for ex in _elib.all_exercises().values()
    if ex["hevy_title"] != ex["canonical"]
}

MUSCLE_TO_SESSION = {
    "chest": "push", "shoulders": "push", "triceps": "push",
    "biceps": "arms", "forearms": "arms",
    "lats": "pull", "upper_back": "pull", "traps": "pull", "lower_back": "pull",
    "quadriceps": "legs", "hamstrings": "legs", "glutes": "legs",
    "calves": "legs", "abductors": "legs", "adductors": "legs",
}

MAIN_LIFT_NAMES = set(config.MAIN_LIFTS.keys())

# Hevy stores unweighted Pull Up / Chin Up / Chest Dip as exercise_type="reps_only"
# (same bucket as Plank or Hanging Leg Raise), so we can't infer "loads bodyweight"
# from the type alone. This list covers the reps_only exercises that do.
REPS_ONLY_BODYWEIGHT_LIFTS = {
    "Pull Up", "Chin Up", "Chest Dip", "Push Up", "Inverted Row",
    "Muscle Up", "Pull Up (Assisted)", "Chin Up (Assisted)",
}


def _headers() -> dict:
    return {"api-key": config.HEVY_API_KEY, "Content-Type": "application/json"}


def _epley(weight: float, reps: int, bodyweight: float = 0.0) -> Optional[float]:
    total = float(weight) + float(bodyweight)
    if total <= 0:
        return None
    if reps == 1:
        return total
    return total * (1 + reps / 30)


def _bodyweight_lookup(con: sqlite3.Connection) -> list[tuple[str, float]]:
    """Return [(date, weight_kg), ...] sorted newest-first for is_bodyweight set e1RM."""
    return [(r["date"], float(r["weight_kg"]))
            for r in con.execute("SELECT date, weight_kg FROM bodyweight ORDER BY date DESC")]


def _bw_on_or_before(bw_readings: list[tuple[str, float]], target_date: str) -> float:
    """Find the bodyweight reading on or before target_date (sorted newest-first). 0 if none."""
    for d, w in bw_readings:
        if d <= target_date:
            return w
    return 0.0


def _backfill_bodyweight_e1rm(con: sqlite3.Connection) -> int:
    """One-time/idempotent: flag known bodyweight lifts and recompute their e1RM."""
    # Flag historical Pull Up / Chin Up / Dip rows that were mis-classified as not-bodyweight.
    bw_names = REPS_ONLY_BODYWEIGHT_LIFTS | {"Weighted Dip"}
    placeholders = ",".join("?" * len(bw_names))
    con.execute(
        f"UPDATE sets SET is_bodyweight = 1, e1rm = NULL "
        f"WHERE is_bodyweight = 0 AND exercise IN ({placeholders})",
        tuple(bw_names),
    )

    bw_readings = _bodyweight_lookup(con)
    if not bw_readings:
        return 0
    rows = con.execute(
        "SELECT id, date, weight_kg, reps FROM sets WHERE is_bodyweight = 1 AND e1rm IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        bw = _bw_on_or_before(bw_readings, row["date"])
        e1rm = _epley(float(row["weight_kg"] or 0), int(row["reps"] or 0), bw)
        if e1rm is not None:
            con.execute("UPDATE sets SET e1rm = ? WHERE id = ?", (e1rm, row["id"]))
            updated += 1
    if updated:
        con.commit()
        print(f"[hevy_sync] Backfilled e1RM for {updated} bodyweight sets.")
    return updated


def fetch_workouts(since: str) -> list[dict]:
    """
    Fetch all workouts with start_time >= since (ISO date string).
    Hevy returns newest-first; stops fetching once it passes the cutoff.
    """
    workouts = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/workouts",
            headers=_headers(),
            params={"page": page, "pageSize": 10},
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data  = resp.json()
        batch = data.get("workouts", [])
        if not batch:
            break
        for w in batch:
            workout_date = (w.get("start_time") or "")[:10]
            if workout_date < since:
                return workouts
            workouts.append(w)
        if page >= data.get("page_count", 1):
            break
        page += 1
    return workouts


def sync_to_db(days: int = 14) -> int:
    """
    Fetch Hevy workouts from the last N days and upsert into the sets table.
    Returns the number of new sets written.
    """
    since    = (date.today() - timedelta(days=days)).isoformat()
    workouts = fetch_workouts(since)

    if not workouts:
        print(f"[hevy_sync] No workouts found since {since}.")
        return 0

    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row

    _backfill_bodyweight_e1rm(con)
    bw_readings = _bodyweight_lookup(con)

    # exercise_template_id → {primary_muscle, exercise_type} from cached library
    lib: dict[str, dict] = {
        row["hevy_id"]: {
            "muscle": row["primary_muscle"] or "",
            "type":   row["exercise_type"]  or "",
        }
        for row in con.execute(
            "SELECT hevy_id, primary_muscle, exercise_type FROM hevy_exercise_library"
        ).fetchall()
    }

    # exercise name → is_main_lift from roster
    main_lifts_in_roster: set[str] = {
        row["exercise_name"]
        for row in con.execute(
            "SELECT exercise_name FROM exercise_roster WHERE is_main_lift = 1"
        ).fetchall()
    }
    main_lifts_in_roster.update(MAIN_LIFT_NAMES)

    total_sets = 0
    new_sessions = 0

    for workout in workouts:
        workout_id   = workout["id"]
        session_id   = f"hevy_{workout_id.replace('-', '')[:12]}"
        workout_date = (workout.get("start_time") or "")[:10]
        workout_name = workout.get("title") or "Hevy Workout"
        exercises    = workout.get("exercises") or []

        # For already-synced sessions: still check for new notes
        already_synced = con.execute(
            "SELECT 1 FROM sets WHERE session_id = ? LIMIT 1", (session_id,)
        ).fetchone()
        if already_synced:
            _store_hevy_notes(con, workout_date, workout, exercises)
            continue

        # Determine session_type: title keywords first, fall back to muscle-group vote
        def _parse_title(name: str) -> str:
            n = name.lower()
            if any(x in n for x in ["push", "chest"]):        return "push"
            if any(x in n for x in ["pull", "back", "bicep"]): return "pull"
            if "leg" in n:                                      return "legs"
            if "arm" in n:                                      return "arms"
            return "unknown"

        session_type = _parse_title(workout_name)
        if session_type == "unknown":
            type_votes: dict[str, int] = {}
            for ex in exercises:
                tid    = ex.get("exercise_template_id", "")
                muscle = lib.get(tid, {}).get("muscle", "")
                stype  = MUSCLE_TO_SESSION.get(muscle)
                if stype:
                    type_votes[stype] = type_votes.get(stype, 0) + len(ex.get("sets") or [])
            session_type = max(type_votes, key=type_votes.get) if type_votes else "unknown"

        set_number = 0
        for ex in exercises:
            tid       = ex.get("exercise_template_id", "")
            raw_name  = ex.get("title") or tid
            ex_name   = HEVY_ALIASES.get(raw_name, raw_name)
            ex_info   = lib.get(tid, {})
            muscle    = ex_info.get("muscle", "")
            ex_type   = ex_info.get("type", "")

            is_main     = 1 if ex_name in main_lifts_in_roster else 0
            is_bodyweight = 1 if (
                ex_type in ("body_weight", "bodyweight_weighted")
                or (ex_type == "reps_only" and ex_name in REPS_ONLY_BODYWEIGHT_LIFTS)
            ) else 0

            for s in (ex.get("sets") or []):
                reps      = int(s.get("reps") or 0)
                if reps == 0:
                    continue
                weight_kg = float(s.get("weight_kg") or 0)
                is_warmup = 1 if s.get("type") == "warmup" else 0
                bw_for_set = _bw_on_or_before(bw_readings, workout_date) if is_bodyweight else 0.0
                e1rm      = _epley(weight_kg, reps, bw_for_set)
                rpe       = s.get("rpe")   # None if not recorded

                con.execute("""
                    INSERT OR IGNORE INTO sets
                        (source, session_id, date, workout_name, session_type,
                         muscle_group, exercise, is_main_lift, is_bodyweight,
                         is_warmup, set_number, weight_kg, reps, e1rm, rpe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "hevy", session_id, workout_date, workout_name, session_type,
                    MUSCLE_TO_SESSION.get(muscle, "other"), ex_name,
                    is_main, is_bodyweight,
                    is_warmup, set_number, weight_kg, reps, e1rm, rpe,
                ))
                set_number += 1
                total_sets += 1

        # ── Extract and store workout / exercise notes ─────────────────────
        _store_hevy_notes(con, workout_date, workout, exercises)

        new_sessions += 1

    con.commit()
    con.close()
    print(f"[hevy_sync] {new_sessions} new sessions, {total_sets} sets written to DB.")
    return total_sets


def _store_hevy_notes(con: sqlite3.Connection, workout_date: str, workout: dict, exercises: list) -> None:
    """
    Extract any notes from the completed Hevy workout and store in session_notes.
    Captures both the workout-level description and per-exercise notes.
    Skips empty notes and avoids duplicates (same date + note text).
    """
    def _insert(date: str, note: str, source: str) -> None:
        note = note.strip()
        if not note:
            return
        existing = con.execute(
            "SELECT 1 FROM session_notes WHERE date = ? AND note = ?", (date, note)
        ).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO session_notes (date, note, source) VALUES (?, ?, ?)",
                (date, note, source),
            )
            print(f"[hevy_sync] Note ({source}): {note}")

    # Workout-level description (user's overall session note)
    description = (workout.get("description") or "").strip()
    if description:
        _insert(workout_date, description, "hevy_workout")

    # Per-exercise notes
    for ex in exercises:
        ex_note = (ex.get("notes") or "").strip()
        if ex_note:
            raw_name = ex.get("title") or ex.get("exercise_template_id", "")
            ex_name  = HEVY_ALIASES.get(raw_name, raw_name)
            tag = ex_note.upper().split(":")[0].strip()
            source = {"NOTE": "user_directive", "DEBUG": "debug_request"}.get(tag, "hevy_exercise")
            _insert(workout_date, f"{ex_name}: {ex_note}", source)


if __name__ == "__main__":
    days = 14
    for arg in sys.argv[1:]:
        if arg.startswith("--days"):
            days = int(arg.split("=")[-1]) if "=" in arg else int(sys.argv[sys.argv.index(arg) + 1])
    sync_to_db(days=days)
