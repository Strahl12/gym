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
