"""
withings.py — Pulls today's bodyweight from the Withings API and writes to DB.

Auth: Withings uses OAuth 2.0. You need to:
  1. Register an app at https://developer.withings.com
  2. Do the one-time OAuth dance to get a refresh token
  3. Store WITHINGS_REFRESH_TOKEN, WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET as env vars

This module auto-refreshes the access token on each run.
"""
import sqlite3
import requests
import json
import os
from datetime import date, datetime, timedelta
from typing import Optional
import config

TOKEN_URL     = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL   = "https://wbsapi.withings.net/measure"
AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_CACHE   = os.path.expanduser("~/.withings_token.json")
OAUTH_PORT    = int(os.environ.get("WITHINGS_OAUTH_PORT", "8765"))
OAUTH_SCOPE   = "user.metrics"


# ── Token management ───────────────────────────────────────────────────────

def _load_cached_token() -> Optional[dict]:
    if os.path.exists(TOKEN_CACHE):
        with open(TOKEN_CACHE) as f:
            return json.load(f)
    return None


def _save_token(token: dict) -> None:
    with open(TOKEN_CACHE, "w") as f:
        json.dump(token, f)


def _refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "action":        "requesttoken",
        "grant_type":    "refresh_token",
        "client_id":     config.WITHINGS_CLIENT_ID,
        "client_secret": config.WITHINGS_CLIENT_SECRET,
        "refresh_token": refresh_token,
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 0:
        raise RuntimeError(f"Withings token refresh failed: {body}")
    token = body["body"]
    _save_token(token)
    return token


def get_access_token() -> str:
    """Returns a valid access token, refreshing if needed."""
    cached = _load_cached_token()

    # If cached token exists and was fetched recently, use it
    if cached:
        expires_at = cached.get("expires_in", 0) + cached.get("fetched_at", 0)
        if expires_at > datetime.now().timestamp() + 300:   # 5 min buffer
            return cached["access_token"]

    # Refresh
    refresh_token = (
        (cached or {}).get("refresh_token")
        or config.WITHINGS_REFRESH_TOKEN
    )
    if not refresh_token:
        raise RuntimeError(
            "No Withings refresh token. Set WITHINGS_REFRESH_TOKEN env var."
        )

    token = _refresh_access_token(refresh_token)
    token["fetched_at"] = datetime.now().timestamp()
    _save_token(token)
    return token["access_token"]


# ── OAuth bootstrap (one-time) ─────────────────────────────────────────────

def _exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "action":        "requesttoken",
        "grant_type":    "authorization_code",
        "client_id":     config.WITHINGS_CLIENT_ID,
        "client_secret": config.WITHINGS_CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  redirect_uri,
    })
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 0:
        raise RuntimeError(f"Withings code exchange failed: {body}")
    return body["body"]


def start_oauth(port: int = OAUTH_PORT) -> None:
    """
    Run the one-time Withings OAuth dance:
      1. Open the authorization URL in the browser
      2. Spin up a local listener on `port` to receive the callback
      3. Exchange the code for tokens and save to TOKEN_CACHE

    The redirect URI registered for your Withings app MUST be:
        http://localhost:{port}/callback
    """
    import http.server
    import secrets
    import threading
    import urllib.parse
    import webbrowser

    if not (config.WITHINGS_CLIENT_ID and config.WITHINGS_CLIENT_SECRET):
        raise RuntimeError(
            "WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET must be set as env vars. "
            "Register an app at https://developer.withings.com first."
        )

    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     config.WITHINGS_CLIENT_ID,
        "scope":         OAUTH_SCOPE,
        "redirect_uri":  redirect_uri,
        "state":         state,
    })

    received: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            received["code"]  = params.get("code",  [None])[0]
            received["state"] = params.get("state", [None])[0]
            received["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("<h2>Withings authorisation received.</h2>"
                   "<p>You can close this tab.</p>") if received["code"] else \
                  f"<h2>Authorisation failed.</h2><pre>{qs}</pre>"
            self.wfile.write(msg.encode())

        def log_message(self, *args, **kwargs):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"[withings] Opening browser for authorisation...")
    print(f"[withings] Redirect URI registered with Withings must be: {redirect_uri}")
    print(f"[withings] If the browser does not open, paste this URL manually:\n  {auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    # Wait for callback (timeout after 5 minutes)
    import time
    deadline = time.time() + 300
    while not received and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    if received.get("error"):
        raise RuntimeError(f"Withings auth error: {received['error']}")
    if not received.get("code"):
        raise RuntimeError("Timed out waiting for Withings callback.")
    if received.get("state") != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF; aborting.")

    print("[withings] Code received, exchanging for tokens...")
    token = _exchange_code_for_token(received["code"], redirect_uri)
    token["fetched_at"] = datetime.now().timestamp()
    _save_token(token)
    print(f"[withings] Refresh token saved to {TOKEN_CACHE}.")
    print(f"[withings] Try: python -c 'import withings; withings.sync_to_db(days=7)'")


# ── Measurement fetch ──────────────────────────────────────────────────────

def fetch_body_measurements(days: int = 7) -> list[dict]:
    """
    Fetch weight, muscle mass, and body fat from the last N days.
    Returns list of {date, weight_kg, muscle_mass_kg, body_fat_pct} — fields may be None.

    Withings measure types:
      1  = weight (kg)
      6  = fat ratio (%)
      76 = muscle mass (kg)
    """
    access_token = get_access_token()
    startdate    = int((datetime.now() - timedelta(days=days)).timestamp())

    resp = requests.post(MEASURE_URL, data={
        "action":       "getmeas",
        "meastypes":    "1,6,76",     # weight, fat ratio, muscle mass
        "category":     1,
        "startdate":    startdate,
        "access_token": access_token,
    })
    resp.raise_for_status()
    body = resp.json()

    if body.get("status") != 0:
        raise RuntimeError(f"Withings measure fetch failed: {body}")

    by_date: dict[str, dict] = {}
    for group in body["body"].get("measuregrps", []):
        d = datetime.fromtimestamp(group["date"]).date().isoformat()
        if d not in by_date:
            by_date[d] = {"date": d, "weight_kg": None, "muscle_mass_kg": None, "body_fat_pct": None}
        for m in group["measures"]:
            val = round(m["value"] * (10 ** m["unit"]), 2)
            if m["type"] == 1:
                by_date[d]["weight_kg"] = val
            elif m["type"] == 6:
                by_date[d]["body_fat_pct"] = val
            elif m["type"] == 76:
                by_date[d]["muscle_mass_kg"] = val

    return sorted(by_date.values(), key=lambda x: x["date"])


# ── DB write ───────────────────────────────────────────────────────────────

def sync_to_db(days: int = 30) -> int:
    """
    Fetch recent Withings measurements and upsert into bodyweight table.
    Returns number of rows inserted/updated.
    """
    measurements = fetch_body_measurements(days=days)
    if not measurements:
        print("[withings] No measurements fetched.")
        return 0

    con = sqlite3.connect(config.DB_PATH)
    count = 0
    for m in measurements:
        con.execute("""
            INSERT INTO bodyweight (date, weight_kg, muscle_mass_kg, body_fat_pct, source)
            VALUES (?, ?, ?, ?, 'withings')
            ON CONFLICT(date) DO UPDATE SET
                weight_kg      = COALESCE(excluded.weight_kg,      weight_kg),
                muscle_mass_kg = COALESCE(excluded.muscle_mass_kg, muscle_mass_kg),
                body_fat_pct   = COALESCE(excluded.body_fat_pct,   body_fat_pct)
        """, (m["date"], m["weight_kg"], m["muscle_mass_kg"], m["body_fat_pct"]))
        count += 1
    con.commit()
    con.close()

    latest = measurements[-1]
    parts  = [f"{latest['weight_kg']}kg"]
    if latest["muscle_mass_kg"]:
        parts.append(f"muscle {latest['muscle_mass_kg']}kg")
    if latest["body_fat_pct"]:
        parts.append(f"BF {latest['body_fat_pct']}%")
    print(f"[withings] Synced {count} measurements. Latest ({latest['date']}): {' | '.join(parts)}")
    return count


if __name__ == "__main__":
    sync_to_db(days=30)
