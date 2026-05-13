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
        ORDER BY MIN(set_number)
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
        p_weight = max((s.get("weight_kg", 0) for s in p_sets), default=0)
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

    # Detect significant reordering of exercises that were both prescribed and completed
    prescribed_order = [ex["exercise_name"] for ex in prescription.get("exercises", [])]
    common_prescribed = [n for n in prescribed_order if n in actual]
    common_actual     = [n for n in actual if n in prescribed_names]
    reordered = common_actual if common_prescribed != common_actual and len(common_prescribed) > 2 else []

    return {
        "skipped":            skipped,
        "added":              added,
        "weight_adjustments": weight_adjustments,
        "reps_adjustments":   reps_adjustments,
        "reordered":          reordered,
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
        diff.get("reordered"),
    ])


def _store_review(session_date: str, analysis: str, source: str) -> None:
    con = _con()
    # Replace any prior review for this date+source so re-runs with new notes produce fresh analysis
    con.execute(
        "DELETE FROM session_notes WHERE date = ? AND source = ?",
        (session_date, source),
    )
    con.execute(
        "INSERT INTO session_notes (date, note, source) VALUES (?, ?, ?)",
        (session_date, analysis, source),
    )
    con.commit()
    con.close()


def _get_stored_review(session_date: str, source: str) -> Optional[str]:
    con = _con()
    row = con.execute(
        "SELECT note FROM session_notes WHERE date = ? AND source = ? ORDER BY id DESC LIMIT 1",
        (session_date, source),
    ).fetchone()
    con.close()
    return row["note"] if row else None


def _get_session_notes(session_date: str) -> list[dict]:
    """Return user_directive and hevy_exercise notes for a session date."""
    con = _con()
    rows = con.execute("""
        SELECT note, source FROM session_notes
        WHERE date = ? AND source IN ('user_directive', 'hevy_exercise', 'manual')
        ORDER BY id
    """, (session_date,)).fetchall()
    con.close()
    return [{"note": r["note"], "source": r["source"]} for r in rows]


def run_feedback_for_date(session_date: str) -> Optional[dict]:
    """
    Find the prescription for session_date, pull actual sets, compute and store diff.
    Actual sets are searched on session_date and the following day (workout may be
    completed the day after the prescription was created).
    Returns the diff dict, or None if no prescription found or already processed.
    """
    from datetime import date as _d, timedelta

    # Find actual sets — check session_date, then next day
    actual      = _actual_sets(session_date)
    actual_date = session_date
    if not actual:
        next_day = (_d.fromisoformat(session_date) + timedelta(days=1)).isoformat()
        actual   = _actual_sets(next_day)
        actual_date = next_day if actual else session_date
    if not actual:
        return None

    # Determine actual session type
    con2 = _con()
    actual_type_row = con2.execute("""
        SELECT session_type FROM sets
        WHERE date = ? AND session_type != 'unknown'
        GROUP BY session_type ORDER BY COUNT(*) DESC LIMIT 1
    """, (actual_date,)).fetchone()
    con2.close()
    actual_session_type = actual_type_row["session_type"] if actual_type_row else None

    # Find prescription: prefer one matching the actual session type (avoids cross-matching
    # when the user does a different session than was prescribed)
    con = _con()
    if actual_session_type:
        row = con.execute("""
            SELECT id, session_type, exercises_json, reasoning
            FROM prescribed_sessions
            WHERE date = ? AND posted_to_hevy = 1
            ORDER BY CASE WHEN session_type = ? THEN 0 ELSE 1 END, id DESC LIMIT 1
        """, (session_date, actual_session_type)).fetchone()
    else:
        row = con.execute("""
            SELECT id, session_type, exercises_json, reasoning
            FROM prescribed_sessions
            WHERE date = ? AND posted_to_hevy = 1
            ORDER BY id DESC LIMIT 1
        """, (session_date,)).fetchone()

    if not row:
        con.close()
        return None

    prescription_id = row["id"]
    session_type    = row["session_type"]
    prescription    = {"exercises": json.loads(row["exercises_json"])}
    reasoning       = row["reasoning"] or ""

    # Gap before this session (helps reviewer judge re-entry / illness context)
    prior = con.execute("""
        SELECT MAX(date) AS d FROM sets
        WHERE date < ? AND session_type != 'unknown'
    """, (actual_date,)).fetchone()
    if prior and prior["d"]:
        days_since_prior = (_d.fromisoformat(actual_date) - _d.fromisoformat(prior["d"])).days
    else:
        days_since_prior = None

    # Notes from the 7 days leading up to and including the session
    notes_window_start = (_d.fromisoformat(actual_date) - timedelta(days=7)).isoformat()
    surrounding_notes = con.execute("""
        SELECT date, note, source FROM session_notes
        WHERE date >= ? AND date <= ?
          AND source NOT IN ('completed_review', 'pre_session_review')
        ORDER BY date DESC
    """, (notes_window_start, actual_date)).fetchall()
    surrounding_notes = [dict(n) for n in surrounding_notes]
    con.close()

    notes = _get_session_notes(actual_date)
    handle_debug_notes(actual_date)

    review_ctx = {
        "reasoning":        reasoning,
        "days_since_prior": days_since_prior,
        "surrounding_notes": surrounding_notes,
    }

    # If already stored, print diff + regenerate review if missing or notes have arrived since
    con3 = _con()
    existing_row = con3.execute(
        "SELECT diff_json, session_type FROM workout_feedback WHERE prescription_id = ?",
        (prescription_id,)
    ).fetchone()
    con3.close()
    if existing_row:
        stored_diff = json.loads(existing_row["diff_json"])
        label = (f"prescribed: {existing_row['session_type']} → actual: {actual_session_type}"
                 if actual_session_type != existing_row["session_type"] else existing_row["session_type"])
        if not diff_is_empty(stored_diff):
            _print_diff(session_date, label, stored_diff)
        stored_review = _get_stored_review(actual_date, "completed_review")
        if stored_review:
            print(f"[feedback] Review:\n{stored_review}")
        elif not diff_is_empty(stored_diff) or notes:
            analysis = analyze_diff(stored_diff, prescription, session_type,
                                    context="completed", notes=notes,
                                    review_ctx=review_ctx)
            if analysis:
                print(f"[feedback] Review:\n{analysis}")
                _store_review(actual_date, analysis, "completed_review")
        return stored_diff

    diff = compute_diff(prescription, actual)
    store_diff(session_date, prescription_id, session_type, diff)
    label = (f"prescribed: {session_type} → actual: {actual_session_type}"
             if actual_session_type != session_type else session_type)
    if not diff_is_empty(diff):
        _print_diff(session_date, label, diff)
    if not diff_is_empty(diff) or notes:
        analysis = analyze_diff(diff, prescription, session_type,
                                context="completed", notes=notes,
                                review_ctx=review_ctx)
        if analysis:
            print(f"[feedback] Review:\n{analysis}")
            _store_review(actual_date, analysis, "completed_review")

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
    if diff.get("reordered"):
        print(f"  reorder:  actual order was: {' → '.join(diff['reordered'])}")


def analyze_diff(diff: dict, prescription: dict, session_type: str,
                 context: str = "pre_session", notes: list[dict] | None = None,
                 review_ctx: dict | None = None) -> str:
    """
    Call Claude Haiku to assess a workout diff and any session notes.
    context: "pre_session" (user edited before starting) | "completed" (what was actually done).
    Returns a short analysis string, or "" on failure.
    """
    import requests
    prescribed_names = [ex["exercise_name"] for ex in prescription.get("exercises", [])]

    diff_lines = []
    for name in diff.get("skipped", []):
        diff_lines.append(f"  REMOVED: {name}")
    for name in diff.get("added", []):
        diff_lines.append(f"  ADDED: {name}")
    for w in diff.get("weight_adjustments", []):
        sign = "+" if w["delta_pct"] > 0 else ""
        diff_lines.append(
            f"  WEIGHT: {w['exercise']} {w['prescribed_kg']}→{w['actual_kg']}kg ({sign}{w['delta_pct']}%)")
    for r in diff.get("reps_adjustments", []):
        diff_lines.append(
            f"  REPS: {r['exercise']} {r['prescribed_reps']}→{r['actual_reps']}")

    if context == "pre_session":
        situation = f"Before starting, the athlete edited the {session_type} routine. Only changes are listed below; anything not listed was left as prescribed:"
    else:
        situation = (f"The athlete completed the {session_type} session. ONLY DEVIATIONS from the "
                     f"prescription are listed below — every prescribed exercise NOT listed below "
                     f"was completed exactly as prescribed. Do not infer skipped exercises from absence:")

    prompt = (
        f"A strength AI prescribed this {session_type} session:\n"
        + "\n".join(f"  {n}" for n in prescribed_names)
    )

    if review_ctx:
        ctx_lines = []
        dsp = review_ctx.get("days_since_prior")
        if dsp is not None:
            ctx_lines.append(f"Gap before this session: {dsp} days since prior gym session.")
        rs = review_ctx.get("reasoning") or ""
        if rs:
            ctx_lines.append(f"Prescription reasoning: {rs}")
        sn = review_ctx.get("surrounding_notes") or []
        if sn:
            ctx_lines.append("Notes from the 7 days leading up to this session:")
            for n in sn:
                ctx_lines.append(f"  {n['date']} [{n['source']}] {n['note']}")
        if ctx_lines:
            prompt += (
                "\n\nIMPORTANT — context for judging this session (do NOT critique as a normal session "
                "if any of this indicates re-entry, illness, deload, or fatigue management):\n"
                + "\n".join(ctx_lines)
            )

    if diff_lines:
        prompt += f"\n\n{situation}\n" + "\n".join(diff_lines)
        prompt += (
            "\n\nOn the diff, cover briefly:\n"
            "1. Were the changes sensible?\n"
            "2. Any important movement patterns dropped or doubled?\n"
            "3. Preference or avoidance of something necessary?\n"
        )
    else:
        prompt += f"\n\nThe athlete completed every prescribed exercise with no deviations ({session_type})."

    if notes:
        notes_text = "\n".join(f"  [{n['source']}] {n['note']}" for n in notes)
        prompt += (
            f"\n\nAthlete notes from this session:\n{notes_text}\n\n"
            "Respond in plain prose (no markdown headers, no bullet tables). "
            "Cover the diff briefly, then address each note in 1-2 sentences: "
            "what it implies for future sessions and whether anything should change in how this exercise is prescribed. "
            "Flag anything critical. 150-200 words total."
        )
    else:
        prompt += "\n\nBe direct and specific. Plain prose, 3-4 sentences."

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"[feedback] Diff analysis failed: {e}")
        return ""


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
        SELECT id, date, session_type, exercises_json FROM prescribed_sessions
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
        print(f"[feedback] Template edits from previous {row['session_type']} session:")
        _print_diff(row["date"], row["session_type"], diff)

        analysis = analyze_diff(diff, prescription, row["session_type"], context="pre_session")
        if analysis:
            print(f"[feedback] Review: {analysis}")
            _store_review(row["date"], analysis, "overwrite_review")

    return diff if not diff_is_empty(diff) else None


# ── Debug note handling ───────────────────────────────────────────────────

def handle_debug_notes(session_date: str) -> None:
    """
    Process any DEBUG: notes for a session. Looks up relevant DB data and calls
    Claude Haiku to investigate the issue, printing and storing the finding.
    """
    import requests
    from context import lift_history as _lift_history

    con = _con()
    rows = con.execute(
        "SELECT id, note FROM session_notes WHERE date = ? AND source = 'debug_request'",
        (session_date,)
    ).fetchall()
    con.close()

    for row in rows:
        note_text = row["note"]
        # Parse "ExerciseName: DEBUG: question text"
        parts = note_text.split(":", 2)
        ex_name  = parts[0].strip() if len(parts) >= 2 else ""
        question = ":".join(parts[2:]).strip() if len(parts) >= 3 else note_text

        # Gather context: exercise history from DB
        history = _lift_history(ex_name, n=10) if ex_name else []
        history_text = (
            "\n".join(f"  {h['date']}: {h['top_weight']}kg × {h['max_reps']} reps  e1RM={h['best_e1rm']}kg"
                      for h in history)
            or "  (no history in DB)"
        )

        prompt = (
            f"An athlete left this debug note after their session:\n"
            f"  Exercise: {ex_name or '(unspecified)'}\n"
            f"  Question: {question}\n\n"
            f"Exercise history in the training DB for '{ex_name}':\n{history_text}\n\n"
            "Investigate the issue directly. In 3-5 sentences:\n"
            "1. What's the most likely explanation based on the data?\n"
            "2. Is there a data/tracking problem, and if so what's the fix?\n"
            "3. What should change in the next prescription for this exercise?\n"
            "Be specific. If history is empty, say so and recommend a conservative starting weight."
        )

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            finding = resp.json()["content"][0]["text"].strip()
            print(f"[debug] {ex_name}: {finding}")
            # Store as a manual note so it surfaces to Claude in future sessions
            _store_review(session_date, f"[DEBUG finding — {ex_name}] {finding}", "debug_finding")
        except Exception as e:
            print(f"[debug] Investigation failed: {e}")


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
