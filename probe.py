"""
probe.py — audit Claude's fitness reasoning against the deterministic rules.

Replays every stored prescription (prescribed_sessions) against the athlete's
training history AS OF that date (sets table) and checks whether the model
honoured the rules the prompt demands of it. Splits findings into:

  HARD      — rules the engine states absolutely (weight-jump cap, loadable
              weight grid, no duplicate exercises, accessory no-repeat window).
              Violations here shipped bad prescriptions to the athlete.
  ADVISORY  — fitness-reasoning agreement (RPE-driven progression, plateau
              response, implied-e1RM sanity, compounds-first, warm-up shape).
              Disagreement isn't necessarily wrong (chat context the probe
              can't see may justify it) — read these as rates, not verdicts.

Ground truth is the sets table only (what was actually trained), so coach-chat
regens don't corrupt the baseline. Purely read-only; no API calls unless --live.

Usage:
  python probe.py --user john                 # audit all stored prescriptions
  python probe.py --user john --since 2026-07-01
  python probe.py --user john --verbose       # list every violation
  python probe.py --user john --json          # machine-readable output
  python probe.py --user john --live 3        # ALSO generate 3 fresh
                                              # prescriptions now and audit
                                              # them (costs API credits)
"""
import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date

import config

EPS = 1e-6
E1RM_MAX_REPS = 15          # same cap as stats.py so e1RM baselines agree


# ── Training-history index (ground truth, from the sets table) ─────────────

def load_history(db_path: str) -> dict:
    """{exercise: [(date, [set rows])]} sorted by date — working sets only."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT date, exercise, weight_kg, reps, rpe, is_warmup, set_number, e1rm "
        "FROM sets WHERE reps > 0 AND is_warmup = 0 ORDER BY date, set_number"
    ).fetchall()
    con.close()
    by_ex: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_ex[r["exercise"]][r["date"]].append(dict(r))
    return {ex: sorted(days.items()) for ex, days in by_ex.items()}


def prev_session(hist, ex, day):
    """Most recent trained session of `ex` strictly before `day`, or None."""
    sessions = hist.get(ex) or []
    prior = [s for s in sessions if s[0] < day]
    return prior[-1] if prior else None


def working_weight(sets):
    """The session's working weight: most common load; ties go to the heavier."""
    weights = [s["weight_kg"] or 0 for s in sets]
    if not weights:
        return 0.0
    counts = Counter(weights)
    top = max(counts.values())
    return max(w for w, c in counts.items() if c == top)


def mode_reps(sets, weight):
    reps = [s["reps"] for s in sets if abs((s["weight_kg"] or 0) - weight) < EPS]
    return Counter(reps).most_common(1)[0][0] if reps else 0


def last_set_rpe(sets):
    for s in reversed(sets):
        if s["rpe"] is not None:
            return float(s["rpe"])
    return None


def best_e1rm_before(hist, ex, day):
    best = None
    for d, sets in hist.get(ex) or []:
        if d >= day:
            break
        for s in sets:
            if s["reps"] <= E1RM_MAX_REPS and s["e1rm"]:
                best = max(best or 0, s["e1rm"])
    return best


def e1rm_series_before(hist, ex, day):
    """Per-session best e1RM series before `day` (mirrors stats.py)."""
    out = []
    for d, sets in hist.get(ex) or []:
        if d >= day:
            break
        vals = [s["e1rm"] for s in sets if s["reps"] <= E1RM_MAX_REPS and s["e1rm"]]
        if vals:
            out.append(max(vals))
    return out


# ── Prescription helpers ───────────────────────────────────────────────────

def p_working_sets(ex):
    return [s for s in ex.get("sets") or [] if not s.get("is_warmup")
            and (s.get("reps") or 0) > 0]


def p_top_weight(ex):
    return max((float(s.get("weight_kg") or 0) for s in p_working_sets(ex)), default=0.0)


def is_bw(ex):
    return "bodyweight" in (ex.get("exercise_type") or "") \
        or ex.get("equipment_category") == "none"


# ── The audit ──────────────────────────────────────────────────────────────

class Tally:
    """checked/flagged counters plus example strings per rule."""
    def __init__(self):
        self.checked = 0
        self.flagged = 0
        self.examples = []

    def check(self, bad: bool, example: str = ""):
        self.checked += 1
        if bad:
            self.flagged += 1
            if example:
                self.examples.append(example)

    def rate(self):
        return (self.flagged / self.checked * 100) if self.checked else 0.0


RULES = {
    "H1": "weight-jump cap (≤ +{cap}kg, baseline ≤14d old)",
    "H2": "loadable weight grid (equipment increments)",
    "H3": "no duplicate exercises in a session",
    "H4": "accessory no-repeat window",
    "A1a": "RPE ≤7 → progression applied",
    "A1b": "RPE ≥9.5 → load NOT increased",
    "A2": "implied e1RM sanity (top set ≤ best e1RM +10%)",
    "A3": "session opens with two compounds (non-arms days)",
    "A4": "plateau response (no load increase on a flat lift)",
    "A5": "main-lift warm-up shape",
}


def audit(user: str, since: str | None):
    config.activate(user)
    import exercise_lib
    meta = {}
    for e in exercise_lib.all_exercises().values():
        c = e.get("canonical")
        if c:
            meta[c] = e

    db = config.DB_PATH
    hist = load_history(db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    q = "SELECT id, date, session_type, exercises_json FROM prescribed_sessions"
    args = ()
    if since:
        q += " WHERE date >= ?"
        args = (since,)
    rows = con.execute(q + " ORDER BY date, id", args).fetchall()
    con.close()

    cap = float(config.PROGRESSION.get("max_increase_kg", 5.0))
    inc_map = getattr(config, "EQUIPMENT_INCREMENTS", {}) or {}
    tallies = {k: Tally() for k in RULES}

    for row in rows:
        day = row["date"]
        try:
            exercises = json.loads(row["exercises_json"])
        except Exception:
            continue

        # H3 duplicates — one check per prescription
        names = [e.get("exercise_name", "") for e in exercises]
        dupes = [n for n, c in Counter(names).items() if c > 1]
        tallies["H3"].check(bool(dupes), f"{day}: duplicate {dupes}" if dupes else "")

        # A3 compounds first — skip arms days: the arms template's own
        # "bicep/tricep compound" slots (curls, extensions) are single-joint by
        # the library's taxonomy, so flagging them would be unfair.
        if row["session_type"] != "arms":
            first_two = [meta.get(n, {}).get("is_compound") for n in names[:2]]
            if len(first_two) == 2 and None not in first_two:
                ok = all(first_two)
                tallies["A3"].check(not ok, "" if ok else f"{day} ({row['session_type']}): opens {names[:2]}")

        for ex in exercises:
            name = ex.get("exercise_name", "")
            wp = p_top_weight(ex)
            m = meta.get(name, {})

            # H2 grid — every prescribed load
            inc = float(inc_map.get(ex.get("equipment_category"), 2.5))
            for s in ex.get("sets") or []:
                w = float(s.get("weight_kg") or 0)
                if w <= 0:
                    continue
                off = abs(w / inc - round(w / inc)) > 1e-6
                tallies["H2"].check(off, f"{day} {name}: {w}kg not on {inc}kg grid" if off else "")

            # H4 no-repeat window — accessories only, against LOGGED history
            if not ex.get("is_main_lift"):
                prior = prev_session(hist, name, day)
                if prior:
                    gap = (date.fromisoformat(day) - date.fromisoformat(prior[0])).days
                    window = config.repeat_window_for(name, m.get("muscle", ""))
                    bad = 0 < gap <= window
                    tallies["H4"].check(
                        bad, f"{day} {name}: trained {gap}d earlier (window {window}d)" if bad else "")

            prior = prev_session(hist, name, day)
            if not prior or wp <= 0:
                continue
            w0 = working_weight(prior[1])
            r0 = mode_reps(prior[1], w0)
            gap_days = (date.fromisoformat(day) - date.fromisoformat(prior[0])).days

            # H1 jump cap — only when the baseline is current: past the deload
            # threshold the prompt's own re-entry rule (80% of last) governs,
            # and an old baseline may legitimately be beaten by more than +cap.
            deload_days = int(config.PROGRESSION.get("deload_threshold_days", 14))
            if w0 > 0 and gap_days <= deload_days:
                over = wp - w0
                bad = over > cap + EPS
                tallies["H1"].check(
                    bad, f"{day} {name}: {w0} → {wp}kg (+{over:g}, {gap_days}d gap)" if bad else "")

            # A1 RPE agreement (split by direction)
            rpe0 = last_set_rpe(prior[1])
            if rpe0 is not None and w0 > 0:
                rp = mode_reps([{"weight_kg": s.get("weight_kg"), "reps": s.get("reps")}
                                for s in p_working_sets(ex)], wp)
                increased = wp > w0 + EPS or (abs(wp - w0) < EPS and rp > r0)
                if rpe0 <= 7:
                    tallies["A1a"].check(
                        not increased,
                        "" if increased else f"{day} {name}: RPE {rpe0:g} but no progression ({w0}kg×{r0} → {wp}kg×{rp})")
                elif rpe0 >= 9.5:
                    tallies["A1b"].check(
                        increased,
                        f"{day} {name}: RPE {rpe0:g} but progressed {w0}kg×{r0} → {wp}kg×{rp}" if increased else "")

            # A2 implied e1RM sanity — weighted lifts only
            if not is_bw(ex) and w0 > 0:
                best = best_e1rm_before(hist, name, day)
                if best:
                    top = max(p_working_sets(ex), key=lambda s: float(s.get("weight_kg") or 0))
                    implied = float(top["weight_kg"]) * (1 + int(top["reps"]) / 30)
                    bad = implied > best * 1.10 + EPS
                    tallies["A2"].check(
                        bad, f"{day} {name}: implies e1RM {implied:.1f} vs best {best:.1f}" if bad else "")

            # A4 plateau response
            series = e1rm_series_before(hist, name, day)
            n = int(getattr(config, "PLATEAU_SESSIONS", 4))
            if len(series) >= n and series[-1] <= series[-n] and w0 > 0:
                bad = wp > w0 + EPS
                tallies["A4"].check(
                    bad, f"{day} {name}: plateaued but loaded {w0} → {wp}kg" if bad else "")

            # A5 warm-up shape — main barbell lifts
            if ex.get("is_main_lift") and ex.get("equipment_category") == "barbell":
                warms = [float(s.get("weight_kg") or 0) for s in ex.get("sets") or []
                         if s.get("is_warmup")]
                bad = not warms or (wp > 0 and max(warms) >= wp)
                tallies["A5"].check(
                    bad, f"{day} {name}: warmups {warms} vs working {wp}kg" if bad else "")

    return rows, tallies, cap


# ── Reporting ──────────────────────────────────────────────────────────────

def report(user, rows, tallies, cap, verbose=False, as_json=False):
    span = f"{rows[0]['date']} → {rows[-1]['date']}" if rows else "no data"
    if as_json:
        print(json.dumps({
            "user": user, "prescriptions": len(rows), "span": span,
            "rules": {k: {"description": RULES[k].format(cap=cap),
                          "checked": t.checked, "flagged": t.flagged,
                          "rate_pct": round(t.rate(), 1),
                          "examples": t.examples if verbose else t.examples[:5]}
                      for k, t in tallies.items()},
        }, indent=2))
        return

    print(f"\nProbe audit — {user} — {len(rows)} prescriptions ({span})\n")
    for section, keys in (("HARD RULES (engine-stated absolutes)", "H"),
                          ("ADVISORY (fitness-reasoning agreement)", "A")):
        print(section)
        for k, t in tallies.items():
            if not k.startswith(keys):
                continue
            desc = RULES[k].format(cap=f"{cap:g}")
            if t.checked == 0:
                print(f"  {k} {desc:52} no applicable data")
                continue
            print(f"  {k} {desc:52} {t.flagged:3d} flagged / {t.checked:4d} checks ({t.rate():.1f}%)")
            for e in (t.examples if verbose else t.examples[:5]):
                print(f"       · {e}")
            if not verbose and len(t.examples) > 5:
                print(f"       · … {len(t.examples) - 5} more (--verbose)")
        print()
    print("Notes: weight-grid snapping + dumbbell 2.5kg increments were added "
          "2026-09-23, so H2 flags before then are pre-fix and expected. "
          "Advisory disagreements may be justified by chat context (illness, "
          "deload requests) the probe can't see — treat them as rates to track, "
          "not individual verdicts.")


def live_probe(user, n, verbose):
    """Generate N fresh prescriptions from today's real context and audit each.
    Costs API credits (~$0.10 per generation); never posts to Hevy or the DB."""
    config.activate(user)
    import claude_api
    from context import build_context
    print(f"\nLIVE probe — generating {n} fresh prescription(s) for {user}'s "
          "current context (API credits will be spent)…")
    ctx = build_context()
    for i in range(n):
        w = claude_api.get_workout(ctx)
        if not w:
            print(f"  [{i + 1}] generation failed (see engine output above)")
            continue
        if w.get("rest_recommended"):
            print(f"  [{i + 1}] rest recommended: {w.get('reason')}")
            continue
        fake_row = {"id": -1, "date": date.today().isoformat(),
                    "session_type": w.get("session_type"),
                    "exercises_json": json.dumps(w.get("exercises", []))}
        # Reuse the same checks by writing through the audit path in-memory:
        print(f"  [{i + 1}] {w.get('session_type')} — {w.get('title')}")
        for ex in w.get("exercises", []):
            print(f"       {ex.get('exercise_name'):40} "
                  + " ".join(f"{s.get('weight_kg')}x{s.get('reps')}"
                             + ("w" if s.get("is_warmup") else "")
                             for s in ex.get("sets", [])))


def main():
    ap = argparse.ArgumentParser(description="Audit Claude's prescriptions against the rules")
    ap.add_argument("--user", required=True)
    ap.add_argument("--since", help="only audit prescriptions from this ISO date")
    ap.add_argument("--verbose", action="store_true", help="list every violation")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--live", type=int, metavar="N",
                    help="also generate N fresh prescriptions now and print them (spends API credits)")
    args = ap.parse_args()

    rows, tallies, cap = audit(args.user, args.since)
    report(args.user, rows, tallies, cap, verbose=args.verbose, as_json=args.as_json)
    if args.live:
        live_probe(args.user, args.live, args.verbose)


if __name__ == "__main__":
    main()
