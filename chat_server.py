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

## Athlete data (generated fresh for this message — the same context the morning engine sees)
{athlete_block}
{today_block}"""

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

    system = CHAT_SYSTEM.format(name=user.title(), athlete_block=athlete_block,
                                today_block=today_block)

    resp = requests.post(
        ANTHROPIC_URL,
        headers=headers,
        json={
            "model":      CLAUDE_MODEL,
            "max_tokens": CHAT_MAX_TOKENS,
            "system":     system,
            "messages":   _to_api_messages(history),
        },
        timeout=120,
    )
    if not resp.ok:
        try:
            err_msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            err_msg = ""
        raise RuntimeError(f"Anthropic API {resp.status_code}: {err_msg or resp.reason}")
    return resp.json()["content"][0]["text"].strip()


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
