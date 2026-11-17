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
import re
from datetime import date, datetime, timedelta
from typing import Optional
import config


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── Per-lift metrics ───────────────────────────────────────────────────────

def lift_history(exercise: str, n: int = 8) -> list[dict]:
    """Last n sessions for an exercise, most recent first.

    Each row carries the FULL per-set vector:
      sets — [{weight, reps, rpe}, ...] in set_number order, working sets only.
             No compression. This is the raw record of the session.

    Summary fields are derived from `sets` for convenience:
      top_*     — heaviest working set (by e1RM, fallback weight/reps).
      working_* — mode weight + mode reps at that weight. Progression should
                  anchor here, not on top_weight.
      working_set_count, avg_rpe, first_set_rpe, last_set_rpe.
    """
    from collections import Counter
    con = _con()
    date_rows = con.execute("""
        SELECT date FROM sets
        WHERE exercise = ? AND is_warmup = 0 AND reps IS NOT NULL AND reps > 0
        GROUP BY date ORDER BY date DESC LIMIT ?
    """, (exercise, n)).fetchall()

    result = []
    for r in date_rows:
        d = r["date"]
        rows = con.execute("""
            SELECT weight_kg, reps, rpe, e1rm FROM sets
            WHERE exercise = ? AND date = ? AND is_warmup = 0 AND reps > 0
            ORDER BY set_number
        """, (exercise, d)).fetchall()
        if not rows:
            continue

        sets = [{"weight": row["weight_kg"], "reps": row["reps"], "rpe": row["rpe"]} for row in rows]
        # Top set: max e1RM; for pure-BW (e1rm NULL) fall back to weight then reps.
        top = max(rows, key=lambda x: (
            x["e1rm"] if x["e1rm"] is not None else -1,
            x["weight_kg"] or 0,
            x["reps"] or 0,
        ))
        # Working set: mode weight (ties → heavier), then mode reps at that weight (ties → more).
        wc = Counter(row["weight_kg"] for row in rows)
        working_weight = max(wc.items(), key=lambda kv: (kv[1], kv[0]))[0]
        reps_at_w = [row["reps"] for row in rows if row["weight_kg"] == working_weight]
        rc = Counter(reps_at_w)
        working_reps = max(rc.items(), key=lambda kv: (kv[1], kv[0]))[0]
        rpes = [row["rpe"] for row in rows if row["rpe"] is not None]

        result.append({
            "date":              d,
            "sets":              sets,
            "top_weight":        top["weight_kg"],
            "top_reps":          top["reps"],
            "best_e1rm":         round(top["e1rm"], 1) if top["e1rm"] is not None else None,
            "working_weight":    working_weight,
            "working_reps":      working_reps,
            "working_set_count": len(rows),
            "avg_rpe":           round(sum(rpes) / len(rpes), 1) if rpes else None,
            "first_set_rpe":     rpes[0]  if rpes else None,
            "last_set_rpe":      rpes[-1] if rpes else None,
        })
    con.close()
    return result


def suppressed_session_dates(days: int = 180) -> set:
    """Dates whose prescription imposed an RPE cap / deload / re-entry /
    plateau reset. Sessions on those days had no OPPORTUNITY to progress, so
    a flat e1RM there is the programme's doing, not the athlete's — they must
    not count as plateau evidence (that feedback loop drove the squat
    prescription from 110kg to 80kg over two months)."""
    con = _con()
    try:
        rows = con.execute("""
            SELECT date, reasoning, exercises_json FROM prescribed_sessions
            WHERE date >= date('now', ?)
        """, (f"-{days} days",)).fetchall()
    except sqlite3.OperationalError:
        return set()
    finally:
        con.close()
    pat = re.compile(r"deload|re-?entry|plateau reset|rpe cap", re.I)
    return {r["date"] for r in rows
            if pat.search((r["reasoning"] or "") + " " + (r["exercises_json"] or ""))}


def plateau_detected(history: list[dict], threshold: int = config.PLATEAU_SESSIONS,
                     suppressed: Optional[set] = None) -> bool:
    """True if e1RM hasn't improved over the last `threshold` sessions in
    which the athlete actually HAD the chance to progress (capped/deloaded
    sessions excluded — see suppressed_session_dates)."""
    if suppressed:
        history = [h for h in history if h.get("date") not in suppressed]
    if len(history) < threshold:
        return False
    # Stale evidence expires: if the athlete hasn't had a real shot at this
    # lift in over a month (everything since was capped/deloaded), a months-old
    # flat spell says nothing about today — let re-entry rules handle it.
    newest = history[0].get("date")
    if newest and (date.today() - date.fromisoformat(newest)).days > 35:
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


def days_since_any_session() -> Optional[int]:
    """Days since the most recent session of any type (None if never trained)."""
    con = _con()
    row = con.execute(
        "SELECT MAX(date) AS d FROM sets WHERE session_type != 'unknown'"
    ).fetchone()
    con.close()
    if not row or not row["d"]:
        return None
    return (date.today() - date.fromisoformat(row["d"])).days


def consecutive_training_days() -> int:
    """
    Count consecutive days of training load ending yesterday.
    Counts both gym sessions (sets table) and scheduled recurring activity days
    (e.g. wrestling Thursdays) — both impose fatigue.
    """
    con = _con()
    rows = con.execute("""
        SELECT DISTINCT date FROM sets ORDER BY date DESC LIMIT 14
    """).fetchall()
    con.close()
    dates = {date.fromisoformat(r["date"]) for r in rows}
    try:
        from activities import load_activities
        activity_weekdays = {a["weekday"] for a in load_activities()}
    except Exception:
        activity_weekdays = set()
    count = 0
    check = date.today() - timedelta(days=1)
    while check in dates or check.weekday() in activity_weekdays:
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


def recently_trained_exercises(days: int = 4) -> dict[str, int]:
    """
    Exercises actually LOGGED within the last `days` days, mapped to how many
    days ago they most recently occurred. Feeds the deterministic no-repeat guard
    so the same accessory can't land on back-to-back sessions.

    Recency is keyed on logged sets only — a *prescribed* exercise the athlete
    skipped (or replaced) must not count as trained, or it gets wrongly gated out
    of future sessions.
    """
    today  = date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    out: dict[str, int] = {}
    con = _con()
    try:
        rows = con.execute("""
            SELECT exercise, MAX(date) AS d FROM sets
            WHERE is_warmup = 0 AND date >= ?
            GROUP BY exercise
        """, (cutoff,)).fetchall()
        for r in rows:
            if r["exercise"] and r["d"]:
                out[r["exercise"]] = (today - date.fromisoformat(r["d"])).days
    finally:
        con.close()
    return out


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


def composition_history() -> dict:
    """
    Latest + previous non-null values per composition column, with dates.
    Independent per-column because many weigh-ins lack muscle/body-fat data.
    Returns {weight: {latest: {date, value}, previous: {date, value}}, muscle: {...}, body_fat: {...}}.
    """
    con = _con()
    def _at(col: str, offset: int) -> Optional[dict]:
        row = con.execute(
            f"SELECT date, {col} AS v FROM bodyweight "
            f"WHERE {col} IS NOT NULL ORDER BY date DESC LIMIT 1 OFFSET ?",
            (offset,)
        ).fetchone()
        return {"date": row["date"], "value": row["v"]} if row else None
    result = {
        col: {"latest": _at(sql, 0), "previous": _at(sql, 1)}
        for col, sql in (("weight", "weight_kg"),
                         ("muscle", "muscle_mass_kg"),
                         ("body_fat", "body_fat_pct"))
    }
    con.close()
    return result


def recent_bodyweight(days: int = 14) -> list[dict]:
    """Return [{date, weight_kg}] for the last N days (newest first)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT date, weight_kg FROM bodyweight
        WHERE date >= ? AND weight_kg IS NOT NULL
        ORDER BY date DESC
    """, (since,)).fetchall()
    con.close()
    return [{"date": r["date"], "weight_kg": r["weight_kg"]} for r in rows]


def bodyweight_avg(days: int = 7) -> Optional[float]:
    """Average bodyweight over the last N days. Smooths daily noise."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    row = con.execute("""
        SELECT ROUND(AVG(weight_kg), 2) AS avg_kg
        FROM bodyweight WHERE date >= ? AND weight_kg IS NOT NULL
    """, (since,)).fetchone()
    con.close()
    return row["avg_kg"] if row and row["avg_kg"] else None


def bodyweight_trend(days: int = 30) -> Optional[float]:
    """Simple linear trend in kg/week over last N days. Negative = losing weight."""
    return _composition_slope("weight_kg", days)


def _composition_slope(column: str, days: int) -> Optional[float]:
    """Generic least-squares slope (per week) for a bodyweight-table column."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute(
        f"SELECT date, {column} AS v FROM bodyweight "
        f"WHERE date >= ? AND {column} IS NOT NULL ORDER BY date",
        (since,)
    ).fetchall()
    con.close()
    if len(rows) < 4:
        return None
    xs = [(date.fromisoformat(r["date"]) - date.fromisoformat(rows[0]["date"])).days
          for r in rows]
    ys = [r["v"] for r in rows]
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxy, sx2 = sum(x*y for x, y in zip(xs, ys)), sum(x**2 for x in xs)
    slope_per_day = (n*sxy - sx*sy) / (n*sx2 - sx**2 + 1e-9)
    return round(slope_per_day * 7, 3)


def body_composition_trends(days: int = 30) -> dict:
    """
    Returns trends for weight, muscle mass, and body fat % over the window.
    All slopes are per week. Useful for judging bulk/cut quality.
    """
    return {
        "weight_kg_per_week":     _composition_slope("weight_kg",      days),
        "muscle_kg_per_week":     _composition_slope("muscle_mass_kg", days),
        "body_fat_pct_per_week":  _composition_slope("body_fat_pct",   days),
        "window_days":            days,
    }


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


def recurring_activity_role(d: Optional[date] = None) -> dict:
    """
    Classify `d` (default today) against config.RECURRING_ACTIVITIES.
    Returns {"role": "on"|"pre"|"post"|"none", "activity": dict|None,
             "days_until": int|None, "days_since": int|None}.
    'pre'/'post' only triggered within the configured buffer window.
    """
    d = d or date.today()
    try:
        from activities import load_activities
        activities = load_activities()
    except Exception:
        activities = []
    for act in activities:
        wd = act["weekday"]
        if d.weekday() == wd:
            return {"role": "on", "activity": act, "days_until": 0, "days_since": 0}
        # days until next occurrence (1..7)
        days_until = (wd - d.weekday()) % 7
        if days_until == 0:
            days_until = 7
        if days_until <= act.get("pre_buffer_days", 0):
            return {"role": "pre", "activity": act, "days_until": days_until, "days_since": None}
        # days since most recent occurrence (1..7)
        days_since = (d.weekday() - wd) % 7
        if days_since == 0:
            days_since = 7
        if days_since <= act.get("post_buffer_days", 0):
            return {"role": "post", "activity": act, "days_until": None, "days_since": days_since}
    return {"role": "none", "activity": None, "days_until": None, "days_since": None}


def pick_session_type(days_since: dict, last_type: Optional[str],
                      safe_types: Optional[list] = None) -> str:
    """
    Pure session-type chooser: most overdue recovered type, cycle order as
    tiebreaker, excluding the last type to avoid back-to-back repeats. Works off
    the supplied state only (no DB) so the live path and the forward projection
    in plan.py share identical logic.

      days_since: {session_type: days since last trained, or None if never}
      safe_types: restrict to these (recurring-activity buffer days), if given
    """
    cycle = config.SESSION_CYCLE
    candidates = [t for t in cycle if t != last_type]

    if safe_types:
        narrowed = [t for t in candidates if t in safe_types]
        if narrowed:
            candidates = narrowed

    recovered = [t for t in candidates
                 if days_since.get(t) is None or days_since.get(t) >= config.MIN_RECOVERY_DAYS]
    if not recovered:
        recovered = candidates  # nothing fully recovered — pick least-recently-trained

    def _sort_key(t):
        days = days_since.get(t)
        days_val = days if days is not None else 9999
        cycle_pos = cycle.index(t) if t in cycle else 99
        return (-days_val, cycle_pos)   # most overdue first, cycle order breaks ties

    return sorted(recovered, key=_sort_key)[0]


def suggest_session_type() -> str:
    """
    Picks today's session type from live DB state (see pick_session_type).
    On pre/post buffer days of a recurring activity, restricts to safe_session_types.
    """
    days_since = {t: days_since_session_type(t) for t in config.SESSION_CYCLE}
    role = recurring_activity_role()
    safe = None
    if role["role"] in ("pre", "post"):
        safe = role["activity"].get("safe_session_types") or None
    return pick_session_type(days_since, last_session_type(), safe)


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
        # Recency for priority is keyed on logged sets only — a prescribed-but-
        # skipped exercise must not read as "recently trained" and sink down the
        # roster, or movements the athlete never actually did get gated out.
        last_sets = con.execute("""
            SELECT MAX(date) FROM sets WHERE exercise = ? AND is_warmup != 1
        """, (name,)).fetchone()[0]
        if not last_sets:
            return None
        return (today - date.fromisoformat(last_sets)).days

    # Build movement_pattern lookup from exercises.json
    from exercise_lib import all_exercises as _all_exercises
    _ex_db = _all_exercises()
    _mp_by_canonical: dict[str, str] = {}
    _compound_by_canonical: dict[str, bool] = {}
    for _ex in _ex_db.values():
        _name = _ex.get("canonical", "")
        _mp_by_canonical[_name] = _ex.get("movement_pattern", "")
        _compound_by_canonical[_name] = bool(_ex.get("is_compound", True))

    result: list[dict] = []
    seen: set[str] = set()

    # Novelty 2 (explore): accessories the athlete has barely trained get a
    # bounded priority boost so unfamiliar movements surface more often. Still
    # slot/pattern-constrained downstream — never random.
    novelty = int(getattr(config, "EXERCISE_NOVELTY", 1))
    exposure: dict[str, int] = {}
    if novelty >= 2:
        try:
            exposure = {r["exercise"]: r["n"] for r in con.execute(
                "SELECT exercise, COUNT(DISTINCT date) AS n FROM sets "
                "WHERE is_warmup = 0 GROUP BY exercise")}
        except sqlite3.OperationalError:
            pass

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
        pr = min(raw, cap)
        if novelty >= 2 and not ex["is_main_lift"] and exposure.get(name, 0) <= 2:
            pr = min(pr * 1.3 + 0.3, cap + 0.5)
        result.append({
            "exercise_name":    name,
            "is_main_lift":     bool(ex["is_main_lift"]),
            "target_freq_days": ex["target_freq_days"],
            "days_since_last":  days,
            "star_rating":      star,
            "priority":         round(pr, 2),
            "movement_pattern": _mp_by_canonical.get(name, ""),
            "is_compound":      _compound_by_canonical.get(name, True),
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

    # ── Supplement with historically-preferred exercises missing from roster ──
    # Any exercise done 3+ times in this session_type that isn't already listed
    # gets added at star=2 priority so established preferences always surface.
    try:
        hist_rows = con.execute("""
            SELECT exercise, COUNT(DISTINCT date) AS sessions
            FROM sets
            WHERE session_type = ? AND is_warmup = 0
            GROUP BY exercise
            HAVING COUNT(DISTINCT date) >= 3
        """, (session_type,)).fetchall()

        for h in hist_rows:
            name = h["exercise"]
            if name in seen or name in suspended:
                continue
            days = _days_since(name)
            sessions = int(h["sessions"])
            days_val = days if days is not None else 21
            raw  = days_val / 21.0
            # Preference boost: established exercises sort ahead of untested ones.
            # +0.02 per 10 sessions (capped at +0.2), keeping well below star=3 threshold.
            pref_boost = min(sessions / 500.0, 0.2)
            cap  = STAR_CAP.get(2, 1.0) + pref_boost
            result.append({
                "exercise_name":    name,
                "is_main_lift":     name in config.MAIN_LIFTS,
                "target_freq_days": 21,
                "days_since_last":  days,
                "star_rating":      2,
                "priority":         round(min(raw + pref_boost, cap), 2),
                "session_count":    sessions,
                "movement_pattern": _mp_by_canonical.get(name, ""),
                "is_compound":      _compound_by_canonical.get(name, True),
            })
            seen.add(name)
    except sqlite3.OperationalError:
        pass

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
    Returns all sessions in the last N days, each with full exercise data.
    Each exercise carries the full per-set vector (`sets`) plus the same
    summary fields used by lift_history. Sorted newest first.
    """
    from collections import Counter
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
        # All working sets for this date, in set order. Group by exercise in Python.
        rows = con.execute("""
            SELECT exercise, weight_kg, reps, rpe, e1rm, set_number
            FROM sets
            WHERE date = ? AND is_warmup = 0 AND session_type != 'unknown' AND reps > 0
            ORDER BY set_number
        """, (dr["date"],)).fetchall()
        by_ex: dict[str, list] = {}
        for row in rows:
            by_ex.setdefault(row["exercise"], []).append(row)

        exercises = []
        for ex_name, ex_rows in by_ex.items():
            sets = [{"weight": r["weight_kg"], "reps": r["reps"], "rpe": r["rpe"]} for r in ex_rows]
            top = max(ex_rows, key=lambda x: (
                x["e1rm"] if x["e1rm"] is not None else -1,
                x["weight_kg"] or 0,
                x["reps"] or 0,
            ))
            wc = Counter(r["weight_kg"] for r in ex_rows)
            working_w = max(wc.items(), key=lambda kv: (kv[1], kv[0]))[0]
            reps_at_w = [r["reps"] for r in ex_rows if r["weight_kg"] == working_w]
            rc = Counter(reps_at_w)
            working_r = max(rc.items(), key=lambda kv: (kv[1], kv[0]))[0]
            rpes = [r["rpe"] for r in ex_rows if r["rpe"] is not None]
            exercises.append({
                "exercise":          ex_name,
                "sets":              sets,
                "set_count":         len(ex_rows),
                "top_weight_kg":     top["weight_kg"],
                "top_reps":          top["reps"],
                "working_weight_kg": working_w,
                "working_reps":      working_r,
                "avg_rpe":           round(sum(rpes) / len(rpes), 1) if rpes else None,
                "first_set_rpe":     rpes[0]  if rpes else None,
                "last_set_rpe":      rpes[-1] if rpes else None,
            })
        sessions.append({
            "date":         dr["date"],
            "session_type": dr["session_type"],
            "exercises":    exercises,
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



# ── Fatigue / rest-day signal ─────────────────────────────────────────────

_FATIGUE_NOTE_KEYWORDS = (
    "wrecked", "exhausted", "fried", "drained", "burnt out", "burned out",
    "shattered", "destroyed", "tired", "knackered",
    "pain", "ache", "achy", "sore", "tight", "twinge", "tweaked", "strained",
    "no sleep", "didn't sleep", "bad sleep", "poor sleep",
    "sick", "ill", "flu", "cold",
)


def _last_session_rpe_metrics(stale_after_days: int = 4) -> dict:
    """
    RPE metrics from the most recent session: avg first-set RPE, avg last-set RPE,
    and avg fatigue diff (last - first). Returns Nones if session is stale (>N days old)
    or no RPE was logged.
    """
    result = {"first_avg": None, "last_avg": None, "diff_avg": None}
    workouts = recent_workouts(days=14)
    if not workouts:
        return result
    last = workouts[0]
    age_days = (date.today() - date.fromisoformat(last["date"])).days
    if age_days > stale_after_days:
        return result
    main_set = set(config.MAIN_LIFTS.keys())
    candidates = [e for e in last["exercises"] if e["exercise"] in main_set]
    if not candidates:
        candidates = last["exercises"]
    firsts = [e["first_set_rpe"] for e in candidates if e.get("first_set_rpe") is not None]
    lasts  = [e["last_set_rpe"]  for e in candidates if e.get("last_set_rpe")  is not None]
    diffs  = [e["last_set_rpe"] - e["first_set_rpe"] for e in candidates
              if e.get("first_set_rpe") is not None and e.get("last_set_rpe") is not None]
    if firsts: result["first_avg"] = round(sum(firsts) / len(firsts), 2)
    if lasts:  result["last_avg"]  = round(sum(lasts)  / len(lasts),  2)
    if diffs:  result["diff_avg"]  = round(sum(diffs)  / len(diffs),  2)
    return result


def _last_session_avg_last_set_rpe(stale_after_days: int = 4) -> Optional[float]:
    """Legacy helper retained for callers; delegates to _last_session_rpe_metrics."""
    workouts = recent_workouts(days=14)
    if not workouts:
        return None
    last = workouts[0]
    age_days = (date.today() - date.fromisoformat(last["date"])).days
    if age_days > stale_after_days:
        return None
    main_set = set(config.MAIN_LIFTS.keys())
    rpes = [
        e["last_set_rpe"] for e in last["exercises"]
        if e["exercise"] in main_set and e.get("last_set_rpe") is not None
    ]
    if not rpes:
        rpes = [e["last_set_rpe"] for e in last["exercises"] if e.get("last_set_rpe") is not None]
    if not rpes:
        return None
    return round(sum(rpes) / len(rpes), 2)


def _focus_lift_short_slope(days: int = 14) -> Optional[float]:
    """e1RM kg/week slope over last `days` for the lift with most recent activity."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    best: tuple[Optional[float], int] = (None, 0)
    for lift in config.MAIN_LIFTS:
        history = [h for h in lift_history(lift, n=20) if h["date"] >= cutoff and h.get("best_e1rm")]
        if len(history) < 2:
            continue
        xs = [(date.fromisoformat(h["date"]) - date.fromisoformat(history[-1]["date"])).days for h in history]
        ys = [h["best_e1rm"] for h in history]
        n = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom == 0:
            continue
        slope_per_day = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
        slope_per_week = slope_per_day * 7
        if n > best[1]:
            best = (round(slope_per_week, 2), n)
    return best[0]


def _notes_flag(days: int = 3) -> tuple[bool, list[str]]:
    """True if any note in the last `days` mentions a fatigue keyword."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute(
        "SELECT date, note FROM session_notes WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()
    con.close()
    hits = []
    for r in rows:
        text = (r["note"] or "").lower()
        for kw in _FATIGUE_NOTE_KEYWORDS:
            if kw in text:
                hits.append(f"{r['date']}: {kw}")
                break
    return (bool(hits), hits)


def _predicted_rpe(e1: float, weight: float, reps: int) -> Optional[float]:
    """Expected RPE at (weight, reps) from an e1RM capability anchor — same
    Epley inversion the workout page shows as ~RPE."""
    if not e1 or weight <= 0 or reps <= 0 or weight >= e1:
        return None
    reps_to_failure = 30.0 * (e1 / weight - 1.0)
    pred = 10.0 - (reps_to_failure - reps)
    return max(4.0, min(10.0, pred))


def _acwr() -> dict:
    """Acute:chronic workload ratio on working-set tonnage — 7-day load vs the
    28-day daily average. The best-evidenced single marker of accumulated
    training stress; fully objective. Abstains without a chronic base."""
    con = _con()
    rows = con.execute("""
        SELECT date, SUM(weight_kg * reps) AS load
        FROM sets WHERE is_warmup = 0 AND reps > 0
              AND weight_kg <= 350 AND reps <= 50   -- fat-finger guard
              AND date >= date('now', '-28 days')
        GROUP BY date
    """).fetchall()
    con.close()
    acute_cut = (date.today() - timedelta(days=7)).isoformat()
    acute = sum(r["load"] or 0 for r in rows if r["date"] >= acute_cut)
    chronic = sum(r["load"] or 0 for r in rows)
    if chronic < 1000 or len(rows) < 6:          # thin history: abstain
        return {"ratio": None, "acute_7d": round(acute), "sessions_28d": len(rows)}
    weekly_chronic = chronic / 4.0
    ratio = acute / weekly_chronic if weekly_chronic > 0 else None
    return {"ratio": round(ratio, 2) if ratio is not None else None,
            "acute_7d": round(acute), "sessions_28d": len(rows)}


def _deload_active() -> bool:
    """True when the latest prescription is a deliberate deload / re-entry /
    plateau reset — its reduced loads must not be re-read as fatigue."""
    con = _con()
    row = con.execute("""
        SELECT exercises_json, reasoning FROM prescribed_sessions
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    con.close()
    if not row:
        return False
    blob = ((row["reasoning"] or "") + " " + (row["exercises_json"] or "")).lower()
    return bool(re.search(r"deload|re-?entry|plateau reset|rpe cap|caution", blob))


def _rep_shortfall(days: int = 4) -> dict:
    """Objective under-performance: prescribed reps vs reps actually logged at
    ~the prescribed load, over the last few days. No self-report involved."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    pres = con.execute("""
        SELECT date, exercises_json FROM prescribed_sessions
        WHERE date >= ? ORDER BY date
    """, (cutoff,)).fetchall()
    out = {"prescribed_reps": 0, "achieved_reps": 0, "exercises": 0}
    for p in pres:
        try:
            exercises = json.loads(p["exercises_json"])
        except Exception:
            continue
        for ex in exercises:
            name = (ex.get("exercise_name") or "").strip()
            psets = [st for st in (ex.get("sets") or [])
                     if not st.get("is_warmup") and (st.get("reps") or 0) > 0]
            if not name or not psets:
                continue
            logged = con.execute("""
                SELECT weight_kg, reps FROM sets
                WHERE date = ? AND exercise = ? AND is_warmup = 0 AND reps > 0
            """, (p["date"], name)).fetchall()
            if not logged:
                continue                         # skipped/swapped: not fatigue evidence
            # Pair prescribed and logged sets one-to-one per weight bucket —
            # crediting every prescribed set with the BEST logged set hid
            # sessions where half the sets collapsed (stress-test finding).
            matched_p, matched_a = 0, 0
            buckets: dict = {}
            for st in psets:
                pw = float(st.get("weight_kg") or 0)
                buckets.setdefault(pw, []).append(int(st.get("reps") or 0))
            for pw, preps in buckets.items():
                near = sorted((r["reps"] for r in logged
                               if abs((r["weight_kg"] or 0) - pw) <= max(2.5, pw * 0.075)),
                              reverse=True)
                if not near:
                    continue                     # skipped/swapped: not fatigue evidence
                for i, pr in enumerate(sorted(preps, reverse=True)):
                    if i >= len(near):
                        break                    # fewer sets done: ambiguous (time), skip
                    matched_p += pr
                    matched_a += min(pr, near[i])
            if matched_p:
                out["prescribed_reps"] += matched_p
                out["achieved_reps"] += matched_a
                out["exercises"] += 1
    con.close()
    if out["prescribed_reps"]:
        out["shortfall_pct"] = round(
            100.0 * (1 - out["achieved_reps"] / out["prescribed_reps"]), 1)
    else:
        out["shortfall_pct"] = None
    return out


def _rpe_deviation(window_days: int = 7, baseline_days: int = 90,
                   min_sets: int = 6) -> dict:
    """Debiased RPE drift: logged minus predicted RPE at the same load,
    minus the athlete's own per-exercise baseline offset. Stable biases (the
    athlete always logs hot, the model always predicts one exercise low)
    cancel; what remains is transient — i.e. fatigue. Warm-ups and failure
    sets excluded; abstains below `min_sets` observations in the window."""
    base_cut = (date.today() - timedelta(days=baseline_days)).isoformat()
    win_cut = (date.today() - timedelta(days=window_days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT date, exercise, weight_kg, reps, rpe
        FROM sets
        WHERE is_warmup = 0 AND reps > 0 AND rpe IS NOT NULL
              AND COALESCE(set_type, 'normal') NOT IN ('failure')
              AND date >= ?
        ORDER BY date
    """, (base_cut,)).fetchall()
    # capability anchor per exercise: best e1RM in the window before each set's
    # date would be ideal; a single all-window best is a reasonable, stable
    # approximation for deviation BASELINES (transients ride on top of it)
    e1 = {r["exercise"]: None for r in rows}
    hist = con.execute("""
        SELECT exercise, MAX(e1rm) AS best FROM sets
        WHERE is_warmup = 0 AND e1rm IS NOT NULL AND date >= ?
        GROUP BY exercise
    """, (base_cut,)).fetchall()
    con.close()
    for h in hist:
        e1[h["exercise"]] = h["best"]

    devs_by_ex: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        anchor = e1.get(r["exercise"])
        pred = _predicted_rpe(anchor, float(r["weight_kg"] or 0), int(r["reps"]))
        if pred is None:
            continue
        devs_by_ex.setdefault(r["exercise"], []).append(
            (r["date"], float(r["rpe"]) - pred))

    def median(xs):
        xs = sorted(xs)
        n = len(xs)
        return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)

    recent, n_recent = [], 0
    for ex, pairs in devs_by_ex.items():
        base_devs = [d for (dt, d) in pairs if dt < win_cut]
        base = median(base_devs) if len(base_devs) >= 4 else 0.0
        for dt, d in pairs:
            if dt >= win_cut:
                recent.append(d - (base or 0.0))
                n_recent += 1
    if n_recent < min_sets:
        return {"drift": None, "sets": n_recent}
    return {"drift": round(median(recent), 2), "sets": n_recent}


def fatigue_score() -> dict:
    """
    Composite fatigue score 0-100 with component breakdown.
    >=60: rest strongly recommended; 40-59: caution; <40: fine.

    Objective signals carry the weight (workload ratio, rep shortfall);
    debiased RPE drift corroborates; components ABSTAIN (contribute nothing,
    say so) when their data is too thin, and deliberate deloads are flagged
    so reduced loads are never re-read as decline.
    """
    components: dict = {}
    score = 0.0

    # ── Workload: acute vs chronic tonnage (cap 30) ──
    acwr = _acwr()
    components["acwr"] = acwr["ratio"]
    components["acute_load_7d"] = acwr["acute_7d"]
    pts = 0.0
    if acwr["ratio"] is not None and acwr["ratio"] > 1.2:
        pts = min(30.0, (acwr["ratio"] - 1.2) / 0.6 * 30)
    score += pts
    components["acwr_points"] = round(pts, 1)

    # ── Objective under-performance: rep shortfall vs prescription (cap 25) ──
    sf = _rep_shortfall()
    components["rep_shortfall_pct"] = sf["shortfall_pct"]
    pts = 0.0
    if sf["shortfall_pct"] is not None and sf["shortfall_pct"] >= 5:
        pts = min(25.0, (sf["shortfall_pct"] - 5) * 2.0)
    score += pts
    components["shortfall_points"] = round(pts, 1)

    # ── Debiased RPE drift vs the athlete's own baseline (cap 20) ──
    drift = _rpe_deviation()
    components["rpe_drift"] = drift["drift"]
    components["rpe_drift_sets"] = drift["sets"]
    pts = 0.0
    if drift["drift"] is not None and drift["drift"] >= 0.5:
        pts = min(25.0, (drift["drift"] - 0.25) * 16)
    score += pts
    components["rpe_drift_points"] = round(pts, 1)

    # ── Consecutive training days — only streaks beyond 2 count (cap 15) ──
    consec = consecutive_training_days()
    components["consecutive_training_days"] = consec
    span = max(1, config.MAX_CONSECUTIVE_DAYS - 2)
    pts = min(15.0, max(0, consec - 2) / span * 15)
    score += pts
    components["consecutive_points"] = round(pts, 1)

    # ── Self-reported trouble in notes (cap 5) ──
    flagged, hits = _notes_flag(days=3)
    components["notes_flagged"] = flagged
    components["notes_hits"] = hits
    score += 5.0 if flagged else 0.0
    components["notes_points"] = 5.0 if flagged else 0.0

    components["deload_active"] = _deload_active()

    score = round(score, 1)
    if score >= 60:
        verdict = "rest"
    elif score >= 40:
        verdict = "caution"
    else:
        verdict = "ok"

    return {"score": score, "verdict": verdict, "components": components}


# ── Movement coverage audit ───────────────────────────────────────────────

def movement_coverage_audit(days: int = 14) -> dict:
    """
    Check which movement patterns are absent from the last N days of training.
    Returns:
      {
        "covered":  {pattern: [exercise, ...]},   # patterns trained
        "gaps":     [pattern, ...],               # patterns not trained
        "by_session_type": {session_type: {pattern: [exercise, ...]}},
      }
    """
    from exercise_lib import all_exercises, VALID_MOVEMENT_PATTERNS

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    con = _con()
    rows = con.execute("""
        SELECT DISTINCT exercise FROM sets
        WHERE date >= ? AND is_warmup = 0
    """, (cutoff,)).fetchall()
    con.close()

    trained_names = {r["exercise"] for r in rows}

    # Build canonical → taxonomy map from exercises.json
    ex_db = all_exercises()
    name_to_meta: dict[str, dict] = {}
    for ex in ex_db.values():
        name_to_meta[ex["canonical"].lower()] = ex
        for alias in ex.get("aliases", []):
            name_to_meta[alias.lower()] = ex

    covered: dict[str, list[str]] = {}
    for name in trained_names:
        meta = name_to_meta.get(name.lower())
        if not meta:
            continue
        mp = meta.get("movement_pattern", "")
        if mp and mp in VALID_MOVEMENT_PATTERNS:
            covered.setdefault(mp, []).append(name)

    gaps = sorted(VALID_MOVEMENT_PATTERNS - set(covered.keys()))

    # Per-session-type breakdown using recent_workouts
    by_session_type: dict[str, dict[str, list[str]]] = {}
    since_cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = recent_workouts(days=days)
    for session in recent:
        stype = session["session_type"]
        if stype not in by_session_type:
            by_session_type[stype] = {}
        for ex_row in session["exercises"]:
            ex_name = ex_row["exercise"]
            meta = name_to_meta.get(ex_name.lower())
            if not meta:
                continue
            mp = meta.get("movement_pattern", "")
            if mp and mp in VALID_MOVEMENT_PATTERNS:
                by_session_type[stype].setdefault(mp, []).append(ex_name)

    return {
        "covered": covered,
        "gaps": gaps,
        "by_session_type": by_session_type,
    }


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

def recent_chat_messages(hours: int = 48, limit: int = 15) -> list[dict]:
    """Messages the athlete sent in the web chat (chat_server.py) recently.

    This is how wellness signals mentioned in chat — illness, poor sleep,
    injuries, time constraints — reach the prescription engine. The table
    may not exist for users who have never chatted.
    """
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    con = _con()
    try:
        rows = con.execute("""
            SELECT ts, content FROM chat_messages
            WHERE role = 'user' AND ts >= ?
            ORDER BY id DESC LIMIT ?
        """, (cutoff, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [{"ts": r["ts"], "message": r["content"][:500]} for r in reversed(rows)]


def weight_availability_lines(max_prescriptions: int = 10) -> list[str]:
    """Observed loadable weights (the gym's real racks/stacks, learned from
    logs) plus any recently prescribed weights that keep missing them — for
    the prescription prompt, so the model only writes loadable numbers."""
    import stats
    try:
        avail = stats.achievable_weights(db_path=str(config.DB_PATH))
    except Exception:
        return []
    if not avail:
        return []

    def fmt(w):
        return str(int(w)) if float(w).is_integer() else str(w)

    lines = []
    for key in sorted(avail, key=lambda k: (k.startswith("exercise:"), k)):
        v = avail[key]
        w = v["weights"]
        label = key.split(":", 1)[1]
        span = f"{fmt(w[0])}-{fmt(w[-1])}kg"
        if len(w) <= 8:
            lines.append(f"{label}: observed {{{', '.join(fmt(x) for x in w)}}}kg")
        else:
            step = f"~{fmt(v['step'])}kg steps, " if v["step"] else ""
            lines.append(f"{label}: {step}{span} observed")

    # Prescribed weights that repeatedly miss the observed set (inside its
    # range) are probably not loadable in this gym — say so explicitly.
    try:
        con = _con()
        rows = con.execute("""
            SELECT exercises_json FROM prescribed_sessions
            ORDER BY date DESC LIMIT ?""", (max_prescriptions,)).fetchall()
        con.close()
        misses: dict[str, int] = {}
        for r in rows:
            for ex in json.loads(r[0] if not isinstance(r, sqlite3.Row) else r["exercises_json"]):
                name = (ex.get("exercise_name") or "").strip()
                eq = (ex.get("equipment_category") or "").strip()
                scope = (avail.get(f"exercise:{name}")
                         or (avail.get(f"equipment:{eq}")
                             if eq in ("barbell", "dumbbell", "kettlebell", "plate") else None))
                if not scope or len(scope["weights"]) < 3:
                    continue
                ws = scope["weights"]
                tol = (scope["step"] or 2.5) / 4
                for st in ex.get("sets") or []:
                    w = st.get("weight_kg")
                    if not isinstance(w, (int, float)) or w <= 0:
                        continue
                    if ws[0] <= w <= ws[-1] and all(abs(w - x) > tol for x in ws):
                        k = f"{fmt(w)}kg ({name})"
                        misses[k] = misses.get(k, 0) + 1
        for k, n in sorted(misses.items(), key=lambda kv: -kv[1]):
            if n >= 2:
                lines.append(f"AVOID {k}: prescribed {n}x recently but never "
                             f"logged - likely not loadable here")
    except Exception:
        pass
    return lines


def build_context(session_type: Optional[str] = None) -> dict:
    """
    Returns a dict with all context needed by Claude to prescribe today's workout.
    Also serialisable to JSON for logging / debugging.

    `session_type` pins the session to an explicitly-requested type (e.g. the
    athlete asked the coach for push) instead of the reactive suggestion — every
    dependent field (template, focus lift, recovery numbers) follows it.
    """
    today      = date.today().isoformat()
    is_weekend = date.today().weekday() >= 5
    if session_type not in config.SESSION_CYCLE:
        session_type = suggest_session_type()

    # Build per-lift context for main lifts
    lifts_context = {}
    suppressed = suppressed_session_dates()
    for lift_name, cfg in config.MAIN_LIFTS.items():
        history = lift_history(lift_name, n=10)
        days_ago = days_since_last(lift_name)
        plateau = plateau_detected(history, suppressed=suppressed)
        is_bw = cfg.get("is_bodyweight", False)

        lifts_context[lift_name] = {
            "session_type": cfg["session_type"],
            "days_since_last_session": days_ago,
            "is_bodyweight": is_bw,
            "plateau_detected": plateau,
            "target_sets": cfg["target_sets"],
            "rep_range": cfg["rep_range"],
            "progression_step_kg": cfg["progression_kg"],
            "recent_sessions": history[:10],   # last 10 for Claude context
        }

    bw = latest_bodyweight()
    comp_history = composition_history()
    bw_trend = bodyweight_trend()
    bw_history = recent_bodyweight(days=14)
    bw_avg_7 = bodyweight_avg(days=7)
    comp_trends = body_composition_trends(days=30)
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

    try:
        import plan
        projected = plan.project_sessions(6)
    except Exception:
        projected = []

    return {
        "today": today,
        "is_weekend": is_weekend,
        "suggested_session_type": session_type,
        "bodyweight_kg":      bw["weight_kg"]      if bw else None,
        "bodyweight_date":    bw["date"]           if bw else None,
        "muscle_mass_kg":     bw["muscle_mass_kg"] if bw else None,
        "body_fat_pct":       bw["body_fat_pct"]   if bw else None,
        "composition_history": comp_history,
        "bodyweight_trend_kg_per_week": bw_trend,
        "bodyweight_avg_7d":   bw_avg_7,
        "bodyweight_history":  bw_history,
        "body_composition_trends": comp_trends,
        "sessions_last_7_days": len(recent_sessions),
        "session_dates_last_7_days": recent_sessions,
        "session_balance_last_28_days": balance,
        "last_session_type": last_session_type(),
        "last_session_exercises": last_session_exercises(),
        "weight_availability": weight_availability_lines(),
        "recently_trained_exercises": recently_trained_exercises(days=config.MIN_ACCESSORY_REPEAT_DAYS + 1),
        "projected_sessions": projected,
        "next_session_type": projected[1] if len(projected) > 1 else None,
        "recent_workouts":        recent_workouts(days=28),
        "exercise_stats":         exercise_stats_all_time(),
        "main_lifts": lifts_context,
        "exercise_priorities": priorities,
        "days_since_last_session_of_type": days_since_this_type,
        "recent_workout_feedback":         feedback,
        "session_notes":                   notes,
        "recent_chat_messages":            recent_chat_messages(),
        "focus_phase":                     phase,
        "creator_recommendations":         creator_recs,
        "movement_coverage":               movement_coverage_audit(days=14),
        "fatigue":                         fatigue_score(),
        "days_since_any_session":          days_since_any_session(),
        "recurring_activity":              recurring_activity_role(),
    }


if __name__ == "__main__":
    ctx = build_context()
    print(json.dumps(ctx, indent=2, default=str))
