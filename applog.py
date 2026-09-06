"""
applog.py — writes in-app-logged workouts into the sets table.

The app's own logger sourced here (source='app') is the drop-in alternative to
hevy_sync's Hevy-API path. Everything downstream (context, stats, e1RM,
feedback, session forecasting) reads the sets table, so an app-logged session is
indistinguishable from a Hevy-synced one to the engine — no engine changes.

Column set and e1RM/bodyweight/main-lift logic mirror hevy_sync exactly so the
two write paths stay byte-compatible in the table.
"""
import sqlite3
from datetime import date as _date

import config
import exercise_lib as _elib
from hevy_sync import (
    _epley, _bodyweight_lookup, _bw_on_or_before,
    MUSCLE_TO_SESSION, REPS_ONLY_BODYWEIGHT_LIFTS,
)


def _meta(name: str) -> dict:
    """exercise_lib metadata for a name, or {} if unknown."""
    tid = _elib.resolve_id(name)
    return _elib.all_exercises().get(tid, {}) if tid else {}


def _is_bodyweight(name: str, meta: dict) -> bool:
    et = meta.get("exercise_type", "") or ""
    if "bodyweight" in et:
        return True
    if meta.get("equipment") == "none":
        return True
    return name in REPS_ONLY_BODYWEIGHT_LIFTS


def _session_id(d: str, session_type: str) -> str:
    return f"app_{d.replace('-', '')}_{session_type}"


def log_session(db_path: str, payload: dict) -> dict:
    """Write a logged session into the sets table (source='app').

    Idempotent per (date, session_type): any prior source='app' session with the
    same session_id is deleted first, so re-saving edits rather than duplicates.
    Returns {session_id, sets_written, date, session_type}.
    """
    d = (payload.get("date") or _date.today().isoformat())[:10]
    session_type = ((payload.get("session_type") or "unknown").strip() or "unknown")
    workout_name = ((payload.get("workout_name") or f"{session_type.title()} (logged)").strip())
    exercises = payload.get("exercises") or []
    session_id = _session_id(d, session_type)

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        bw_readings = _bodyweight_lookup(con)
        main_lifts = {
            r["exercise_name"]
            for r in con.execute(
                "SELECT exercise_name FROM exercise_roster WHERE is_main_lift = 1"
            ).fetchall()
        }
        main_lifts.update(getattr(config, "MAIN_LIFTS", {}).keys())

        # Idempotent re-write: drop the prior app log for this (date, session_type).
        con.execute(
            "DELETE FROM sets WHERE session_id = ? AND source = 'app'", (session_id,)
        )

        total = 0
        set_number = 0
        for ex in exercises:
            name = (ex.get("exercise_name") or "").strip()
            if not name:
                continue
            meta = _meta(name)
            muscle = meta.get("muscle", "")
            is_bw = 1 if _is_bodyweight(name, meta) else 0
            is_main = 1 if name in main_lifts else 0

            for s in (ex.get("sets") or []):
                reps = int(s.get("reps") or 0)
                if reps <= 0:
                    continue
                weight_kg = float(s.get("weight_kg") or 0)
                is_warmup = 1 if s.get("is_warmup") else 0
                rpe_raw = s.get("rpe")
                rpe = float(rpe_raw) if rpe_raw not in (None, "") else None
                bw_for_set = _bw_on_or_before(bw_readings, d) if is_bw else 0.0
                e1rm = _epley(weight_kg, reps, bw_for_set)

                con.execute("""
                    INSERT INTO sets
                        (source, session_id, date, workout_name, session_type,
                         muscle_group, exercise, is_main_lift, is_bodyweight,
                         is_warmup, set_number, weight_kg, reps, e1rm, rpe)
                    VALUES ('app', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id, d, workout_name, session_type,
                    MUSCLE_TO_SESSION.get(muscle, "other"), name,
                    is_main, is_bw, is_warmup, set_number, weight_kg, reps, e1rm, rpe,
                ))
                set_number += 1
                total += 1

        con.commit()
        return {
            "session_id": session_id,
            "sets_written": total,
            "date": d,
            "session_type": session_type,
        }
    finally:
        con.close()


def logged_session(db_path: str, d: str | None = None) -> dict | None:
    """Return the app-logged session for a date (default today), grouped by
    exercise, so the editor can re-open it. None if nothing app-logged that day."""
    d = (d or _date.today().isoformat())[:10]
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT exercise, set_number, weight_kg, reps, rpe, is_warmup "
            "FROM sets WHERE date = ? AND source = 'app' ORDER BY set_number",
            (d,),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None

    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["exercise"], []).append({
            "weight_kg": r["weight_kg"],
            "reps": r["reps"],
            "rpe": r["rpe"],
            "is_warmup": bool(r["is_warmup"]),
        })
    return {
        "date": d,
        "exercises": [{"exercise_name": k, "sets": v} for k, v in grouped.items()],
    }
