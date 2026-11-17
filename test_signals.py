"""
Stress-test the athlete-signal systems (fatigue score, plateau detection,
readiness, achievable weights) against SYNTHETIC histories with known ground
truth. Run: .env/bin/python test_signals.py

Each scenario builds a fresh throwaway gym.db, points the production code at
it (config.DB_PATH monkeypatch — the real functions run unmodified), and
checks the verdicts against what a coach looking at the raw data would say.
"""
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta

import config
config.activate("john")              # load config constants (profile etc.)

import context
import stats
from feedback import recent_notes

TODAY = date.today()
LIFT = "Barbell Back Squat"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok ' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


# ── synthetic DB helpers ─────────────────────────────────────────────────────

def fresh_db():
    tmp = tempfile.mkdtemp(prefix="synth_gym_")
    db = os.path.join(tmp, "gym.db")
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE sets (id INTEGER PRIMARY KEY, source TEXT, session_id TEXT,
            date TEXT, workout_name TEXT, session_type TEXT, muscle_group TEXT,
            exercise TEXT, is_main_lift INT, is_bodyweight INT, is_warmup INT,
            set_number INT, weight_kg REAL, reps INT, e1rm REAL, notes TEXT,
            rpe REAL, set_type TEXT);
        CREATE TABLE prescribed_sessions (id INTEGER PRIMARY KEY, block_id INT,
            date TEXT, session_type TEXT, exercises_json TEXT, reasoning TEXT,
            posted_to_hevy INT);
        CREATE TABLE session_notes (id INTEGER PRIMARY KEY, date TEXT,
            note TEXT, source TEXT);
        CREATE TABLE bodyweight (date TEXT, weight_kg REAL);
        CREATE TABLE exercise_roster (exercise_name TEXT, is_main_lift INT);
    """)
    con.commit()
    con.close()
    config.DB_PATH = db
    return tmp, db


def epley(w, r):
    return w * (1 + r / 30.0)


def add_session(db, d, exercise=LIFT, sets=((100, 8, 8.0),) * 4,
                session_type="legs", warmups=True):
    """sets: iterable of (weight, reps, rpe-or-None)."""
    con = sqlite3.connect(db)
    sid = f"synth_{d}_{session_type}"
    n = 0
    if warmups:
        w0 = sets[0][0]
        for ww, rr in ((round(w0 * 0.4, 1), 10), (round(w0 * 0.7, 1), 5)):
            n += 1
            con.execute("""INSERT INTO sets (source, session_id, date, session_type,
                exercise, is_warmup, set_number, weight_kg, reps, e1rm)
                VALUES ('app', ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                        (sid, d, session_type, exercise, n, ww, rr, epley(ww, rr)))
    for (w, r, rpe) in sets:
        n += 1
        con.execute("""INSERT INTO sets (source, session_id, date, session_type,
            exercise, is_warmup, set_number, weight_kg, reps, e1rm, rpe)
            VALUES ('app', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
                    (sid, d, session_type, exercise, n, w, r, epley(w, r), rpe))
    con.commit()
    con.close()


def add_prescription(db, d, exercise=LIFT, sets=((100, 8),) * 4,
                     session_type="legs", reasoning="Progress as normal."):
    con = sqlite3.connect(db)
    ex_json = json.dumps([{
        "exercise_name": exercise, "equipment_category": "barbell",
        "sets": [{"weight_kg": w, "reps": r} for (w, r) in sets],
    }])
    con.execute("""INSERT INTO prescribed_sessions
        (date, session_type, exercises_json, reasoning, posted_to_hevy)
        VALUES (?, ?, ?, ?, 1)""", (d, session_type, ex_json, reasoning))
    con.commit()
    con.close()


def iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def hist():
    return context.lift_history(LIFT, n=10)


def plateau():
    return context.plateau_detected(hist(), suppressed=context.suppressed_session_dates())


# ── the OLD fatigue formula, reimplemented for contrast ──────────────────────

def old_fatigue(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    score = 0.0
    last_date = con.execute(
        "SELECT MAX(date) FROM sets WHERE is_warmup=0").fetchone()[0]
    if last_date and (TODAY - date.fromisoformat(last_date)).days <= 4:
        rows = con.execute("""SELECT exercise, rpe, set_number FROM sets
            WHERE date=? AND is_warmup=0 AND rpe IS NOT NULL
            ORDER BY exercise, set_number""", (last_date,)).fetchall()
        by_ex = {}
        for r in rows:
            by_ex.setdefault(r["exercise"], []).append(r["rpe"])
        lasts = [v[-1] for v in by_ex.values() if v]
        firsts = [v[0] for v in by_ex.values() if v]
        if lasts:
            la = sum(lasts) / len(lasts)
            if la >= 9.5: score += 30
            elif la >= 9.0: score += 18
            elif la >= 8.0: score += 8
        if firsts and sum(firsts) / len(firsts) >= 8.0:
            score += 10
    # 14d slope on the test lift
    rows = con.execute("""SELECT date, MAX(e1rm) e FROM sets
        WHERE exercise=? AND is_warmup=0 AND date >= ? GROUP BY date ORDER BY date""",
        (LIFT, iso(14))).fetchall()
    if len(rows) >= 2:
        xs = [(date.fromisoformat(r["date"]) - date.fromisoformat(rows[0]["date"])).days
              for r in rows]
        ys = [r["e"] for r in rows]
        n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        if den:
            slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den * 7
            if slope < 0:
                score += min(25.0, abs(slope) * 8)
    con.close()
    consec = context.consecutive_training_days()
    score += min(20.0, consec / config.MAX_CONSECUTIVE_DAYS * 20)
    return round(score, 1)


def verdict(s):
    return "rest" if s >= 60 else ("caution" if s >= 40 else "ok")


# ── scenarios ────────────────────────────────────────────────────────────────

def scenario(title):
    print(f"\n== {title} ==")


# 1. Healthy linear progressor — everything should read calm.
scenario("1. Healthy progressor (+2.5kg/session, RPE 7-8, full reps)")
tmp, db = fresh_db()
w = 80.0
for k in range(10, 0, -1):            # 10 sessions, every 3 days
    d = iso(k * 3)
    add_prescription(db, d, sets=((w, 8),) * 4)
    add_session(db, d, sets=((w, 8, 7.5),) * 4)
    w += 2.5
f = context.fatigue_score()
check("fatigue ok", f["verdict"] == "ok", f"score {f['score']}")
check("no plateau", not plateau())
check("acwr ~1", f["components"]["acwr"] is not None and 0.7 <= f["components"]["acwr"] <= 1.3,
      f"acwr {f['components']['acwr']}")
check("no shortfall", (f["components"]["rep_shortfall_pct"] or 0) < 5)
shutil.rmtree(tmp)

# 2. Acute overreach — volume doubled this week, RPE drifting, reps failing.
scenario("2. Acute overreach (double volume last week, +1.5 RPE, missed reps)")
tmp, db = fresh_db()
for k in range(28, 7, -3):            # 3 weeks stable base
    d = iso(k)
    add_prescription(db, d, sets=((100, 8),) * 4)
    add_session(db, d, sets=((100, 8, 7.5),) * 4)
for k in range(6, 0, -1):             # 6 straight days, double sets, struggling
    d = iso(k)
    add_prescription(db, d, sets=((100, 8),) * 8)
    add_session(db, d, sets=((100, 8, 9.0),) * 4 + ((100, 6, 9.5),) * 4)
f = context.fatigue_score()
c = f["components"]
check("fatigue elevated (caution/rest)", f["verdict"] in ("caution", "rest"),
      f"score {f['score']} acwr {c['acwr']} shortfall {c['rep_shortfall_pct']}% drift {c['rpe_drift']}")
check("acwr high", (c["acwr"] or 0) > 1.4, f"acwr {c['acwr']}")
check("shortfall seen", (c["rep_shortfall_pct"] or 0) >= 8, f"{c['rep_shortfall_pct']}%")
shutil.rmtree(tmp)

# 3. Genuine current plateau — free prescriptions, flat e1RM, grinding RPE.
scenario("3. Genuine plateau (6 free sessions flat at RPE 8.5-9)")
tmp, db = fresh_db()
for k in range(6, 0, -1):
    d = iso(k * 4)
    add_prescription(db, d, sets=((100, 8),) * 4, reasoning="Progress as normal.")
    add_session(db, d, sets=((100, 8, 8.5),) * 3 + ((100, 8, 9.0),))
f = context.fatigue_score()
check("plateau detected", plateau())
check("fatigue NOT rest (plateau != fatigue)", f["verdict"] != "rest", f"score {f['score']}")
shutil.rmtree(tmp)

# 4. The old doom loop — real plateau 8 weeks ago, then 6 weeks of coach-imposed
#    caps/deloads with declining weights, athlete executing everything cleanly.
scenario("4. Doom loop replay (old plateau, then 6wk of capped/deloaded sessions)")
tmp, db = fresh_db()
for k in range(5, 0, -1):             # the (real, old) plateau: 56-76 days ago
    d = iso(55 + k * 4)
    add_prescription(db, d, sets=((110, 8),) * 4, reasoning="Progress as normal.")
    add_session(db, d, sets=((110, 8, 8.5),) * 4)
w = 100.0
for k in range(12, 0, -1):            # capped era: weights stepped down, clean execution
    d = iso(k * 4)
    add_prescription(db, d, sets=((w, 8),) * 4,
                     reasoning=f"Plateau reset / deload: RPE cap 8 at {w}kg.")
    add_session(db, d, sets=((w, 8, 8.0),) * 4)
    if k % 4 == 0:
        w -= 5
f = context.fatigue_score()
check("NO plateau flag (suppressed + stale evidence)", not plateau())
check("fatigue ok (clean execution under caps)", f["verdict"] == "ok", f"score {f['score']}")
check("deload correctly flagged", f["components"]["deload_active"] is True)
old = old_fatigue(db)
print(f"       (old formula on same data: {old} -> {verdict(old)}; "
      f"new: {f['score']} -> {f['verdict']})")
shutil.rmtree(tmp)

# 5. True fatigue WITHOUT volume spike — reps collapsing, RPE way over baseline.
scenario("5. Underperformance (normal volume, 20% rep shortfall, +1.5 RPE drift)")
tmp, db = fresh_db()
for k in range(28, 7, -3):            # healthy baseline
    d = iso(k)
    add_prescription(db, d, sets=((100, 8),) * 4)
    add_session(db, d, sets=((100, 8, 7.5),) * 4)
for k in (6, 3, 1):                   # recent: same prescription, falling apart
    d = iso(k)
    add_prescription(db, d, sets=((100, 8),) * 4)
    add_session(db, d, sets=((100, 7, 9.0),) * 2 + ((100, 6, 9.5),) * 2)
f = context.fatigue_score()
c = f["components"]
check("fatigue at least caution", f["verdict"] in ("caution", "rest"),
      f"score {f['score']} shortfall {c['rep_shortfall_pct']}% drift {c['rpe_drift']}")
check("shortfall detected", (c["rep_shortfall_pct"] or 0) >= 10, f"{c['rep_shortfall_pct']}%")
shutil.rmtree(tmp)

# 6. No-RPE logger — must abstain on drift, stay calm, not crash.
scenario("6. RPE never logged (objective signals only)")
tmp, db = fresh_db()
for k in range(10, 0, -1):
    d = iso(k * 3)
    add_prescription(db, d, sets=((100, 8),) * 4)
    add_session(db, d, sets=((100, 8, None),) * 4)
f = context.fatigue_score()
check("drift abstains", f["components"]["rpe_drift"] is None)
check("verdict ok", f["verdict"] == "ok", f"score {f['score']}")
shutil.rmtree(tmp)

# 7. Typo robustness — one 900kg fat-finger in yesterday's session.
scenario("7. Typo set (900kg x 8 logged yesterday)")
tmp, db = fresh_db()
for k in range(28, 1, -3):
    d = iso(k)
    add_prescription(db, d, sets=((100, 8),) * 4)
    add_session(db, d, sets=((100, 8, 7.5),) * 4)
add_prescription(db, iso(1), sets=((100, 8),) * 4)
add_session(db, iso(1), sets=((100, 8, 7.5),) * 3 + ((900, 8, 7.5),))
f = context.fatigue_score()
c = f["components"]
check("typo does not flip verdict", f["verdict"] == "ok",
      f"score {f['score']} acwr {c['acwr']}")
check("typo excluded from workload", (c["acwr"] or 0) < 1.3, f"acwr {c['acwr']}")
aw = stats.achievable_weights(db_path=db)
bb = aw.get("equipment:barbell", {}).get("weights", [])
check("typo weight not 'achievable' (min_seen)", 900.0 not in bb, f"barbell set {bb}")
shutil.rmtree(tmp)

# ── summary ──────────────────────────────────────────────────────────────────
print(f"\n{'='*60}\n{len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("  FAIL:", name)
raise SystemExit(1 if FAIL else 0)
