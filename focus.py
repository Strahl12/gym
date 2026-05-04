"""
focus.py — Focus lift phase management.

Two phases per session type:
  focus:      emphasise the primary lift; accessories directly support it
  complement: triggered when focus lift is progressing well; shift emphasis
              to a complementary lift to build supporting strength

Phase transitions are computed from e1RM history on each run, requiring
no manual input. User can override the focus lift at any time via CLI.
"""
import sqlite3
from datetime import date, timedelta
from typing import Optional
import config


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── Phase logic ────────────────────────────────────────────────────────────

def _is_progressing(lift_name: str) -> bool:
    """
    True if the lift's e1RM has improved over the last COMPLEMENT_TRIGGER_SESSIONS
    sessions (most recent e1RM strictly higher than the oldest in that window).
    """
    from context import lift_history
    n = config.COMPLEMENT_TRIGGER_SESSIONS
    history = lift_history(lift_name, n=n + 1)
    e1rms = [h["best_e1rm"] for h in history[:n] if h.get("best_e1rm")]
    if len(e1rms) < n:
        return False
    return e1rms[0] > e1rms[-1]


def _is_plateaued(lift_name: str) -> bool:
    from context import lift_history, plateau_detected
    return plateau_detected(lift_history(lift_name, n=8))


def _pick_next_complement(session_type: str, focus_lift: str, exclude: Optional[str] = None) -> Optional[str]:
    """
    Pick the most overdue complementary lift from config.LIFT_COMPLEMENTS.
    Excludes the focus lift itself and optionally the last used complement.
    """
    candidates = [
        c for c in config.LIFT_COMPLEMENTS.get(focus_lift, [])
        if c != focus_lift and c != exclude
    ]
    if not candidates:
        return None

    # Prefer the one trained least recently
    from context import days_since_last
    def _recency(name: str) -> int:
        d = days_since_last(name)
        return d if d is not None else 9999

    return max(candidates, key=_recency)


# ── DB read/write ──────────────────────────────────────────────────────────

def _get_phase_row(session_type: str) -> Optional[sqlite3.Row]:
    con = _con()
    row = con.execute(
        "SELECT * FROM focus_lift_phases WHERE session_type = ?", (session_type,)
    ).fetchone()
    con.close()
    return row


def _upsert_phase(session_type: str, focus_lift: str, phase: str,
                  complement_lift: Optional[str], phase_started: str) -> None:
    con = _con()
    con.execute("""
        INSERT INTO focus_lift_phases
            (session_type, focus_lift, phase, complement_lift, phase_started, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(session_type) DO UPDATE SET
            focus_lift      = excluded.focus_lift,
            phase           = excluded.phase,
            complement_lift = excluded.complement_lift,
            phase_started   = excluded.phase_started,
            updated_at      = excluded.updated_at
    """, (session_type, focus_lift, phase, complement_lift, phase_started))
    con.commit()
    con.close()


# ── Public API ─────────────────────────────────────────────────────────────

def get_phase(session_type: str) -> dict:
    """
    Compute and return the current phase for a session type.
    Transitions phases automatically based on e1RM history.
    Initialises from DEFAULT_FOCUS_LIFTS if no row exists yet.

    Returns:
      {session_type, focus_lift, phase, complement_lift,
       phase_started, phase_age_days, transitioned}
    """
    today     = date.today().isoformat()
    row       = _get_phase_row(session_type)
    transitioned = False

    if not row:
        focus_lift = config.DEFAULT_FOCUS_LIFTS.get(session_type, "")
        _upsert_phase(session_type, focus_lift, "focus", None, today)
        row = _get_phase_row(session_type)

    # Sync config → DB: if focus phase and DEFAULT_FOCUS_LIFTS changed, update
    if row["phase"] == "focus":
        config_default = config.DEFAULT_FOCUS_LIFTS.get(session_type, "")
        if config_default and row["focus_lift"] != config_default:
            print(f"[focus] {session_type}: focus lift updated {row['focus_lift']} → {config_default} (config change)")
            _upsert_phase(session_type, config_default, "focus", None, today)
            row = _get_phase_row(session_type)

    focus_lift      = row["focus_lift"]
    current_phase   = row["phase"]
    complement_lift = row["complement_lift"]
    phase_started   = row["phase_started"]
    phase_age_days  = (date.today() - date.fromisoformat(phase_started)).days

    # ── Transition logic ───────────────────────────────────────────────────
    if current_phase == "focus":
        # Enter complement phase only if focus lift is actively progressing
        # and hasn't plateaued
        if _is_progressing(focus_lift) and not _is_plateaued(focus_lift):
            complement_lift = _pick_next_complement(session_type, focus_lift)
            if complement_lift:
                _upsert_phase(session_type, focus_lift, "complement", complement_lift, today)
                current_phase = "complement"
                phase_age_days = 0
                transitioned  = True
                print(f"[focus] {session_type}: entering complement phase → emphasise '{complement_lift}'")

    elif current_phase == "complement":
        focus_plateaued      = _is_plateaued(focus_lift)
        complement_plateaued = complement_lift and _is_plateaued(complement_lift)
        phase_expired        = phase_age_days >= config.COMPLEMENT_PHASE_DAYS

        if focus_plateaued or complement_plateaued or phase_expired:
            reason = ("focus lift plateaued" if focus_plateaued
                      else "complement lift plateaued" if complement_plateaued
                      else f"phase ran {phase_age_days}d (limit {config.COMPLEMENT_PHASE_DAYS}d)")
            _upsert_phase(session_type, focus_lift, "focus", None, today)
            current_phase   = "focus"
            complement_lift = None
            phase_age_days  = 0
            transitioned    = True
            print(f"[focus] {session_type}: returning to focus phase ({reason})")

    return {
        "session_type":    session_type,
        "focus_lift":      focus_lift,
        "phase":           current_phase,
        "complement_lift": complement_lift,
        "phase_started":   phase_started,
        "phase_age_days":  phase_age_days,
        "transitioned":    transitioned,
    }


def set_focus_lift(session_type: str, lift_name: str) -> None:
    """Manually override the focus lift for a session type. Resets to focus phase."""
    today = date.today().isoformat()
    _upsert_phase(session_type, lift_name, "focus", None, today)
    print(f"[focus] {session_type}: focus lift set to '{lift_name}'")


def all_phases() -> dict[str, dict]:
    """Return current phase state for all session types."""
    return {stype: get_phase(stype) for stype in config.SESSION_CYCLE}


def phase_summary() -> str:
    """One-line summary of all session type phases for logging."""
    parts = []
    for stype in config.SESSION_CYCLE:
        row = _get_phase_row(stype)
        if not row:
            continue
        if row["phase"] == "complement" and row["complement_lift"]:
            parts.append(f"{stype}=complement({row['complement_lift']})")
        else:
            parts.append(f"{stype}=focus({row['focus_lift']})")
    return " | ".join(parts)
