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
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, render_template, request

import config
from context import build_context
from claude_api import ANTHROPIC_URL, CLAUDE_MODEL, _headers, format_athlete_context

ROOT       = Path(__file__).parent
USERS_ROOT = ROOT / "users"

HOST, PORT          = "127.0.0.1", 8090
HISTORY_TO_MODEL    = 20      # messages of continuity sent to Claude
HISTORY_ON_PAGE     = 100     # messages rendered on page load
MAX_MESSAGE_CHARS   = 2000
RATE_LIMIT_MESSAGES = 30      # per user...
RATE_LIMIT_WINDOW_S = 3600    # ...per hour — caps the Anthropic bill
CHAT_MAX_TOKENS     = 1024

CHAT_SYSTEM = """You are {name}'s strength coach — the same AI that writes their daily gym programming.
Answer questions about their training using the athlete data below.

Rules:
- Be concise and specific to the data. Plain text only — no markdown headings or tables
  (replies render in a small chat bubble). Weights in kg.
- You cannot modify routines or log anything from this chat. If they want today's session
  changed, tell them to edit it in the Hevy app. If they want future programming to behave
  differently, tell them to add an exercise note in Hevy starting with "NOTE:" — the morning
  engine reads those as directives.
- However, what they tell you HERE about their readiness — illness, poor sleep, injury,
  soreness, stress, limited time — IS read by the morning engine (it sees chat messages from
  the last 48h). Acknowledge such reports and confirm they'll be factored into the next
  session. No special format needed; they just have to mention it before the morning run.
- If the data doesn't answer their question, say so rather than guessing.

## Changing training goals
You CAN change their training profile — main lifts, focus lifts, training mode,
goal mode, target weight — via the update_profile tool. This is a guided process:
- When they start talking about changing a goal or lift, walk them through what the
  app needs, one or two questions at a time. For a new main lift that means: which
  exact Hevy exercise (use search_hevy_exercises and confirm the title with them),
  which session type (push/pull/legs/arms), sets, rep range, and progression
  increment. Suggest sensible defaults from their data instead of interrogating —
  e.g. "4 sets of 4-6 reps, +2.5kg progression — sound good?".
- Before applying, state the exact change in one message and get an explicit yes.
  NEVER call update_profile without the athlete confirming in this conversation.
- Changes take effect from the next morning's programming — say so after applying.
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
]

MAX_TOOL_ROUNDS = 6

app = Flask(__name__)

_CONFIG_LOCK = threading.Lock()          # config.activate() mutates process-global state
_RATE: dict[str, deque] = defaultdict(deque)


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


def _user_for(token: str) -> str | None:
    for known, user in TOKENS.items():
        if hmac.compare_digest(known, token):
            return user
    return None


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
    lines.append("Focus lifts: " + ", ".join(f"{st}={n}" for st, n in p["default_focus_lifts"].items()))
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
            raise RuntimeError(f"Anthropic API {resp.status_code}: {err_msg or resp.reason}")
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


@app.get("/u/<token>")
def chat_page(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    return render_template("chat.html", user=user.title())


@app.get("/u/<token>/history")
def chat_history(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
    con = _db(user)
    try:
        return jsonify(_history(con, HISTORY_ON_PAGE))
    finally:
        con.close()


@app.post("/u/<token>/chat")
def chat_post(token: str):
    user = _user_for(token)
    if user is None:
        abort(404)
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


if __name__ == "__main__":
    if not TOKENS:
        print("[chat] No CHAT_TOKENs found in users/*/secrets.env — nothing to serve.")
        raise SystemExit(1)
    print(f"[chat] {datetime.now().isoformat(timespec='seconds')} — "
          f"serving {len(TOKENS)} user(s) on http://{HOST}:{PORT}")
    from waitress import serve
    serve(app, host=HOST, port=PORT, threads=4)
