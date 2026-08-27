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
SESSION_COLOURS = {
    "push": "#2563eb",   # blue
    "pull": "#16a34a",   # green
    "legs": "#d97706",   # amber
    "arms": "#9333ea",   # purple
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


BODYWEIGHT_COLOUR = "#0891b2"   # cyan — distinct from the PPLA palette
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


def stats_payload(user: str) -> dict:
    """Everything the stats page needs, in one JSON-serialisable dict."""
    return {
        "summary": summary(user),
        "anchors": anchor_progress(user),
        "bodyweight": bodyweight(user),
        "calendar": calendar_days(user),
        "colours": SESSION_COLOURS,
    }
