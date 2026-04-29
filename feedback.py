"""
feedback.py — Two feedback signals:

1. Template diff: before overwriting the Hevy routine, GET the current content and
   diff it against the prescription. Captures edits made in the Hevy app before the session.

2. Completed-workout diff: after hevy_sync, diff the prescribed exercises against
   what was actually logged. Captures in-session changes.

Both are stored in workout_feedback and surfaced to Claude.
"""
import json
import sqlite3
from datetime import date, timedelta
from typing import Optional
import config


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _actual_sets(session_date: str) -> dict[str, dict]:
    """
    Returns {exercise_name: {top_weight_kg, avg_reps, set_count}} for a date.
    Only working sets (non-warmup).
    """
    con = _con()
    rows = con.execute("""
        SELECT exercise,
               MAX(weight_kg)       AS top_weight,
               ROUND(AVG(reps), 1)  AS avg_reps,
               COUNT(*)             AS set_count
        FROM sets
        WHERE date = ? AND is_warmup = 0
        GROUP BY exercise
    """, (session_date,)).fetchall()
    con.close()
    return {r["exercise"]: dict(r) for r in rows}


def compute_diff(prescription: dict, actual: dict[str, dict]) -> dict:
    """
    Compares a prescription dict (from prescribed_sessions) against actual sets.

    Returns a diff dict with:
      - skipped:  exercises prescribed but not logged
      - added:    exercises logged but not prescribed
      - weight_adjustments: same exercise, weight changed by >5%
      - reps_adjustments:   same exercise, reps changed by >1
    """
    prescribed_exercises = {
        ex["exercise_name"]: ex
        for ex in prescription.get("exercises", [])
    }

    skipped = []
    weight_adjustments = []
    reps_adjustments = []

    for name, pex in prescribed_exercises.items():
        if name not in actual:
            skipped.append(name)
            continue

        aex = actual[name]
        p_sets   = [s for s in pex.get("sets", []) if not s.get("is_warmup")]
        p_weight = max((s["weight_kg"] for s in p_sets), default=0)
        p_reps   = round(sum(s["reps"] for s in p_sets) / len(p_sets), 1) if p_sets else 0

        a_weight = aex["top_weight"]
        a_reps   = aex["avg_reps"]

        if p_weight > 0 and abs(a_weight - p_weight) / p_weight > 0.05:
            weight_adjustments.append({
                "exercise":     name,
                "prescribed_kg": p_weight,
                "actual_kg":    a_weight,
                "delta_pct":    round((a_weight - p_weight) / p_weight * 100, 1),
            })

        if p_reps > 0 and abs(a_reps - p_reps) > 1:
            reps_adjustments.append({
                "exercise":       name,
                "prescribed_reps": p_reps,
                "actual_reps":    a_reps,
            })

    prescribed_names = set(prescribed_exercises.keys())
    added = [name for name in actual if name not in prescribed_names]

    return {
        "skipped":            skipped,
        "added":              added,
        "weight_adjustments": weight_adjustments,
        "reps_adjustments":   reps_adjustments,
    }


def store_diff(session_date: str, prescription_id: int, session_type: str, diff: dict) -> None:
    con = _con()
    # Upsert — replace if we re-run for the same date
    con.execute("""
        INSERT OR REPLACE INTO workout_feedback
            (date, prescription_id, session_type, diff_json)
        VALUES (?, ?, ?, ?)
    """, (session_date, prescription_id, session_type, json.dumps(diff)))
    con.commit()
    con.close()


def diff_is_empty(diff: dict) -> bool:
    return not any([
        diff.get("skipped"),
        diff.get("added"),
        diff.get("weight_adjustments"),
        diff.get("reps_adjustments"),
    ])


def run_feedback_for_date(session_date: str) -> Optional[dict]:
    """
    Find the prescription for session_date, pull actual sets, compute and store diff.
    Returns the diff dict, or None if no prescription found.
    """
    con = _con()
    row = con.execute("""
        SELECT id, session_type, exercises_json
        FROM prescribed_sessions
        WHERE date = ?
        ORDER BY id DESC LIMIT 1
    """, (session_date,)).fetchone()
    con.close()

    if not row:
        return None

    prescription_id = row["id"]
    session_type    = row["session_type"]
    prescription    = {"exercises": json.loads(row["exercises_json"])}

    actual = _actual_sets(session_date)
    if not actual:
        return None

    diff = compute_diff(prescription, actual)
    if not diff_is_empty(diff):
        store_diff(session_date, prescription_id, session_type, diff)
        _print_diff(session_date, session_type, diff)

    return diff


def run_feedback_recent(days: int = 14) -> int:
    """Backfill diffs for the last N days that have both a prescription and actual sets."""
    count = 0
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        diff = run_feedback_for_date(d)
        if diff and not diff_is_empty(diff):
            count += 1
    return count


def _print_diff(session_date: str, session_type: str, diff: dict) -> None:
    print(f"[feedback] {session_date} ({session_type}):")
    for name in diff.get("skipped", []):
        print(f"  skipped:  {name}")
    for name in diff.get("added", []):
        print(f"  added:    {name}")
    for w in diff.get("weight_adjustments", []):
        sign = "+" if w["delta_pct"] > 0 else ""
        print(f"  weight:   {w['exercise']} {w['prescribed_kg']}→{w['actual_kg']}kg ({sign}{w['delta_pct']}%)")
    for r in diff.get("reps_adjustments", []):
        print(f"  reps:     {r['exercise']} {r['prescribed_reps']}→{r['actual_reps']} reps")


def diff_hevy_template_vs_prescription(routine_id: str, today_iso: str) -> Optional[dict]:
    """
    GET the current Hevy routine content and diff it against the most recent
    prescription for today. Captures edits the user made in the Hevy app
    before starting the session. Stores result in workout_feedback.
    Returns the diff, or None if no changes or no prescription found.
    """
    import requests
    headers = {"api-key": config.HEVY_API_KEY, "Content-Type": "application/json"}
    resp = requests.get(f"https://api.hevyapp.com/v1/routines/{routine_id}", headers=headers)
    if not resp.ok:
        return None

    data     = resp.json()
    routines = data.get("routine", data)
    routine  = routines[0] if isinstance(routines, list) and routines else routines
    hevy_exercises = routine.get("exercises") or []

    con = _con()
    # Compare against the last prescription that was actually pushed to Hevy —
    # that's what the routine currently contains (before any user edits).
    row = con.execute("""
        SELECT id, session_type, exercises_json FROM prescribed_sessions
        WHERE posted_to_hevy = 1
        ORDER BY date DESC, id DESC LIMIT 1
    """).fetchone()
    con.close()

    if not row:
        return None

    # Build actual-like dict from the Hevy template exercises
    from hevy_sync import HEVY_ALIASES
    template_actual: dict[str, dict] = {}
    for ex in hevy_exercises:
        raw_name = ex.get("title") or ex.get("exercise_template_id", "")
        name = HEVY_ALIASES.get(raw_name, raw_name)
        sets = [s for s in (ex.get("sets") or []) if s.get("type") != "warmup"]
        if not sets:
            continue
        weights  = [float(s.get("weight_kg") or 0) for s in sets]
        reps_all = [int(s.get("reps") or 0) for s in sets]
        template_actual[name] = {  # keyed by canonical name
            "top_weight": max(weights),
            "avg_reps":   round(sum(reps_all) / len(reps_all), 1) if reps_all else 0,
            "set_count":  len(sets),
        }

    prescription = {"exercises": json.loads(row["exercises_json"])}
    diff = compute_diff(prescription, template_actual)

    if not diff_is_empty(diff):
        store_diff(today_iso, row["id"], row["session_type"], diff)
        print(f"[feedback] Template edits detected:")
        _print_diff(today_iso, row["session_type"], diff)

    return diff if not diff_is_empty(diff) else None


# ── Session notes ──────────────────────────────────────────────────────────

def add_session_note(note: str, session_date: Optional[str] = None) -> None:
    """Store a free-text note for a session (injury signs, observations, etc.)."""
    d = session_date or date.today().isoformat()
    con = _con()
    con.execute("INSERT INTO session_notes (date, note, source) VALUES (?, ?, 'manual')", (d, note))
    con.commit()
    con.close()
    print(f"[feedback] Note saved for {d}: {note}")


def recent_notes(n: int = 5) -> list[dict]:
    """Returns the last N session notes for Claude's context."""
    con = _con()
    rows = con.execute("""
        SELECT date, note, source FROM session_notes
        ORDER BY date DESC, id DESC LIMIT ?
    """, (n,)).fetchall()
    con.close()
    return [{"date": r["date"], "note": r["note"], "source": r["source"]} for r in rows]


def recent_feedback(n: int = 3) -> list[dict]:
    """
    Returns the last N workout diffs for use in Claude's context.
    Each entry: {date, session_type, diff}
    """
    con = _con()
    rows = con.execute("""
        SELECT date, session_type, diff_json
        FROM workout_feedback
        ORDER BY date DESC
        LIMIT ?
    """, (n,)).fetchall()
    con.close()
    return [
        {"date": r["date"], "session_type": r["session_type"], "diff": json.loads(r["diff_json"])}
        for r in rows
    ]


if __name__ == "__main__":
    print("[feedback] Backfilling diffs for last 14 days...")
    count = run_feedback_recent(days=14)
    print(f"[feedback] {count} diffs stored.")
