"""
context.py — Builds the structured context dict that gets passed to Claude.

Reads from the SQLite DB and computes:
  - Per main lift: last weight, e1RM trend, days since last session, plateau flag
  - Session type balance over the last 4 weeks
  - Fatigue: sessions in the last 7 days
  - Latest bodyweight from Withings (if available)
  - Suggested session type for today (based on balance + recovery)
  - Exercise priority list for today's session (days_since / target_freq_days)
"""
import sqlite3
import json
from datetime import date, timedelta
from typing import Optional
import config


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── Per-lift metrics ───────────────────────────────────────────────────────

def lift_history(exercise: str, n: int = 8) -> list[dict]:
    """Last n working sets (non-warmup) grouped by session, most recent first."""
    con = _con()
    rows = con.execute("""
        SELECT date,
               MAX(weight_kg)       AS top_weight,
               MAX(reps)            AS max_reps,
               ROUND(MAX(e1rm), 1)  AS best_e1rm
        FROM sets
        WHERE exercise = ?
          AND is_warmup = 0
          AND e1rm IS NOT NULL
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
    """, (exercise, n)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def plateau_detected(history: list[dict], threshold: int = config.PLATEAU_SESSIONS) -> bool:
    """True if e1RM hasn't improved over the last `threshold` sessions."""
    if len(history) < threshold:
        return False
    e1rms = [h["best_e1rm"] for h in history[:threshold] if h["best_e1rm"]]
    if not e1rms:
        return False
    return max(e1rms) <= e1rms[-1] * 1.01   # <1% improvement = plateau


def days_since_last(exercise: str) -> Optional[int]:
    con = _con()
    row = con.execute("""
        SELECT MAX(date) AS last FROM sets WHERE exercise = ?
    """, (exercise,)).fetchone()
    con.close()
    if not row or not row["last"]:
        return None
    last = date.fromisoformat(row["last"])
    return (date.today() - last).days


# ── Session balance ────────────────────────────────────────────────────────

def session_balance(days: int = 28) -> dict[str, int]:
    """Count of each session type in the last N days."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT session_type, COUNT(DISTINCT session_id) AS n
        FROM sets
        WHERE date >= ? AND session_type != 'unknown'
        GROUP BY session_type
    """, (since,)).fetchall()
    con.close()
    base = {"push": 0, "pull": 0, "legs": 0, "arms": 0}
    for r in rows:
        base[r["session_type"]] = r["n"]
    return base


def consecutive_training_days() -> int:
    """Count consecutive days with sessions ending yesterday (today not yet trained)."""
    con = _con()
    rows = con.execute("""
        SELECT DISTINCT date FROM sets ORDER BY date DESC LIMIT 14
    """).fetchall()
    con.close()
    dates = {date.fromisoformat(r["date"]) for r in rows}
    count = 0
    check = date.today() - timedelta(days=1)
    while check in dates:
        count += 1
        check -= timedelta(days=1)
    return count


def recent_session_dates(days: int = 7) -> list[str]:
    """Dates of all sessions in the last N days."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT DISTINCT date FROM sets
        WHERE date >= ?
        ORDER BY date DESC
    """, (since,)).fetchall()
    con.close()
    return [r["date"] for r in rows]


def last_session_exercises() -> list[str]:
    """Exercises (working sets only) from the most recently completed session."""
    con = _con()
    row = con.execute("""
        SELECT MAX(date) FROM sets WHERE session_type != 'unknown'
    """).fetchone()
    if not row or not row[0]:
        con.close()
        return []
    last_date = row[0]
    rows = con.execute("""
        SELECT DISTINCT exercise FROM sets
        WHERE date = ? AND is_warmup = 0
        ORDER BY exercise
    """, (last_date,)).fetchall()
    con.close()
    return [r["exercise"] for r in rows]


def last_session_type() -> Optional[str]:
    con = _con()
    row = con.execute("""
        SELECT session_type FROM sets
        WHERE session_type != 'unknown'
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    con.close()
    return row["session_type"] if row else None


def recent_session_types(n: int = 6) -> list[str]:
    """Last N distinct sessions in reverse-chronological order, as short labels."""
    _abbrev = {"push": "Push", "pull": "Pull", "legs": "Legs", "arms": "Arms"}
    con = _con()
    rows = con.execute("""
        SELECT session_type FROM (
            SELECT date, session_type
            FROM sets
            WHERE session_type != 'unknown'
            GROUP BY date
            ORDER BY date DESC
            LIMIT ?
        )
    """, (n,)).fetchall()
    con.close()
    return [_abbrev.get(r["session_type"], r["session_type"].title()) for r in rows]


# ── Bodyweight ─────────────────────────────────────────────────────────────

def latest_bodyweight() -> Optional[dict]:
    con = _con()
    row = con.execute("""
        SELECT date, weight_kg, muscle_mass_kg, body_fat_pct FROM bodyweight
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    con.close()
    if not row:
        return None
    return {
        "date":           row["date"],
        "weight_kg":      row["weight_kg"],
        "muscle_mass_kg": row["muscle_mass_kg"],
        "body_fat_pct":   row["body_fat_pct"],
    }


def bodyweight_trend(days: int = 30) -> Optional[float]:
    """Simple linear trend in kg/week over last N days. Negative = losing weight."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT date, weight_kg FROM bodyweight
        WHERE date >= ? ORDER BY date
    """, (since,)).fetchall()
    con.close()
    if len(rows) < 4:
        return None
    # Simple slope via least squares
    xs = [(date.fromisoformat(r["date"]) - date.fromisoformat(rows[0]["date"])).days
          for r in rows]
    ys = [r["weight_kg"] for r in rows]
    n = len(xs)
    sx, sy, sxy, sx2 = sum(xs), sum(ys), sum(x*y for x,y in zip(xs,ys)), sum(x**2 for x in xs)
    slope_per_day = (n*sxy - sx*sy) / (n*sx2 - sx**2 + 1e-9)
    return round(slope_per_day * 7, 3)   # kg/week


# ── Suggested session type ─────────────────────────────────────────────────

def days_since_session_type(stype: str) -> Optional[int]:
    """Days since the last session of the given type, or None if never."""
    con = _con()
    row = con.execute("""
        SELECT MAX(date) FROM sets WHERE session_type = ?
    """, (stype,)).fetchone()
    con.close()
    val = row[0] if row else None
    if not val:
        return None
    return (date.today() - date.fromisoformat(val)).days


def suggest_session_type() -> str:
    """
    Picks the most overdue recovered session type, using cycle order as a tiebreaker.
    'Recovered' means days_since >= MIN_RECOVERY_DAYS (or never trained).
    Excludes the last session type to avoid back-to-back repeats.
    Falls back to the next in cycle if nothing is recovered yet.
    """
    cycle     = config.SESSION_CYCLE
    last_type = last_session_type()

    candidates = [t for t in cycle if t != last_type]

    recovered = []
    for t in candidates:
        days = days_since_session_type(t)
        if days is None or days >= config.MIN_RECOVERY_DAYS:
            recovered.append(t)

    if not recovered:
        recovered = candidates  # nothing fully recovered — pick least-recently-trained

    # Sort: most days since last session first; use cycle position as tiebreaker
    def _sort_key(t):
        days = days_since_session_type(t)
        days_val = days if days is not None else 9999
        cycle_pos = cycle.index(t) if t in cycle else 99
        return (-days_val, cycle_pos)

    return sorted(recovered, key=_sort_key)[0]


# ── Exercise priority roster ──────────────────────────────────────────────

def exercise_priorities(session_type: str) -> list[dict]:
    """
    Returns exercises for session_type sorted by priority (highest first).
    priority = days_since_last / target_freq_days  (>1.0 = overdue)

    Sources last-trained date from both sets table and prescribed_sessions.
    Applies any active block overrides (suspend / add / priority_bump).
    Returns [] gracefully if exercise_roster tables haven't been created yet.
    """
    today = date.today()
    today_iso = today.isoformat()

    # priority ceiling per star — lower-starred exercises can never outcompete higher ones
    STAR_CAP = {5: 3.0, 4: 2.5, 3: 2.0, 2: 1.0, 1: 0.5, 0: 0.0}

    try:
        con = _con()
        roster = con.execute("""
            SELECT exercise_name, is_main_lift, target_freq_days, star_rating
            FROM exercise_roster
            WHERE session_type = ? AND active = 1 AND star_rating > 0
        """, (session_type,)).fetchall()
    except sqlite3.OperationalError:
        return []

    # Active block (if any)
    block_id: Optional[int] = None
    try:
        row = con.execute("""
            SELECT id FROM blocks
            WHERE status = 'active'
              AND (start_date IS NULL OR start_date <= ?)
              AND (end_date   IS NULL OR end_date   >= ?)
            ORDER BY id DESC LIMIT 1
        """, (today_iso, today_iso)).fetchone()
        block_id = row["id"] if row else None
    except sqlite3.OperationalError:
        pass

    suspended: set[str] = set()
    additions: list[tuple[str, float]] = []   # (exercise_name, priority_bump)

    if block_id:
        try:
            overrides = con.execute("""
                SELECT suspend_exercise, add_exercise, priority_bump
                FROM block_overrides
                WHERE block_id = ?
                  AND (session_type IS NULL OR session_type = ?)
            """, (block_id, session_type)).fetchall()
            for o in overrides:
                if o["suspend_exercise"]:
                    suspended.add(o["suspend_exercise"])
                if o["add_exercise"]:
                    additions.append((o["add_exercise"], float(o["priority_bump"] or 0)))
        except sqlite3.OperationalError:
            pass

    def _days_since(name: str) -> Optional[int]:
        last_sets = con.execute("""
            SELECT MAX(date) FROM sets WHERE exercise = ? AND is_warmup != 1
        """, (name,)).fetchone()[0]
        try:
            last_prescribed = con.execute("""
                SELECT MAX(date) FROM prescribed_sessions
                WHERE EXISTS (
                    SELECT 1 FROM json_each(exercises_json)
                    WHERE json_extract(value, '$.exercise_name') = ?
                )
            """, (name,)).fetchone()[0]
        except sqlite3.OperationalError:
            last_prescribed = None
        candidates = [d for d in [last_sets, last_prescribed] if d]
        if not candidates:
            return None
        return (today - date.fromisoformat(max(candidates))).days

    result: list[dict] = []
    seen: set[str] = set()

    for ex in roster:
        name = ex["exercise_name"]
        if name in suspended:
            continue
        days = _days_since(name)
        # Never-trained exercises get priority 1.0 (due, not overdue) rather than
        # 999/freq which would make them dominate over exercises actually in rotation.
        days_val = days if days is not None else ex["target_freq_days"]
        star  = int(ex["star_rating"])
        raw   = days_val / ex["target_freq_days"]
        cap   = STAR_CAP.get(star, 1.0)
        result.append({
            "exercise_name":    name,
            "is_main_lift":     bool(ex["is_main_lift"]),
            "target_freq_days": ex["target_freq_days"],
            "days_since_last":  days,
            "star_rating":      star,
            "priority":         round(min(raw, cap), 2),
        })
        seen.add(name)

    for add_name, bump in additions:
        if add_name in suspended:
            continue
        if add_name in seen:
            for item in result:
                if item["exercise_name"] == add_name:
                    # bump can push above the normal 3.0 cap to signal explicit override intent
                    item["priority"] = round(min(item["priority"] + bump, 5.0), 2)
            continue
        # Exercise not in base roster — add it
        try:
            entry = con.execute("""
                SELECT target_freq_days FROM exercise_roster WHERE exercise_name = ?
            """, (add_name,)).fetchone()
            freq = entry["target_freq_days"] if entry else 7.0
        except sqlite3.OperationalError:
            freq = 7.0
        days = _days_since(add_name)
        days_val = days if days is not None else 999
        raw = days_val / freq
        result.append({
            "exercise_name":    add_name,
            "is_main_lift":     False,
            "target_freq_days": freq,
            "days_since_last":  days,
            "priority":         round(min(raw, 3.0) + bump, 2),
        })
        seen.add(add_name)

    con.close()
    excluded = {e.lower() for e in getattr(config, "EXCLUDED_EXERCISES", [])}
    result = [r for r in result if r["exercise_name"].lower() not in excluded]
    result.sort(key=lambda x: x["priority"], reverse=True)
    return result


# ── All-time exercise statistics ─────────────────────────────────────────

def exercise_stats_all_time() -> dict[str, dict]:
    """
    Compute per-exercise statistics from the full training history.
    Returns {exercise_name: {best_e1rm, current_e1rm, best_weight_kg, total_sessions, trend}}
    trend: "↑" | "↓" | "→" | None  (compares last 4w e1RM vs prior 4w)
    """
    con = _con()

    # All-time: best e1RM, best weight, session count
    all_time = {
        r["exercise"]: {
            "best_e1rm":     round(r["best_e1rm"], 1) if r["best_e1rm"] else None,
            "best_weight_kg": r["best_weight_kg"],
            "total_sessions": r["total_sessions"],
        }
        for r in con.execute("""
            SELECT exercise,
                   MAX(e1rm)           AS best_e1rm,
                   MAX(weight_kg)      AS best_weight_kg,
                   COUNT(DISTINCT date) AS total_sessions
            FROM sets WHERE is_warmup = 0
            GROUP BY exercise
        """).fetchall()
    }

    # Most recent e1RM per exercise
    for r in con.execute("""
        SELECT s.exercise, MAX(s.e1rm) AS current_e1rm
        FROM sets s
        INNER JOIN (
            SELECT exercise, MAX(date) AS last_date
            FROM sets WHERE is_warmup = 0
            GROUP BY exercise
        ) latest ON s.exercise = latest.exercise AND s.date = latest.last_date
        WHERE s.is_warmup = 0
        GROUP BY s.exercise
    """).fetchall():
        if r["exercise"] in all_time:
            all_time[r["exercise"]]["current_e1rm"] = (
                round(r["current_e1rm"], 1) if r["current_e1rm"] else None
            )

    # 4-week trend: compare best e1RM in last 28 days vs prior 28 days
    cutoff_recent = (date.today() - timedelta(days=28)).isoformat()
    cutoff_prior  = (date.today() - timedelta(days=56)).isoformat()

    recent_e1rm = {
        r["exercise"]: r["best_e1rm"]
        for r in con.execute("""
            SELECT exercise, MAX(e1rm) AS best_e1rm FROM sets
            WHERE is_warmup = 0 AND date >= ? GROUP BY exercise
        """, (cutoff_recent,)).fetchall()
        if r["best_e1rm"]
    }
    prior_e1rm = {
        r["exercise"]: r["best_e1rm"]
        for r in con.execute("""
            SELECT exercise, MAX(e1rm) AS best_e1rm FROM sets
            WHERE is_warmup = 0 AND date >= ? AND date < ? GROUP BY exercise
        """, (cutoff_prior, cutoff_recent)).fetchall()
        if r["best_e1rm"]
    }

    con.close()

    for ex, stats in all_time.items():
        recent = recent_e1rm.get(ex)
        prior  = prior_e1rm.get(ex)
        if recent and prior:
            delta_pct = (recent - prior) / prior * 100
            stats["trend"] = "↑" if delta_pct > 2 else ("↓" if delta_pct < -2 else "→")
        else:
            stats["trend"] = None
        stats.setdefault("current_e1rm", None)

    return all_time


# ── Recent workout history ────────────────────────────────────────────────

def recent_workouts(days: int = 28) -> list[dict]:
    """
    Returns all sessions in the last N days, each with full exercise data (working sets only).
    Sorted newest first.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    date_rows = con.execute("""
        SELECT date, session_type
        FROM sets
        WHERE session_type != 'unknown' AND date >= ?
        GROUP BY date
        ORDER BY date DESC
    """, (since,)).fetchall()

    sessions = []
    for dr in date_rows:
        ex_rows = con.execute("""
            SELECT exercise,
                   MAX(weight_kg)       AS top_weight_kg,
                   ROUND(AVG(reps), 1)  AS avg_reps,
                   COUNT(*)             AS sets
            FROM sets
            WHERE date = ? AND is_warmup = 0 AND session_type != 'unknown'
            GROUP BY exercise
            ORDER BY MAX(set_number)
        """, (dr["date"],)).fetchall()
        sessions.append({
            "date":         dr["date"],
            "session_type": dr["session_type"],
            "exercises":    [dict(r) for r in ex_rows],
        })
    con.close()
    return sessions


# ── e1RM trend ────────────────────────────────────────────────────────────

def e1rm_trends(weeks: int = 4) -> dict[str, dict]:
    """
    For each main lift, compare most-recent e1RM to the oldest session in the window.
    Returns {lift_name: {current_e1rm, baseline_e1rm, delta_kg, delta_pct, trend}}
    trend: "↑" | "↓" | "→" | "?"
    """
    cutoff = (date.today() - timedelta(days=weeks * 7)).isoformat()
    result = {}
    for lift_name in config.MAIN_LIFTS:
        history = lift_history(lift_name, n=20)
        window = [h for h in history if h["date"] >= cutoff and h["best_e1rm"]]
        if not window:
            result[lift_name] = {"trend": "?", "current_e1rm": None, "baseline_e1rm": None,
                                 "delta_kg": None, "delta_pct": None}
            continue
        current_e1rm = window[0]["best_e1rm"]   # most recent (history is DESC)
        if len(window) < 2:
            result[lift_name] = {"trend": "?", "current_e1rm": current_e1rm,
                                 "baseline_e1rm": None, "delta_kg": None, "delta_pct": None}
            continue
        baseline_e1rm = window[-1]["best_e1rm"]  # oldest in window
        delta_kg = round(current_e1rm - baseline_e1rm, 1)
        delta_pct = round(delta_kg / baseline_e1rm * 100, 1) if baseline_e1rm else 0
        trend = "↑" if delta_kg > 1 else ("↓" if delta_kg < -1 else "→")
        result[lift_name] = {
            "trend": trend,
            "current_e1rm": current_e1rm,
            "baseline_e1rm": baseline_e1rm,
            "delta_kg": delta_kg,
            "delta_pct": delta_pct,
        }
    return result


# ── Prescription logging ──────────────────────────────────────────────────

def active_block_id() -> Optional[int]:
    """Return the id of the currently active block, or None."""
    today_iso = date.today().isoformat()
    try:
        con = _con()
        row = con.execute("""
            SELECT id FROM blocks
            WHERE status = 'active'
              AND (start_date IS NULL OR start_date <= ?)
              AND (end_date   IS NULL OR end_date   >= ?)
            ORDER BY id DESC LIMIT 1
        """, (today_iso, today_iso)).fetchone()
        con.close()
        return row["id"] if row else None
    except sqlite3.OperationalError:
        return None


def log_prescription(workout: dict, block_id: Optional[int] = None) -> int:
    """
    Save a Claude-prescribed workout to prescribed_sessions.
    Returns the new row id.
    """
    today_iso     = date.today().isoformat()
    session_type  = workout.get("session_type", "unknown")
    exercises_json = json.dumps(workout.get("exercises", []))
    reasoning     = workout.get("reasoning", "")

    con = _con()
    try:
        cur = con.execute("""
            INSERT INTO prescribed_sessions
                (block_id, date, session_type, exercises_json, reasoning, posted_to_hevy)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (block_id, today_iso, session_type, exercises_json, reasoning))
        row_id = cur.lastrowid
        con.commit()
    except sqlite3.OperationalError as e:
        print(f"[context] Could not log prescription (run migrate.py first): {e}")
        row_id = -1
    finally:
        con.close()
    return row_id


def mark_posted_to_hevy(prescribed_session_id: int) -> None:
    """Mark a prescribed session as posted to Hevy."""
    if prescribed_session_id < 0:
        return
    try:
        con = _con()
        con.execute("""
            UPDATE prescribed_sessions SET posted_to_hevy = 1 WHERE id = ?
        """, (prescribed_session_id,))
        con.commit()
        con.close()
    except sqlite3.OperationalError:
        pass


# ── Main context builder ───────────────────────────────────────────────────

def build_context() -> dict:
    """
    Returns a dict with all context needed by Claude to prescribe today's workout.
    Also serialisable to JSON for logging / debugging.
    """
    today      = date.today().isoformat()
    is_weekend = date.today().weekday() >= 5
    session_type = suggest_session_type()

    # Build per-lift context for main lifts
    lifts_context = {}
    for lift_name, cfg in config.MAIN_LIFTS.items():
        history = lift_history(lift_name, n=8)
        days_ago = days_since_last(lift_name)
        plateau = plateau_detected(history)
        is_bw = cfg.get("is_bodyweight", False)

        lifts_context[lift_name] = {
            "session_type": cfg["session_type"],
            "days_since_last_session": days_ago,
            "is_bodyweight": is_bw,
            "plateau_detected": plateau,
            "target_sets": cfg["target_sets"],
            "rep_range": cfg["rep_range"],
            "progression_step_kg": cfg["progression_kg"],
            "recent_sessions": history[:6],   # last 6 for Claude context
        }

    bw = latest_bodyweight()
    bw_trend = bodyweight_trend()
    balance = session_balance(days=28)
    recent_sessions = recent_session_dates(days=7)
    priorities = exercise_priorities(session_type)
    days_since_this_type = days_since_session_type(session_type)

    try:
        from feedback import recent_feedback, recent_notes
        feedback = recent_feedback(n=3)
        notes    = recent_notes(n=5)
    except Exception:
        feedback = []
        notes    = []

    try:
        from focus import get_phase
        phase = get_phase(session_type)
    except Exception:
        phase = None

    try:
        from creators import top_recommendations
        creator_recs = top_recommendations(session_type=session_type, n=10)
    except Exception:
        creator_recs = []

    return {
        "today": today,
        "is_weekend": is_weekend,
        "suggested_session_type": session_type,
        "bodyweight_kg":      bw["weight_kg"]      if bw else None,
        "bodyweight_date":    bw["date"]           if bw else None,
        "muscle_mass_kg":     bw["muscle_mass_kg"] if bw else None,
        "body_fat_pct":       bw["body_fat_pct"]   if bw else None,
        "bodyweight_trend_kg_per_week": bw_trend,
        "sessions_last_7_days": len(recent_sessions),
        "session_dates_last_7_days": recent_sessions,
        "session_balance_last_28_days": balance,
        "last_session_type": last_session_type(),
        "last_session_exercises": last_session_exercises(),
        "recent_workouts":        recent_workouts(days=28),
        "exercise_stats":         exercise_stats_all_time(),
        "main_lifts": lifts_context,
        "exercise_priorities": priorities,
        "days_since_last_session_of_type": days_since_this_type,
        "recent_workout_feedback":         feedback,
        "session_notes":                   notes,
        "focus_phase":                     phase,
        "creator_recommendations":         creator_recs,
    }


if __name__ == "__main__":
    ctx = build_context()
    print(json.dumps(ctx, indent=2, default=str))
