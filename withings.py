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

TOKEN_URL    = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL  = "https://wbsapi.withings.net/measure"
TOKEN_CACHE  = os.path.expanduser("~/.withings_token.json")


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


# ── Measurement fetch ──────────────────────────────────────────────────────

def fetch_weight_measurements(days: int = 7) -> list[dict]:
    """
    Fetch weight measurements from the last N days.
    Returns list of {date, weight_kg}.
    """
    access_token = get_access_token()
    startdate    = int((datetime.now() - timedelta(days=days)).timestamp())

    resp = requests.post(MEASURE_URL, data={
        "action":       "getmeas",
        "meastypes":    1,            # 1 = body weight
        "category":     1,            # real measurements only
        "startdate":    startdate,
        "access_token": access_token,
    })
    resp.raise_for_status()
    body = resp.json()

    if body.get("status") != 0:
        raise RuntimeError(f"Withings measure fetch failed: {body}")

    results = []
    for group in body["body"].get("measuregrps", []):
        ts = group["date"]
        for m in group["measures"]:
            if m["type"] == 1:   # weight
                # Withings returns value * 10^unit
                weight_kg = m["value"] * (10 ** m["unit"])
                results.append({
                    "date":      datetime.fromtimestamp(ts).date().isoformat(),
                    "weight_kg": round(weight_kg, 2),
                })

    return sorted(results, key=lambda x: x["date"])


# ── DB write ───────────────────────────────────────────────────────────────

def sync_to_db(days: int = 30) -> int:
    """
    Fetch recent Withings measurements and upsert into bodyweight table.
    Returns number of rows inserted/updated.
    """
    measurements = fetch_weight_measurements(days=days)
    if not measurements:
        print("[withings] No measurements fetched.")
        return 0

    con = sqlite3.connect(config.DB_PATH)
    count = 0
    for m in measurements:
        con.execute("""
            INSERT INTO bodyweight (date, weight_kg, source)
            VALUES (?, ?, 'withings')
            ON CONFLICT(date) DO UPDATE SET weight_kg = excluded.weight_kg
        """, (m["date"], m["weight_kg"]))
        count += 1
    con.commit()
    con.close()

    print(f"[withings] Synced {count} measurements to DB.")
    print(f"           Latest: {measurements[-1]['date']} — {measurements[-1]['weight_kg']}kg")
    return count


if __name__ == "__main__":
    sync_to_db(days=30)
