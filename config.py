"""
Central config. Edit this file to update goals, lift definitions, and API keys.
All API keys should be set as environment variables — never hardcoded.
"""
import os
from dataclasses import dataclass, field

# ── Paths ──────────────────────────────────────────────────────────────────
DB_PATH = "/Users/johnparry/projects/projects/personal/gym/gym.db"

# ── API Keys (set as env vars) ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HEVY_API_KEY      = os.environ.get("HEVY_API_KEY", "")
WITHINGS_ACCESS_TOKEN  = os.environ.get("WITHINGS_ACCESS_TOKEN", "")
WITHINGS_REFRESH_TOKEN = os.environ.get("WITHINGS_REFRESH_TOKEN", "")
WITHINGS_CLIENT_ID     = os.environ.get("WITHINGS_CLIENT_ID", "")
WITHINGS_CLIENT_SECRET = os.environ.get("WITHINGS_CLIENT_SECRET", "")

# ── Training goals ─────────────────────────────────────────────────────────
GOAL = """
Primary goal: get stronger on the main lifts — not hypertrophy, not conditioning.
Training split: Push / Pull / Legs / Arms (PPL+Arms).
Target frequency: 5 sessions per week.
All weights in kg.
"""

# ── Main lifts + Hevy exercise template IDs ───────────────────────────────
# Template IDs must be fetched from Hevy API (see hevy.py:get_template_id)
# and filled in here after first run.
MAIN_LIFTS = {
    "Barbell Bench Press": {
        "hevy_template_id": None,   # fill after fetching
        "hevy_name": "Barbell Bench Press",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),        # strength rep range
        "progression_kg": 2.5,      # increment when progressing
    },
    "Strict Military Press": {
        "hevy_template_id": None,
        "hevy_name": "Overhead Press (Barbell)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
    "Pull Up": {
        "hevy_template_id": None,
        "hevy_name": "Pull Up",
        "session_type": "pull",
        "target_sets": 4,
        "rep_range": (4, 8),
        "progression_kg": 2.5,      # added weight once BW sets are easy
        "is_bodyweight": True,
    },
    "Weighted Dip": {
        "hevy_template_id": None,
        "hevy_name": "Dip",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (6, 10),
        "progression_kg": 2.5,
        "is_bodyweight": True,
    },
    "Front Squat": {
        "hevy_template_id": None,
        "hevy_name": "Front Squat (Barbell)",
        "session_type": "legs",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
}

# Session type → which main lifts belong there
SESSION_LIFTS = {
    "push": ["Barbell Bench Press", "Strict Military Press", "Weighted Dip"],
    "pull": ["Pull Up"],
    "legs": ["Front Squat"],
    "arms": [],  # accessory-only session
}

# Plateau detection: flag if e1RM hasn't improved across this many sessions
PLATEAU_SESSIONS = 4

# Minimum days between training the same muscle group
MIN_RECOVERY_DAYS = 1
