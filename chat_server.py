"""
chat_server.py — Bare-bones web chat with the AI coach, one secret URL per user.

Each user gets a link  /u/<CHAT_TOKEN>  (token lives in users/<name>/secrets.env;
the add-user wizard generates one). The page shows their chat history; messages
are answered by Claude using the same athlete context that drives the morning
prescription (build_context + format_athlete_context), plus today's prescription
if one was generated.

Runs on localhost; exposed publicly via Tailscale Funnel on its own port so the
existing tailnet-only serve on 443 stays private:

    tailscale funnel --bg --https=8443 http://127.0.0.1:8090

Kept alive by cron — @reboot start plus a */5 flock watchdog (see README).
"""
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, make_response, redirect, render_template, request

import chat_auth
import config
from context import build_context
from claude_api import ANTHROPIC_URL, CLAUDE_MODEL, _headers, format_athlete_context

ROOT       = Path(__file__).parent
USERS_ROOT = ROOT / "users"

HOST = os.environ.get("GYM_CHAT_HOST", "127.0.0.1")
PORT = int(os.environ.get("GYM_CHAT_PORT", "8090"))
HISTORY_TO_MODEL    = 20      # messages of continuity sent to Claude
HISTORY_ON_PAGE     = 100     # messages rendered on page load
MAX_MESSAGE_CHARS   = 2000
RATE_LIMIT_MESSAGES = 30      # per user...
RATE_LIMIT_WINDOW_S = 3600    # ...per hour — caps the Anthropic bill
CHAT_MAX_TOKENS     = 1024
REGEN_TIMEOUT_S     = 240     # full engine run: syncs + prescription + Hevy POST
CHAT_SYNC_THROTTLE_S = 600    # re-pull Hevy at most once per 10 min per user in chat
CHAT_SYNC_DAYS       = 14     # how far back the on-demand chat sync looks

DEV_USER = "john"             # only this user sees the dev status panel in their chat

CHAT_SYSTEM = """You are {name}'s strength coach — the same AI that writes their daily gym programming.
Answer questions about their training using the athlete data below.

Rules:
- Be concise and specific to the data. Plain text only — no markdown headings or tables
  (replies render in a small chat bubble). Weights in kg.
- You CAN regenerate today's routine: regenerate_routine re-runs the programming engine
  (fresh Hevy/Withings sync, fresh prescription) and replaces today's session in their
  Hevy app. The engine reads this chat, so whatever they've told you here — less time,
  feeling beaten up, want a different session type, a movement swap — gets factored in.
  Confirm what they want changed and get an explicit yes before calling it; warn that it
  replaces the current routine and takes a minute or two. If the engine declines (rest
  day, activity day, Claude recommends rest), relay the reason and stop — only retry with
  force=true if they explicitly insist on training anyway. Tiny tweaks (one weight, one
  set) are quicker edited directly in the Hevy app.
- You cannot log workouts or completed sets from this chat. If they want future
  programming to behave differently on a specific exercise, they can add an exercise
  note in Hevy starting with "NOTE:" — the morning engine reads those as directives.
- However, what they tell you HERE about their readiness — illness, poor sleep, injury,
  soreness, stress, limited time — IS read by the morning engine (it sees chat messages from
  the last 48h). Acknowledge such reports and confirm they'll be factored into the next
  session. No special format needed; they just have to mention it before the morning run.
- If the data doesn't answer their question, say so rather than guessing.
- You CAN pull their Weekly Review: get_weekly_review returns this-week vs last-week
  sessions/sets/bodyweight, their real training cadence over the last ~4 months, and the
  split that fits it. Call it whenever they want to discuss the review, their weekly
  summary, how often they train, or whether their split suits their schedule. Ground your
  reply in those numbers. The split it names is a suggestion — talk it through, but only
  change anything if they explicitly confirm (then use update_profile).

## Changing training goals
You CAN change their training profile — main lifts, focus lifts, training mode,
goal mode, target weight — via the update_profile tool. This is a guided process:
- A profile change is the athlete's decision, and asking them to confirm is the ONLY
  gate. Never refuse or block a change based on their training history, recency, or
  whether they've trained that session type lately. If they want to change their arms
  anchor when they haven't done arms in weeks, that is completely fine — do it. You are
  not a gatekeeper; you confirm the exact change and apply it. The only hard stops are
  the tool's own validation errors (e.g. an exercise name that doesn't exist), which you
  relay plainly and help them fix — you never leave them unable to make the change.
- When they start talking about changing a goal or lift, walk them through what the
  app needs, one or two questions at a time. For a new main lift that means: which
  exact Hevy exercise (use search_hevy_exercises and confirm the title with them),
  which session type (push/pull/legs/arms), sets, rep range, and progression
  increment. Suggest sensible defaults from their data instead of interrogating —
  e.g. "4 sets of 4-6 reps, +2.5kg progression — sound good?".
- "Anchor lift" means the focus lift: the lift each session type (push/pull/legs/arms)
  is built around — every session anchors on it and accessories support it. If they ask
  to see their anchor lifts, list them per session type from the profile below (plain
  lines, one per session type), noting any temporary complement phase. Change one with
  the set_focus_lift op — usually one of their main lifts, but any real Hevy exercise
  works (e.g. Close Grip Bench Press on arms day). A confirmed change re-anchors the
  very next session of that type.
- Changing training mode changes what rep ranges make sense. When they change mode,
  review each main lift's rep range in the same conversation and propose updates
  (strength ≈ 4-6, hypertrophy ≈ 8-12, mixed ≈ 5-8; bodyweight lifts progress by
  reps anyway, so leave theirs unless asked). Apply the set_main_lift rep_range ops
  together with set_training_mode once confirmed, and tell them working weights will
  be recalculated for the new rep range — heavier low-rep weights do NOT carry over.
- Before applying, state the exact change in one message and get an explicit yes.
  NEVER call update_profile without the athlete confirming in this conversation.
- Changes take effect from the next generated session. After applying, offer to
  regenerate today's routine so they kick in immediately; otherwise it's tomorrow.
- Session durations and the excluded-exercises list are also changeable here.
- For anything the tool doesn't cover, explain it can't be changed from chat.
- If they ask how their coaching is set up or configured (their goals, lifts,
  linked accounts, "what do you know about me", etc.), give a plain-language
  rundown of the profile below — aims, main and focus lifts, session durations,
  exclusions, and linked accounts — then ask if they'd like to change any of it.
  Plain text, remember: simple labelled lines and dashes, no ** or ## markdown.

## Their current profile
{profile_block}
{onboarding_block}
## Athlete data (generated fresh for this message — the same context the morning engine sees)
{athlete_block}
{today_block}"""

ONBOARDING_GUIDE = """
## SETUP NEEDED — first-time onboarding
This athlete has NOT confirmed their training profile yet; the profile above is
template defaults. Before anything else, greet them, explain you'll set up their
training together, and walk through it one or two questions at a time:
1. Training mode (strength / hypertrophy / mixed) and goal mode (cut / bulk /
   maintain) — if cutting or bulking, also target weight and kg/week rate.
2. Main lifts, one per session type at minimum. If their data above shows
   training history, suggest their most-trained movements as mains and confirm;
   otherwise propose common defaults. Use search_hevy_exercises for exact
   titles. Suggest sets / rep range / progression defaults rather than asking
   for every number.
3. Focus lifts per session type (default: their main lift for that type).
Apply confirmed changes with update_profile as you go. When everything above is
confirmed, include the complete_onboarding op in the final update — that ends
setup mode. Until then, gently steer other questions back to finishing setup.
Once setup is complete, offer to generate their first session right now with
regenerate_routine — it lands in their Hevy app a minute or two later.
"""

CHAT_TOOLS = [
    {
        "name": "search_hevy_exercises",
        "description": "Search the athlete's Hevy exercise library by name. Use this to find "
                       "the exact exercise title before adding or changing a main/focus lift.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "update_profile",
        "description": "Apply confirmed changes to the athlete's training profile. Only call "
                       "after the athlete has explicitly confirmed the exact change in this "
                       "conversation. Batch related operations into one call; the batch is "
                       "all-or-nothing and errors return guidance on what is missing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string",
                                   "enum": ["set_main_lift", "remove_main_lift", "set_focus_lift",
                                            "set_training_mode", "set_goal_mode",
                                            "set_target_weight_kg", "set_weight_rate_kg_per_week",
                                            "set_session_duration", "set_excluded_exercises",
                                            "complete_onboarding"]},
                            "name": {"type": "string",
                                     "description": "Lift name (set_main_lift / remove_main_lift / set_focus_lift)"},
                            "hevy_exercise_title": {"type": "string",
                                                    "description": "Exact Hevy library title (from search_hevy_exercises)"},
                            "session_type": {"type": "string", "enum": ["push", "pull", "legs", "arms"]},
                            "target_sets": {"type": "integer"},
                            "rep_range": {"type": "array", "items": {"type": "integer"},
                                          "description": "[low, high]"},
                            "progression_kg": {"type": "number"},
                            "is_bodyweight": {"type": "boolean"},
                            "day_type": {"type": "string", "enum": ["weekday", "weekend"],
                                         "description": "For set_session_duration"},
                            "value": {"description": "Value for the scalar set_* ops. For "
                                      "set_excluded_exercises: the FULL new list of exact "
                                      "Hevy titles (replaces the old list). For "
                                      "set_session_duration: minutes."},
                        },
                        "required": ["op"],
                    },
                },
            },
            "required": ["operations"],
        },
    },
    {
        "name": "regenerate_routine",
        "description": "Re-run the programming engine for the athlete NOW: syncs their latest "
                       "Hevy workouts and bodyweight, rebuilds context (including this chat, so "
                       "their requests here are factored in), gets a fresh prescription, and "
                       "replaces today's routine in their Hevy app. Use after profile changes or "
                       "when they want today's session redone. Takes a minute or two. Only call "
                       "after the athlete has explicitly confirmed in this conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean",
                          "description": "Override a rest-day or activity-day block. Only after "
                                         "the athlete explicitly insists on training despite it."},
            },
        },
    },
    {
        "name": "get_weekly_review",
        "description": "Pull the athlete's Weekly Review data: this-week vs last-week sessions, "
                       "sets and bodyweight change, their real training cadence over the last ~4 "
                       "months (average sessions/week and which weekdays they usually train), and "
                       "the split that best fits that cadence. Call this whenever the athlete wants "
                       "to talk about their review, their weekly summary, how often they train, or "
                       "whether their split suits their schedule — it's the same data behind the "
                       "Review tab. Read-only; changes nothing.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

MAX_TOOL_ROUNDS = 6

app = Flask(__name__)

_CONFIG_LOCK = threading.Lock()          # config.activate() mutates process-global state
_RATE: dict[str, deque] = defaultdict(deque)
_LAST_SYNC: dict[str, float] = {}        # user → last on-demand Hevy sync (throttle)

# Anthropic API health, updated from every coach call + the dev credit ping.
# Surfaced only to DEV_USER via the dev panel in their chat page.
_API_LOCK = threading.Lock()
_API_STATUS: dict = {
    "credit_ok":     True,   # False once the API reports the credit balance is too low
    "last_error":    None,   # human-readable last API error (any kind)
    "last_error_ts": None,
    "last_ok_ts":    None,   # last time a call to Anthropic succeeded
}


def _is_credit_error(status: int, msg: str) -> bool:
    """Anthropic signals an exhausted balance as HTTP 400 whose message says the
    credit balance is too low. 402 is treated as billing defensively."""
    m = (msg or "").lower()
    return status == 402 or "credit balance" in m or "insufficient credit" in m


def _note_api_ok() -> None:
    with _API_LOCK:
        _API_STATUS["credit_ok"] = True
        _API_STATUS["last_ok_ts"] = datetime.now().isoformat(timespec="seconds")


def _note_api_error(status: int, msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    with _API_LOCK:
        if _is_credit_error(status, msg):
            _API_STATUS["credit_ok"] = False
            _API_STATUS["last_error"] = f"OUT OF CREDIT — {(msg or '')[:240]}"
        else:
            _API_STATUS["last_error"] = f"HTTP {status} — {(msg or '')[:240]}"
        _API_STATUS["last_error_ts"] = ts


def _devinfo_payload() -> dict:
    with _API_LOCK:
        payload = dict(_API_STATUS)
    payload["model"] = CLAUDE_MODEL
    return payload


# ---- new-user invites (dev-generated, single-use) ------------------------
# Stored in users/.invites.json (gitignored under users/*). Each token gates
# one self-service signup at /join/<token>.
INVITES_PATH = USERS_ROOT / ".invites.json"
_INVITE_LOCK = threading.Lock()


def _load_invites() -> dict:
    try:
        return json.loads(INVITES_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_invites(data: dict) -> None:
    tmp = INVITES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(INVITES_PATH)


def _new_invite() -> str:
    token = secrets.token_urlsafe(24)
    with _INVITE_LOCK:
        inv = _load_invites()
        inv[token] = {"created": datetime.now().isoformat(timespec="seconds"), "used_by": None}
        _save_invites(inv)
    return token


def _invite_open(token: str) -> bool:
    if not token:
        return False
    with _INVITE_LOCK:
        rec = _load_invites().get(token)
    return bool(rec and rec.get("used_by") is None)


def _consume_invite(token: str, user: str) -> bool:
    """Mark an invite used. Returns False if it was already spent or unknown."""
    with _INVITE_LOCK:
        inv = _load_invites()
        rec = inv.get(token)
        if not rec or rec.get("used_by") is not None:
            return False
        rec["used_by"] = user
        rec["used_at"] = datetime.now().isoformat(timespec="seconds")
        _save_invites(inv)
    return True


def _credit_ping() -> dict:
    """Cheap live probe: a 1-token message call that reveals credit state without
    real cost. Updates _API_STATUS and returns the fresh payload. DEV_USER only."""
    with _CONFIG_LOCK:
        config.activate(DEV_USER)
        headers = _headers()
    try:
        resp = requests.post(
            ANTHROPIC_URL, headers=headers,
            json={"model": CLAUDE_MODEL, "max_tokens": 1,
                  "messages": [{"role": "user", "content": "ping"}]},
            timeout=30,
        )
    except Exception as e:
        _note_api_error(0, str(e))
        return _devinfo_payload()
    if resp.ok:
        _note_api_ok()
    else:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.reason
        _note_api_error(resp.status_code, msg)
    return _devinfo_payload()


def _sync_recent(user: str) -> None:
    """Pull the user's latest Hevy workouts into their DB so the coach sees
    self-directed sessions right after they finish, not just after the 7:30am
    cron. Throttled per user; failures are non-fatal (fall back to synced data).

    Must be called with _CONFIG_LOCK held and config already activated for user.
    """
    now = time.time()
    if now - _LAST_SYNC.get(user, 0.0) < CHAT_SYNC_THROTTLE_S:
        return
    _LAST_SYNC[user] = now
    try:
        from hevy_sync import sync_to_db
        n = sync_to_db(days=CHAT_SYNC_DAYS)
        if n:
            print(f"[chat] {user}: on-demand Hevy sync wrote {n} new sets")
    except Exception as e:
        print(f"[chat] {user}: on-demand Hevy sync failed (using existing data): {e}")


def _load_tokens() -> dict[str, str]:
    """token → user name, from CHAT_TOKEN= lines in users/*/secrets.env."""
    tokens: dict[str, str] = {}
    for env in sorted(USERS_ROOT.glob("*/secrets.env")):
        user = env.parent.name
        if user.startswith("_"):
            continue
        m = re.search(r"^CHAT_TOKEN=(\S+)\s*$", env.read_text(), flags=re.MULTILINE)
        if not m:
            continue
        token = m.group(1)
        if token in tokens:
            print(f"[chat] WARNING: users {tokens[token]!r} and {user!r} share a CHAT_TOKEN — ignoring {user!r}")
            continue
        if len(token) < 16:
            print(f"[chat] WARNING: CHAT_TOKEN for {user!r} is too short (<16 chars) — ignoring")
            continue
        tokens[token] = user
    return tokens


TOKENS = _load_tokens()
SECRET = chat_auth.load_secret(USERS_ROOT / ".chat_session_secret")


def _user_for(token: str) -> str | None:
    for known, user in TOKENS.items():
        if hmac.compare_digest(known, token):
            return user
    return None


def _password_hash_for(user: str) -> str | None:
    if user.startswith("_") or not (USERS_ROOT / user).is_dir():
        return None
    record = chat_auth.load_auth(USERS_ROOT, user)
    return record.get("password_hash") if record else None


def _session_user() -> str | None:
    """User name from a valid session cookie, if the account still exists."""
    cookie = request.cookies.get(chat_auth.SESSION_COOKIE)
    if not cookie:
        return None
    return chat_auth.read_session(SECRET, cookie, _password_hash_for)


def _login_redirect(user: str, password_hash: str):
    resp = make_response(redirect("/app", code=303))
    resp.set_cookie(
        chat_auth.SESSION_COOKIE,
        chat_auth.make_session(SECRET, user, password_hash),
        max_age=chat_auth.SESSION_TTL_S,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    return resp


def _db(user: str) -> sqlite3.Connection:
    con = sqlite3.connect(USERS_ROOT / user / "gym.db")
    con.row_factory = sqlite3.Row
    # Same schema as migrate.py — created here too so existing users don't need a re-migrate.
    con.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            role    TEXT NOT NULL,
            content TEXT NOT NULL
        )
    """)
    return con


def _history(con: sqlite3.Connection, limit: int) -> list[dict]:
    rows = con.execute(
        "SELECT ts, role, content FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _store(con: sqlite3.Connection, role: str, content: str) -> None:
    con.execute(
        "INSERT INTO chat_messages (ts, role, content) VALUES (?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), role, content),
    )
    con.commit()


def _rate_ok(user: str) -> bool:
    now = time.time()
    q = _RATE[user]
    while q and now - q[0] > RATE_LIMIT_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT_MESSAGES:
        return False
    q.append(now)
    return True


def _to_api_messages(history: list[dict]) -> list[dict]:
    """Claude requires a leading user turn and no consecutive same-role turns.

    Both can occur in stored history (e.g. a failed reply followed by a retry),
    so drop leading assistant turns and merge same-role runs.
    """
    merged: list[dict] = []
    for m in history:
        if not merged and m["role"] != "user":
            continue
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append({"role": m["role"], "content": m["content"]})
    return merged


def _profile_block(user: str) -> tuple[str, bool]:
    """Returns (rendered profile + linked-accounts block, needs_onboarding)."""
    import profile_editor
    p = profile_editor.read_profile(user)
    lines = [f"Training mode: {p['training_mode']} | goal mode: {p['goal_mode']}"
             f" | target weight: {p['target_weight_kg']}kg"
             f" | rate: {p['weight_rate_kg_per_week']}kg/wk"]
    if p["goal_text"]:
        lines.append(f"Goal: {p['goal_text']}")
    dur = p["target_duration_minutes"]
    lines.append(f"Session duration: weekday {dur.get('weekday')} min, weekend {dur.get('weekend')} min")
    lines.append("Main lifts:")
    for name, cfg in p["main_lifts"].items():
        bw = ", bodyweight" if cfg.get("is_bodyweight") else ""
        lines.append(f"  {name} → {cfg.get('hevy_name', name)} ({cfg['session_type']}): "
                     f"{cfg['target_sets']} sets of {cfg['rep_range'][0]}-{cfg['rep_range'][1]}, "
                     f"+{cfg['progression_kg']}kg{bw}")
    phases = profile_editor.focus_phase_state(user)
    lines.append("Anchor (focus) lifts — the lift each session type is built around:")
    for st in ("push", "pull", "legs", "arms"):
        live = phases.get(st)
        if live is None:
            if st in p["default_focus_lifts"]:
                lines.append(f"  {st}: {p['default_focus_lifts'][st]}")
            continue
        if live["phase"] == "complement" and live["complement_lift"]:
            lines.append(f"  {st}: {live['focus_lift']} — temporarily in a complement phase "
                         f"emphasising {live['complement_lift']} since {live['phase_started']} "
                         f"(anchor is progressing well; emphasis returns to it automatically)")
        else:
            lines.append(f"  {st}: {live['focus_lift']}")
    lines.append("Excluded exercises: " + (", ".join(p["excluded_exercises"]) or "none"))
    if p["skill_work"]:
        lines.append("Skill work: " + ", ".join(p["skill_work"]))

    withings_linked = (USERS_ROOT / user / "withings_token.json").exists()
    lines.append("Linked accounts:")
    lines.append(f"  Hevy: connected — sessions are delivered to routine folder"
                 f" {p['hevy_routine_folder_id']} in their Hevy app")
    lines.append("  Withings (bodyweight scale): "
                 + ("linked — weight syncs automatically"
                    if withings_linked else
                    "NOT linked — bodyweight tracking is off. Linking can't be done from "
                    "chat; the server admin runs the Withings sign-in with them."))
    return "\n".join(lines), p["needs_onboarding"]


def _regenerate_routine(user: str, force: bool) -> tuple[str, bool]:
    """Run the full engine (run.py) as a subprocess and summarize the outcome.

    A subprocess keeps the engine's config.activate / logging setup out of this
    process; sys.executable is the same venv python that launched us.
    """
    import json as _json
    started = time.time()
    cmd = [sys.executable, str(ROOT / "run.py"), "--user", user]
    if force:
        cmd.append("--force")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=REGEN_TIMEOUT_S, cwd=ROOT)
    except subprocess.TimeoutExpired:
        print(f"[chat] {user}: regenerate timed out after {REGEN_TIMEOUT_S}s")
        return ("NO ROUTINE POSTED — the engine timed out; tell the athlete to "
                "try again in a few minutes"), True

    workout_file = USERS_ROOT / user / "logs" / f"{date.today().isoformat()}_workout.json"
    posted = (proc.returncode == 0 and workout_file.exists()
              and workout_file.stat().st_mtime >= started)
    if posted:
        w = _json.loads(workout_file.read_text())
        lines = [f"Routine posted to their Hevy app: {w.get('title')}"]
        for ex in w.get("exercises", []):
            working = [s for s in ex.get("sets", []) if not s.get("is_warmup")]
            if working:
                top = max(s.get("weight_kg") or 0 for s in working)
                load = f" @ {top}kg" if top else " (bodyweight)"
                lines.append(f"  {ex['exercise_name']}: {len(working)}x{working[0].get('reps')}{load}")
            else:
                lines.append(f"  {ex['exercise_name']}")
        if w.get("reasoning"):
            lines.append(f"Engine reasoning: {w['reasoning']}")
        return "\n".join(lines), False

    if proc.returncode != 0:
        err_tail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-8:])
        print(f"[chat] {user}: regenerate failed rc={proc.returncode}:\n{err_tail}")
        return "NO ROUTINE POSTED — the engine hit an error; tell the athlete it didn't work", True

    tail = "\n".join((proc.stdout or "").strip().splitlines()[-12:])
    return ("NO ROUTINE POSTED — the engine declined to program a session. Its output is "
            "below; explain the reason to the athlete in plain words. Only retry with "
            "force=true if they explicitly insist.\n" + tail), False


def _review_block(user: str) -> str:
    """Render the Weekly Review into a compact text block for the coach to reason
    over — same numbers the Review tab shows."""
    import stats
    r = stats.weekly_review(user)

    def _wk(w, label):
        types = ", ".join(f"{t} {n}" for t, n in w["types"].items() if n) or "no sessions"
        bw = w.get("bodyweight_change")
        bw_txt = f", bodyweight {bw:+.1f}kg" if bw not in (None, 0) else ""
        return f"- {label}: {w['sessions']} sessions, {w['sets']} working sets ({types}){bw_txt}"

    f = r["frequency"]
    lines = [
        "WEEKLY REVIEW",
        _wk(r["this_week"], "This week"),
        _wk(r["prev_week"], "Last week"),
    ]
    if f["weeks_analysed"]:
        typical = ", ".join(f["typical_days"]) or "no consistent days"
        target = f" (they aim for {f['target_per_week']})" if f["target_per_week"] else ""
        lines.append(
            f"- Cadence over the last {f['weeks_analysed']} weeks: "
            f"{f['avg_per_week']} sessions/week on average{target}; usual days: {typical}")
    else:
        lines.append("- Not enough history yet to read a reliable cadence")

    s = r["suggestion"]
    if s:
        fit = ("matches their current split" if not s["changes_recommended"]
               else f"differs from their current {r['current_split']} "
                    f"({r['current_split_days']}x/week) split")
        lines.append(
            f"- Cadence-fit split suggestion: {s['name']} ({s['cadence']}, {s['per_muscle']}) "
            f"— {fit}. Rationale: {s['rationale']}")
    lines.append("This is a suggestion only — never change their split without explicit "
                 "confirmation, and use update_profile for any change they do confirm.")
    return "\n".join(lines)


def _run_tool(user: str, name: str, args: dict) -> tuple[str, bool]:
    """Execute one tool call. Returns (result_text, is_error)."""
    import json as _json
    import profile_editor
    try:
        if name == "search_hevy_exercises":
            matches = profile_editor.search_exercises(user, args.get("query", ""))
            if not matches:
                return "no matches — try a different search term", False
            return "\n".join(f"{m['title']}  ({m['muscle']}, {m['equipment']})" for m in matches), False
        if name == "update_profile":
            with _CONFIG_LOCK:   # serialize profile writes
                summaries = profile_editor.apply_operations(user, args.get("operations", []))
            print(f"[chat] {user}: profile updated — {'; '.join(summaries)}")
            return "Applied: " + "; ".join(summaries), False
        if name == "regenerate_routine":
            print(f"[chat] {user}: regenerating routine (force={bool(args.get('force'))})")
            return _regenerate_routine(user, bool(args.get("force")))
        if name == "get_weekly_review":
            print(f"[chat] {user}: pulling weekly review")
            with _CONFIG_LOCK:
                config.activate(user)
                _sync_recent(user)
            return _review_block(user), False
        return f"unknown tool {name!r}", True
    except profile_editor.ProfileEditError as e:
        return f"NO CHANGES APPLIED — {e} (fix and retry, or tell the athlete honestly)", True
    except Exception as e:
        print(f"[chat] {user}: tool {name} failed: {e}")
        return "NO CHANGES APPLIED — internal error; tell the athlete it didn't work", True


def _coach_reply(user: str, history: list[dict]) -> str:
    """Build athlete context under the config lock; call Claude outside it."""
    with _CONFIG_LOCK:
        config.activate(user)
        _sync_recent(user)
        athlete_block = format_athlete_context(build_context(), all_lifts=True)
        today_block = ""
        workout_file = Path(config.LOG_DIR) / f"{date.today().isoformat()}_workout.json"
        if workout_file.exists():
            today_block = ("\n## Today's prescription (already in their Hevy app)\n"
                           + workout_file.read_text())
        headers = _headers()

    profile_block, needs_onboarding = _profile_block(user)
    system = CHAT_SYSTEM.format(name=user.title(), athlete_block=athlete_block,
                                today_block=today_block, profile_block=profile_block,
                                onboarding_block=ONBOARDING_GUIDE if needs_onboarding else "")

    messages = _to_api_messages(history)
    for _ in range(MAX_TOOL_ROUNDS):
        resp = requests.post(
            ANTHROPIC_URL,
            headers=headers,
            json={
                "model":      CLAUDE_MODEL,
                "max_tokens": CHAT_MAX_TOKENS,
                "system":     system,
                "tools":      CHAT_TOOLS,
                "messages":   messages,
            },
            timeout=120,
        )
        if not resp.ok:
            try:
                err_msg = resp.json().get("error", {}).get("message", "")
            except ValueError:
                err_msg = ""
            _note_api_error(resp.status_code, err_msg or resp.reason)
            raise RuntimeError(f"Anthropic API {resp.status_code}: {err_msg or resp.reason}")
        _note_api_ok()
        data = resp.json()

        if data.get("stop_reason") != "tool_use":
            texts = [b["text"] for b in data["content"] if b["type"] == "text"]
            return "\n".join(texts).strip()

        messages.append({"role": "assistant", "content": data["content"]})
        results = []
        for block in data["content"]:
            if block["type"] != "tool_use":
                continue
            result, is_error = _run_tool(user, block["name"], block["input"] or {})
            results.append({"type": "tool_result", "tool_use_id": block["id"],
                            "content": result, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("tool loop exceeded MAX_TOOL_ROUNDS")


def _history_response(user: str):
    con = _db(user)
    try:
        return jsonify(_history(con, HISTORY_ON_PAGE))
    finally:
        con.close()


def _stats_response(user: str):
    import stats
    with _CONFIG_LOCK:            # _sync_recent needs the active user + serialised writes
        config.activate(user)
        _sync_recent(user)
    try:
        return jsonify(stats.stats_payload(user))
    except Exception as e:
        print(f"[chat] {user}: stats build failed: {e}")
        return jsonify({"error": "stats unavailable"}), 500


def _review_response(user: str):
    import stats
    with _CONFIG_LOCK:            # _sync_recent needs the active user + serialised writes
        config.activate(user)
        _sync_recent(user)
    try:
        return jsonify(stats.weekly_review(user))
    except Exception as e:
        print(f"[chat] {user}: review build failed: {e}")
        return jsonify({"error": "review unavailable"}), 500


def _chat_response(user: str):
    body    = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({"error": f"message too long (max {MAX_MESSAGE_CHARS} chars)"}), 400
    if not _rate_ok(user):
        return jsonify({"error": "rate limit reached — try again in a while"}), 429

    con = _db(user)
    try:
        _store(con, "user", message)
        history = _history(con, HISTORY_TO_MODEL)
        t0 = time.time()
        try:
            reply = _coach_reply(user, history)
        except Exception as e:
            print(f"[chat] {user}: coach call failed: {e}")
            return jsonify({"error": "coach unavailable — try again shortly"}), 502
        _store(con, "assistant", reply)
        print(f"[chat] {user}: {len(message)} chars in, {len(reply)} chars out, {time.time() - t0:.1f}s")
        return jsonify({"reply": reply})
    finally:
        con.close()


# ------------------------------------------------------- legacy token urls


@app.get("/u/<token>")
def chat_page(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return render_template("chat.html", user=user.title(), dev=(user == DEV_USER))


@app.get("/u/<token>/history")
def chat_history(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return _history_response(user)


@app.post("/u/<token>/chat")
def chat_post(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return _chat_response(user)


@app.get("/u/<token>/stats")
def chat_stats_page(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return render_template("stats.html", user=user.title(),
                           data_url=f"/u/{token}/stats/data", chat_url=f"/u/{token}")


@app.get("/u/<token>/stats/data")
def chat_stats_data(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return _stats_response(user)


@app.get("/u/<token>/review")
def chat_review_page(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return render_template("review.html", user=user.title(),
                           data_url=f"/u/{token}/review/data", chat_url=f"/u/{token}")


@app.get("/u/<token>/review/data")
def chat_review_data(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return _review_response(user)


@app.get("/u/<token>/devinfo")
def chat_devinfo(token: str):
    if _user_for(token) != DEV_USER:
        abort(404)
    return jsonify(_devinfo_payload())


@app.post("/u/<token>/devinfo/ping")
def chat_devinfo_ping(token: str):
    if _user_for(token) != DEV_USER:
        abort(404)
    return jsonify(_credit_ping())


# ------------------------------------------------------------ login + app


@app.get("/")
def root():
    return redirect("/app" if _session_user() else "/login", code=302)


@app.get("/login")
def login_form():
    return render_template("login.html", error=None, username="")


@app.post("/login")
def login_submit():
    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""

    if chat_auth.throttled(username):
        return render_template(
            "login.html", username=username,
            error="Too many attempts — try again in 15 minutes.",
        ), 429

    found = chat_auth.user_for_username(USERS_ROOT, username) if username else None
    if (
        found is None
        or not found[1].get("password_hash")
        or not chat_auth.verify_password(password, found[1]["password_hash"])
    ):
        chat_auth.record_failure(username)
        time.sleep(0.4)  # blunt the cost of online guessing
        return render_template(
            "login.html", username=username, error="Wrong username or password.",
        ), 401

    print(f"[chat] login: {username}")
    return _login_redirect(found[0], found[1]["password_hash"])


@app.get("/setup/<token>")
def setup_form(token: str):
    found = chat_auth.user_for_setup_token(USERS_ROOT, token)
    if found is None:
        abort(404)
    user, record = found
    return render_template(
        "setup.html", token=token, username=record["username"],
        name=user.title(), min_len=chat_auth.MIN_PASSWORD_LEN, error=None,
    )


@app.post("/setup")
def setup_submit():
    token = request.form.get("t") or ""
    found = chat_auth.user_for_setup_token(USERS_ROOT, token)
    if found is None:
        abort(404)
    user, record = found
    password = request.form.get("password") or ""
    if len(password) < chat_auth.MIN_PASSWORD_LEN:
        return render_template(
            "setup.html", token=token, username=record["username"],
            name=user.title(), min_len=chat_auth.MIN_PASSWORD_LEN,
            error=f"At least {chat_auth.MIN_PASSWORD_LEN} characters.",
        ), 400

    record["password_hash"] = chat_auth.hash_password(password)
    record["setup_token"] = None  # single-use
    chat_auth.save_auth(USERS_ROOT, user, record)
    print(f"[chat] password set: {record['username']} (other sessions signed out)")
    return _login_redirect(user, record["password_hash"])


@app.get("/app", strict_slashes=False)
def app_page():
    user = _session_user()
    if user is None:
        return redirect("/login", code=302)
    return render_template("chat.html", user=user.title(), dev=(user == DEV_USER))


@app.get("/app/history")
def app_history():
    user = _session_user()
    if user is None:
        abort(401)
    return _history_response(user)


@app.post("/app/chat")
def app_chat():
    user = _session_user()
    if user is None:
        abort(401)
    return _chat_response(user)


@app.get("/app/stats")
def app_stats_page():
    user = _session_user()
    if user is None:
        return redirect("/login", code=302)
    return render_template("stats.html", user=user.title(),
                           data_url="/app/stats/data", chat_url="/app")


@app.get("/app/stats/data")
def app_stats_data():
    user = _session_user()
    if user is None:
        abort(401)
    return _stats_response(user)


@app.get("/app/review")
def app_review_page():
    user = _session_user()
    if user is None:
        return redirect("/login", code=302)
    return render_template("review.html", user=user.title(),
                           data_url="/app/review/data", chat_url="/app")


@app.get("/app/review/data")
def app_review_data():
    user = _session_user()
    if user is None:
        abort(401)
    return _review_response(user)


@app.get("/app/devinfo")
def app_devinfo():
    if _session_user() != DEV_USER:
        abort(404)
    return jsonify(_devinfo_payload())


@app.post("/app/devinfo/ping")
def app_devinfo_ping():
    if _session_user() != DEV_USER:
        abort(404)
    return jsonify(_credit_ping())


@app.post("/app/invite/new")
def app_invite_new():
    """Dev-only: mint a single-use signup link. The client turns the returned
    path into a full URL with its own origin (the funnel host)."""
    if _session_user() != DEV_USER:
        abort(404)
    return jsonify({"path": f"/join/{_new_invite()}"})


def _ago(day: str | None) -> str:
    if not day:
        return "never"
    try:
        n = (date.today() - date.fromisoformat(day[:10])).days
    except ValueError:
        return "—"
    return "today" if n == 0 else ("yesterday" if n == 1 else f"{n}d ago")


def _users_overview() -> list[dict]:
    """Basic per-user info for the dev users page: activity, sessions, status."""
    import profile_editor
    rows = []
    for d in sorted(USERS_ROOT.glob("*/")):
        user = d.name
        if user.startswith("_") or user.startswith(".") or not (d / "gym.db").is_file():
            continue
        rec = chat_auth.load_auth(USERS_ROOT, user) or {}
        con = sqlite3.connect(d / "gym.db")
        con.row_factory = sqlite3.Row
        try:
            last_chat = con.execute("SELECT MAX(ts) FROM chat_messages").fetchone()[0]
        except sqlite3.OperationalError:
            last_chat = None
        try:
            srow = con.execute(
                "SELECT MAX(date) d, COUNT(DISTINCT date) n FROM sets "
                "WHERE session_type != 'unknown' AND session_type IS NOT NULL"
            ).fetchone()
            last_workout, sessions = srow["d"], srow["n"]
        except sqlite3.OperationalError:
            last_workout, sessions = None, 0
        con.close()
        try:
            onboarding = profile_editor.read_profile(user)["needs_onboarding"]
        except Exception:
            onboarding = None
        last_active = max([x for x in (last_chat and last_chat[:10], last_workout) if x], default=None)
        rows.append({
            "user": user,
            "username": rec.get("username") or "—",
            "has_password": bool(rec.get("password_hash")),
            "status": "setup pending" if onboarding else ("active" if onboarding is False else "?"),
            "last_active": _ago(last_active),
            "last_workout": _ago(last_workout),
            "sessions": sessions,
            "withings": (d / "withings_token.json").is_file(),
        })
    rows.sort(key=lambda r: (r["last_active"] == "never", r["user"]))
    return rows


@app.get("/app/users")
def app_users():
    if _session_user() != DEV_USER:
        abort(404)
    users = _users_overview()
    return render_template("users.html", users=users, count=len(users))


# -------------------------------------------------- new-user self-signup


def _render_join(token: str, error=None, name="", status=200):
    return render_template(
        "join.html", token=token, name=name, error=error,
        min_len=chat_auth.MIN_PASSWORD_LEN,
        hevy_url="https://hevy.com/settings?developer",
    ), status


@app.get("/join/<token>")
def join_form(token: str):
    if not _invite_open(token):
        abort(404)
    return _render_join(token)


@app.post("/join")
def join_submit():
    import add_user
    token = request.form.get("t") or ""
    if not _invite_open(token):
        abort(404)
    name     = (request.form.get("name") or "").strip().lower()
    password = request.form.get("password") or ""
    hevy_key = (request.form.get("hevy_key") or "").strip()

    if not add_user._NAME_RE.match(name):
        return _render_join(token, "Name: lowercase letters, digits, _ or - (start with a letter).", name, 400)
    if (USERS_ROOT / name).exists():
        return _render_join(token, f"The name '{name}' is taken — pick another.", name, 400)
    if len(password) < chat_auth.MIN_PASSWORD_LEN:
        return _render_join(token, f"Password must be at least {chat_auth.MIN_PASSWORD_LEN} characters.", name, 400)
    if not hevy_key or not add_user._verify_hevy_key(hevy_key):
        return _render_join(token, "That Hevy API key didn't work — check it and try again.", name, 400)

    # Use an existing routine folder if the account has one; else leave unset.
    folders   = add_user._list_hevy_folders(hevy_key)
    folder_id = folders[0]["id"] if folders else None

    try:
        with _CONFIG_LOCK:               # create_user activates config + seeds the DB
            add_user.create_user(name, hevy_key, folder_id)
    except ValueError as e:
        return _render_join(token, str(e), name, 400)
    except Exception as e:
        print(f"[chat] join: create_user failed for {name!r}: {e}")
        return _render_join(token, "Something went wrong creating the account — try again.", name, 500)

    password_hash = chat_auth.hash_password(password)
    chat_auth.save_auth(USERS_ROOT, name, {
        "username": f"{name}-gym", "password_hash": password_hash, "setup_token": None,
    })
    _consume_invite(token, name)

    global TOKENS
    TOKENS = _load_tokens()              # register the new user's chat token
    print(f"[chat] new user via invite: {name}")
    return _login_redirect(name, password_hash)


@app.post("/logout")
def logout():
    resp = make_response(redirect("/login", code=303))
    resp.delete_cookie(chat_auth.SESSION_COOKIE, path="/")
    return resp


if __name__ == "__main__":
    if not TOKENS:
        print("[chat] No CHAT_TOKENs found in users/*/secrets.env — nothing to serve.")
        raise SystemExit(1)
    print(f"[chat] {datetime.now().isoformat(timespec='seconds')} — "
          f"serving {len(TOKENS)} user(s) on http://{HOST}:{PORT}")
    from waitress import serve
    serve(app, host=HOST, port=PORT, threads=4)
