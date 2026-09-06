"""
stats.py — read-only workout stats for the web stats view.

Standalone like profile_editor: takes the user name and opens their gym.db
directly, so it never touches config's process-global active user (the chat
server serves several users under one process).

Everything here is derived from the `sets` table (Hevy-synced training log)
plus the live anchor state in focus_lift_phases. No writes.
"""
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import profile_editor

_USERS_ROOT = Path(__file__).parent / "users"

SESSION_TYPES = ("push", "pull", "legs", "arms")

# Palette shared with the frontend legend/calendar (kept here so the API is the
# single source of truth for colours).
# WipEout/tDR neon palette — vivid and distinct on the dark HUD background.
SESSION_COLOURS = {
    "push": "#00e5ff",   # cyan
    "pull": "#b6ff00",   # lime
    "legs": "#ff6a00",   # orange
    "arms": "#ff0066",   # magenta
}


def _con(user: str) -> sqlite3.Connection:
    db = _USERS_ROOT / user / "gym.db"
    if not db.is_file():
        raise FileNotFoundError(f"no gym.db for user {user!r}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def _anchor_lifts(user: str) -> dict[str, str]:
    """session_type -> current anchor (focus) lift name, from the live phase row,
    falling back to the profile's DEFAULT_FOCUS_LIFTS."""
    phases = profile_editor.focus_phase_state(user)
    profile = profile_editor.read_profile(user)
    defaults = profile.get("default_focus_lifts", {})
    out = {}
    for st in SESSION_TYPES:
        live = phases.get(st)
        name = (live or {}).get("focus_lift") or defaults.get(st)
        if name:
            out[st] = name
    return out


# Epley/Brzycki e1RM estimates are only meaningful to ~15 reps; beyond that a
# mistyped set (e.g. 90kg × 60) yields a garbage e1RM that would swamp the chart.
_E1RM_MAX_REPS = 15


def _e1rm_series(con: sqlite3.Connection, exercise: str) -> list[dict]:
    """Per-session best e1RM for one exercise, oldest first, nulls dropped.

    Matches the exercise name exactly — the same way the morning engine's
    lift_history / e1rm_trends do — so this chart agrees with the coach's
    numbers rather than silently folding in near-name variants.
    """
    rows = con.execute("""
        SELECT date, MAX(e1rm) AS best_e1rm
        FROM sets
        WHERE exercise = ? AND is_warmup = 0 AND reps > 0 AND reps <= ?
              AND e1rm IS NOT NULL
        GROUP BY date
        ORDER BY date ASC
    """, (exercise, _E1RM_MAX_REPS)).fetchall()
    return [{"date": r["date"], "e1rm": round(r["best_e1rm"], 1)} for r in rows]


def anchor_progress(user: str) -> list[dict]:
    """For each session type, the current anchor lift and its full e1RM series."""
    anchors = _anchor_lifts(user)
    con = _con(user)
    try:
        out = []
        for st in SESSION_TYPES:
            name = anchors.get(st)
            if not name:
                continue
            series = _e1rm_series(con, name)
            latest = series[-1]["e1rm"] if series else None
            best = max((p["e1rm"] for p in series), default=None)
            out.append({
                "session_type": st,
                "lift": name,
                "colour": SESSION_COLOURS[st],
                "series": series,
                "latest_e1rm": latest,
                "best_e1rm": best,
                "sessions": len(series),
            })
        return out
    finally:
        con.close()


def exercise_history(user: str, exercise: str) -> dict:
    """e1RM progression for one exercise, oldest→newest, with the top working
    set behind each session's best e1RM. Same exact-name matching and rep cap
    as _e1rm_series, so it agrees with the coach's numbers and the Stats charts."""
    con = _con(user)
    try:
        rows = con.execute("""
            SELECT date, weight_kg, reps, e1rm
            FROM sets
            WHERE exercise = ? AND is_warmup = 0 AND reps > 0 AND reps <= ?
                  AND e1rm IS NOT NULL
            ORDER BY date ASC, e1rm DESC
        """, (exercise, _E1RM_MAX_REPS)).fetchall()
    finally:
        con.close()

    series, seen = [], set()
    for r in rows:                       # first row per date = that session's best e1RM
        if r["date"] in seen:
            continue
        seen.add(r["date"])
        series.append({
            "date": r["date"],
            "e1rm": round(r["e1rm"], 1),
            "weight": round(r["weight_kg"], 1) if r["weight_kg"] is not None else None,
            "reps": r["reps"],
        })
    latest = series[-1]["e1rm"] if series else None
    best = max((p["e1rm"] for p in series), default=None)
    return {
        "exercise": exercise,
        "series": series,
        "sessions": len(series),
        "first_e1rm": series[0]["e1rm"] if series else None,
        "latest_e1rm": latest,
        "best_e1rm": best,
    }


def calendar_days(user: str, days: int = 365) -> dict[str, str]:
    """{date: session_type} for every trained day in the window (unknown dropped).

    If a date has sets of more than one type (rare), the type with the most
    working sets wins."""
    since = (date.today() - timedelta(days=days)).isoformat()
    con = _con(user)
    try:
        rows = con.execute("""
            SELECT date, session_type, COUNT(*) AS n
            FROM sets
            WHERE session_type != 'unknown' AND session_type IS NOT NULL
              AND date >= ? AND is_warmup = 0
            GROUP BY date, session_type
        """, (since,)).fetchall()
    finally:
        con.close()
    best: dict[str, tuple[int, str]] = {}
    for r in rows:
        cur = best.get(r["date"])
        if cur is None or r["n"] > cur[0]:
            best[r["date"]] = (r["n"], r["session_type"])
    return {d: t for d, (n, t) in best.items()}


def session_detail(user: str, day: str) -> dict | None:
    """The workout trained on `day`: per-exercise sets in the order performed,
    plus headline aggregates (total volume load = Σ weight·reps over working
    sets, working-set count, total reps, top e1RM, heaviest set). None if
    nothing was trained that day. Powers the click-a-day panel on the calendar."""
    con = _con(user)
    try:
        rows = con.execute("""
            SELECT session_id, session_type, workout_name, exercise,
                   is_main_lift, is_bodyweight, is_warmup, set_number,
                   weight_kg, reps, e1rm, rpe
            FROM sets
            WHERE date = ?
            ORDER BY session_id, set_number
        """, (day,)).fetchall()
    finally:
        con.close()
    if not rows:
        return None

    exercises: list[dict] = []
    by_name: dict[str, dict] = {}
    session_types: list[str] = []
    volume = 0.0
    working_sets = 0
    total_reps = 0
    top_e1rm: float | None = None
    top_set: dict | None = None

    for r in rows:
        st = r["session_type"]
        if st and st != "unknown" and st not in session_types:
            session_types.append(st)

        ex = by_name.get(r["exercise"])
        if ex is None:
            ex = {
                "exercise_name": r["exercise"],
                "is_main_lift": bool(r["is_main_lift"]),
                "is_bodyweight": bool(r["is_bodyweight"]),
                "sets": [], "volume_kg": 0.0, "top_e1rm": None,
            }
            by_name[r["exercise"]] = ex
            exercises.append(ex)

        w = float(r["weight_kg"] or 0)
        reps = int(r["reps"] or 0)
        warm = bool(r["is_warmup"])
        e1 = round(r["e1rm"], 1) if r["e1rm"] is not None else None
        ex["sets"].append({"weight_kg": w, "reps": reps, "rpe": r["rpe"],
                           "is_warmup": warm, "e1rm": e1})

        if not warm:
            vol = w * reps
            ex["volume_kg"] += vol
            volume += vol
            working_sets += 1
            total_reps += reps
            if e1 is not None:
                if top_e1rm is None or e1 > top_e1rm:
                    top_e1rm = e1
                if ex["top_e1rm"] is None or e1 > ex["top_e1rm"]:
                    ex["top_e1rm"] = e1
            if w > 0 and (top_set is None or w > top_set["weight_kg"]):
                top_set = {"weight_kg": w, "reps": reps, "exercise": r["exercise"]}

    for ex in exercises:
        ex["volume_kg"] = round(ex["volume_kg"])

    return {
        "date": day,
        "session_types": session_types,
        "workout_name": rows[0]["workout_name"],
        "total_volume_kg": round(volume),
        "working_sets": working_sets,
        "total_reps": total_reps,
        "exercise_count": len(exercises),
        "top_e1rm": top_e1rm,
        "top_set": top_set,
        "exercises": exercises,
    }


BODYWEIGHT_COLOUR = "#b388ff"   # neon violet — distinct from the PPLA palette
BODYWEIGHT_RATE_DAYS = 30       # window for the current kg/week rate


def _centered_ma(series: list[dict], half_window_days: int = 7) -> list[dict]:
    """Lag-free smoothing: each point is the mean of all readings within
    ±half_window_days. Date-based (not point-based) so it's robust to gaps and
    irregular weigh-in spacing. Same length as the input."""
    pts = [(date.fromisoformat(p["date"]), p["weight"]) for p in series]
    out = []
    for d0, _ in pts:
        lo, hi = d0 - timedelta(days=half_window_days), d0 + timedelta(days=half_window_days)
        vals = [w for dj, w in pts if lo <= dj <= hi]
        out.append({"date": d0.isoformat(), "weight": round(sum(vals) / len(vals), 2)})
    return out


def _weekly_rate(series: list[dict], days: int = BODYWEIGHT_RATE_DAYS):
    """Least-squares slope of weight vs. time over the last `days`, as kg/week.
    None if the recent window is too thin to be meaningful."""
    if not series:
        return None
    latest = date.fromisoformat(series[-1]["date"])
    cutoff = latest - timedelta(days=days)
    window = [(date.fromisoformat(p["date"]), p["weight"]) for p in series
              if date.fromisoformat(p["date"]) >= cutoff]
    if len(window) < 3:
        return None
    xs = [(d - window[0][0]).days for d, _ in window]
    ys = [w for _, w in window]
    if xs[-1] - xs[0] < 7:        # span under a week — not a stable rate
        return None
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope_per_day = (n * sxy - sx * sy) / denom
    return round(slope_per_day * 7, 2)


def bodyweight(user: str) -> dict:
    """Bodyweight time series for the weight-over-time chart.

    'linked' reflects whether a Withings token exists; the frontend shows
    'weight tracker not connected' when there's no usable series.
    """
    linked = (_USERS_ROOT / user / "withings_token.json").is_file()
    con = _con(user)
    try:
        rows = con.execute("""
            SELECT date, weight_kg FROM bodyweight
            WHERE weight_kg IS NOT NULL AND weight_kg > 0
            ORDER BY date ASC
        """).fetchall()
    finally:
        con.close()
    series = [{"date": r["date"], "weight": round(r["weight_kg"], 1)} for r in rows]
    target = profile_editor.read_profile(user).get("target_weight_kg")
    return {
        "linked": linked,
        "colour": BODYWEIGHT_COLOUR,
        "series": series,
        "smoothed": _centered_ma(series) if len(series) >= 2 else [],
        "latest": series[-1]["weight"] if series else None,
        "target": target,
        "rate_kg_per_week": _weekly_rate(series),
        "rate_days": BODYWEIGHT_RATE_DAYS,
    }


def summary(user: str) -> dict:
    """Headline counts: totals, per-type balance, this week/month, streak."""
    con = _con(user)
    try:
        session_rows = con.execute("""
            SELECT date, session_type
            FROM sets
            WHERE session_type != 'unknown' AND session_type IS NOT NULL
            GROUP BY date
            ORDER BY date DESC
        """).fetchall()
        total_sets = con.execute(
            "SELECT COUNT(*) FROM sets WHERE is_warmup = 0 AND reps > 0"
        ).fetchone()[0]
    finally:
        con.close()

    dates = [r["date"] for r in session_rows]
    by_type = Counter(r["session_type"] for r in session_rows)

    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    month_start = today.replace(day=1).isoformat()
    last_28 = (today - timedelta(days=28)).isoformat()
    balance_28 = Counter(
        r["session_type"] for r in session_rows if r["date"] >= last_28
    )

    # Current streak: consecutive calendar days ending today or yesterday.
    trained = set(dates)
    streak = 0
    cursor = today
    if cursor.isoformat() not in trained:
        cursor = cursor - timedelta(days=1)
    while cursor.isoformat() in trained:
        streak += 1
        cursor = cursor - timedelta(days=1)

    return {
        "total_sessions": len(dates),
        "total_sets": total_sets,
        "sessions_this_week": sum(1 for d in dates if d >= week_start),
        "sessions_this_month": sum(1 for d in dates if d >= month_start),
        "current_streak": streak,
        "last_session_date": dates[0] if dates else None,
        "balance_all_time": {st: by_type.get(st, 0) for st in SESSION_TYPES},
        "balance_28d": {st: balance_28.get(st, 0) for st in SESSION_TYPES},
        "first_session_date": dates[-1] if dates else None,
    }


# ── Weekly review + split suggestion ──────────────────────────────────────
#
# The split a lifter *should* run is a function of how often they actually get
# to the gym: more days per week means each muscle can be hit on its own day
# while still being trained ~2×/week (the hypertrophy sweet spot). This section
# measures real cadence over the last few weeks and suggests the split that
# fits it. It only suggests — nothing here writes to the profile or the engine.

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
REVIEW_WEEKS = 17         # ~4 months of completed weeks for the cadence read

# Fallback split identity when a user's profile states nothing. Real values are
# derived per-user by _current_split() — kept out of config's process-global
# active-user state so stats stays correct when serving a different user.
CURRENT_SPLIT = "Push / Pull / Legs / Arms"
CURRENT_SPLIT_DAYS = 5    # the design frequency baked into the PPL+Arms cycle


def _current_split(user: str) -> tuple[str, int]:
    """The split this user is actually programmed on, and its design frequency
    (sessions/week). Read from their profile/GOAL — never from the process-global
    active config — so a multi-user server reports the right split per request.
    """
    import re
    prof = profile_editor.read_profile(user)
    name  = prof.get("split_name")
    cycle = prof.get("session_cycle")
    goal  = prof.get("goal_text") or ""
    if not name:
        m = re.search(r"split:\s*([^.\n]+?)(?:\.|\n|$)", goal, re.I)
        name = m.group(1).strip() if m else CURRENT_SPLIT
    design_days = _target_frequency(user) or (len(cycle) if cycle else CURRENT_SPLIT_DAYS)
    return name, design_days


def _week_bounds(offset_weeks: int = 0) -> tuple[date, date]:
    """(Monday, Sunday) of the week `offset_weeks` before the current one."""
    today = date.today()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=offset_weeks)
    return monday, monday + timedelta(days=6)


def _split_suggestion(spw: float) -> dict:
    """Map a measured weekly training cadence to the split that fits it.

    `sample` is one representative week of sessions; `per_muscle` is the
    resulting weekly frequency each muscle group gets under that split.
    `programmable` is True only for splits the engine can auto-generate (the
    push/pull/legs/arms family) — others are coach advice the athlete would set
    up manually.
    """
    if spw < 2.5:
        return {
            "name": "Full Body",
            "cadence": "2×/week",
            "sample": ["Full body", "Full body"],
            "per_muscle": "each muscle ~2×/week",
            "programmable": False,
            "rationale": "At two sessions a week, a split would train each muscle "
                         "only once — full-body days let you still hit everything twice.",
        }
    if spw < 3.5:
        return {
            "name": "Full Body ×3",
            "cadence": "3×/week",
            "sample": ["Full body", "Full body", "Full body"],
            "per_muscle": "each muscle ~3×/week",
            "programmable": False,
            "rationale": "Three whole-body sessions keep every muscle at a high "
                         "weekly frequency; a body-part split would leave big gaps.",
        }
    if spw < 4.5:
        return {
            "name": "Upper / Lower ×2",
            "cadence": "4×/week",
            "sample": ["Upper", "Lower", "Upper", "Lower"],
            "per_muscle": "each muscle ~2×/week",
            "programmable": False,
            "rationale": "Four days splits cleanly into two upper and two lower "
                         "days, hitting everything twice with room to recover.",
        }
    if spw < 5.5:
        return {
            "name": "Push / Pull / Legs / Arms",
            "cadence": "5×/week",
            "sample": ["Push", "Pull", "Legs", "Arms", "Push"],
            "per_muscle": "each muscle ~1.7×/week",
            "programmable": True,
            "rationale": "Five days is the classic PPL+Arms window — the split "
                         "this app already runs.",
        }
    if spw < 6.5:
        return {
            "name": "Push / Pull / Legs ×2",
            "cadence": "6×/week",
            "sample": ["Push", "Pull", "Legs", "Push", "Pull", "Legs"],
            "per_muscle": "each muscle ~2×/week",
            "programmable": True,
            "rationale": "Six days runs the full push/pull/legs cycle twice, "
                         "putting every muscle on a clean 2×/week frequency.",
        }
    return {
        "name": "Push / Pull / Legs ×2 + Arms",
        "cadence": "6–7×/week",
        "sample": ["Push", "Pull", "Legs", "Arms", "Push", "Pull", "Legs"],
        "per_muscle": "each muscle 2×/week +",
        "programmable": True,
        "rationale": "At this frequency PPL runs twice with a dedicated arms day "
                     "for extra volume — watch recovery at seven sessions.",
    }


def _target_frequency(user: str):
    """The 'N sessions per week' the athlete states in their profile GOAL, if any."""
    import re
    goal = profile_editor.read_profile(user).get("goal_text") or ""
    m = re.search(r"(\d+)\s*(?:-\s*\d+\s*)?sessions?\s+per\s+week", goal, re.I)
    return int(m.group(1)) if m else None


def _week_slice(day_type: dict, day_sets: dict, start: date, end: date) -> dict:
    """Aggregate one Mon–Sun window from the per-day maps."""
    days = []
    types: Counter = Counter()
    total_sets = 0
    d = start
    while d <= end:
        iso = d.isoformat()
        st = day_type.get(iso)
        n = day_sets.get(iso, 0)
        if st:
            types[st] += 1
            total_sets += n
        days.append({"weekday": WEEKDAY_NAMES[d.weekday()], "date": iso,
                     "session_type": st, "sets": n})
        d += timedelta(days=1)
    return {
        "start": start.isoformat(),
        "sessions": sum(types.values()),
        "sets": total_sets,
        "types": {st: types.get(st, 0) for st in SESSION_TYPES},
        "days": days,
    }


def _week_weight_change(bw_series: list[dict], start: date, end: date):
    """kg change between the first and last weigh-in inside a week, or None."""
    inside = [p for p in bw_series if start.isoformat() <= p["date"] <= end.isoformat()]
    if len(inside) < 2:
        return None
    return round(inside[-1]["weight"] - inside[0]["weight"], 1)


def weekly_review(user: str) -> dict:
    """This-week / last-week recap plus a cadence-driven split suggestion.

    Suggestion only: it reads how often (and which days) the athlete actually
    trains and names the split that fits, but changes nothing.
    """
    window_start, _ = _week_bounds(REVIEW_WEEKS)   # Monday, REVIEW_WEEKS weeks back
    con = _con(user)
    try:
        rows = con.execute("""
            SELECT date, session_type, COUNT(*) AS n
            FROM sets
            WHERE session_type != 'unknown' AND session_type IS NOT NULL
              AND is_warmup = 0 AND reps > 0 AND date >= ?
            GROUP BY date, session_type
        """, (window_start.isoformat(),)).fetchall()
        bw_rows = con.execute("""
            SELECT date, weight_kg FROM bodyweight
            WHERE weight_kg IS NOT NULL AND weight_kg > 0 AND date >= ?
            ORDER BY date ASC
        """, (window_start.isoformat(),)).fetchall()
    finally:
        con.close()

    # Dominant session type + total working sets per trained day.
    best: dict[str, tuple[int, str]] = {}
    day_sets: Counter = Counter()
    for r in rows:
        day_sets[r["date"]] += r["n"]
        cur = best.get(r["date"])
        if cur is None or r["n"] > cur[0]:
            best[r["date"]] = (r["n"], r["session_type"])
    day_type = {d: t for d, (n, t) in best.items()}
    bw_series = [{"date": r["date"], "weight": round(r["weight_kg"], 1)} for r in bw_rows]

    this_start, this_end = _week_bounds(0)
    prev_start, prev_end = _week_bounds(1)
    this_week = _week_slice(day_type, day_sets, this_start, this_end)
    prev_week = _week_slice(day_type, day_sets, prev_start, prev_end)
    this_week["bodyweight_change"] = _week_weight_change(bw_series, this_start, this_end)
    prev_week["bodyweight_change"] = _week_weight_change(bw_series, prev_start, prev_end)

    # Cadence read over the completed weeks only (the current partial week would
    # otherwise drag the average down).
    # Only count weeks from the athlete's first trained week onward, so a new
    # user with two weeks of history isn't scored as if they'd skipped six.
    trained_dates = sorted(day_type)
    first = date.fromisoformat(trained_dates[0]) if trained_dates else None
    active_weeks = []
    weekday_hits: Counter = Counter()
    for off in range(1, REVIEW_WEEKS + 1):
        w_start, w_end = _week_bounds(off)
        if first is None or w_end < first:
            continue
        trained = [d for d in day_type
                   if w_start.isoformat() <= d <= w_end.isoformat()]
        active_weeks.append(len(trained))
        for d in trained:
            weekday_hits[date.fromisoformat(d).weekday()] += 1
    avg_per_week = round(sum(active_weeks) / len(active_weeks), 1) if active_weeks else 0.0
    weeks_analysed = len(active_weeks)

    # Weekdays trained in at least half of the analysed weeks = "typical".
    threshold = max(1, weeks_analysed / 2) if weeks_analysed else 1
    typical = [WEEKDAY_NAMES[wd] for wd in range(7) if weekday_hits.get(wd, 0) >= threshold]
    day_counts = {WEEKDAY_NAMES[wd]: weekday_hits.get(wd, 0) for wd in range(7)}

    current_split, current_split_days = _current_split(user)
    suggestion = _split_suggestion(avg_per_week) if weeks_analysed else None
    if suggestion:
        suggestion["changes_recommended"] = round(avg_per_week) != current_split_days

    return {
        "this_week": this_week,
        "prev_week": prev_week,
        "frequency": {
            "avg_per_week": avg_per_week,
            "weeks_analysed": weeks_analysed,
            "typical_days": typical,
            "day_counts": day_counts,
            "target_per_week": _target_frequency(user),
        },
        "current_split": current_split,
        "current_split_days": current_split_days,
        "suggestion": suggestion,
        "colours": SESSION_COLOURS,
    }


def stats_payload(user: str) -> dict:
    """Everything the stats page needs, in one JSON-serialisable dict."""
    return {
        "summary": summary(user),
        "anchors": anchor_progress(user),
        "bodyweight": bodyweight(user),
        "calendar": calendar_days(user),
        "colours": SESSION_COLOURS,
    }
