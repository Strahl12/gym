"""
plan.py — adaptive forward projection of upcoming sessions.

This is NOT a fixed calendar. It rolls the same reactive chooser
(context.pick_session_type) forward from current DB state to forecast the next
few *sessions in order* — never bound to specific dates. Because it is re-derived
from real logs on every call, a missed day, an extra rest day, or an out-of-order
session simply changes the next forecast; there is no schedule to fall out of sync
with. The user stays free to train (or rest) whenever they like.

Its purpose is order-awareness: today's prescription can see what session is
likely next (and what was last) and adjust exercise selection for adjacent-muscle
interactions (e.g. don't pre-fatigue triceps the day before a dedicated Arms day).
"""
import config
import context


def _typical_gap_days() -> int:
    """Rough number of days between sessions, from recent cadence (min 1 day)."""
    dates = context.recent_session_dates(days=28)
    spw = (len(dates) / 4.0) if dates else 0.0     # sessions per week
    if spw <= 0:
        return 2
    return max(1, round(7 / spw))


def project_sessions(n: int = 6, today_type: str | None = None) -> list[str]:
    """
    Forecast the next `n` sessions as an ordered list of session types, starting
    with today's suggested session. Rolls the reactive chooser forward, advancing
    a simulated recovery clock at the user's typical cadence between picks.

    `today_type`, if given, pins the day-0 slot to an already-committed session
    for today (e.g. a fresh prescription the coach just changed to arms). The DB
    only knows what's been *trained*, so without this the strip would keep showing
    the most-overdue type until the session is actually logged. Later slots still
    roll forward reactively, so tomorrow re-plans around today's committed session.
    """
    cycle = config.SESSION_CYCLE
    if not cycle:
        return []
    days_since = {t: context.days_since_session_type(t) for t in cycle}
    last = context.last_session_type()
    gap  = _typical_gap_days()

    if today_type not in cycle:
        today_type = None

    seq: list[str] = []
    for i in range(max(0, n)):
        # Day 0: a committed prescription for today wins; else the authoritative
        # live suggestion (honours recurring-activity buffers). Later steps use the
        # pure chooser on the simulated clock.
        pick = (today_type or context.suggest_session_type()) if i == 0 else \
            context.pick_session_type(days_since, last)
        seq.append(pick)
        for t in cycle:
            if days_since[t] is not None:
                days_since[t] += gap
        days_since[pick] = 0
        last = pick
    return seq


def next_session_type() -> str | None:
    """The session type most likely to follow today's (projection index 1)."""
    seq = project_sessions(2)
    return seq[1] if len(seq) > 1 else None


if __name__ == "__main__":
    import sys
    config.activate(sys.argv[1] if len(sys.argv) > 1 else "john")
    print("typical gap (days):", _typical_gap_days())
    print("projected sessions:", " → ".join(project_sessions(6)))
