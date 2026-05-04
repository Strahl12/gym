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

# ── Training mode ──────────────────────────────────────────────────────────
# Options: "strength" | "hypertrophy" | "powerlifting"
#   strength:     4–6 reps, 80–90% 1RM, 2–4 min rest, linear progression
#   hypertrophy:  8–12 reps, 65–80% 1RM, 60–90s rest, volume focus
#   powerlifting: 1–5 reps, 85–95% 1RM, 4–6 min rest on main lifts, peaking focus
TRAINING_MODE = "strength"

# ── Training goals ─────────────────────────────────────────────────────────
GOAL = """
Training split: Push / Pull / Legs / Arms (PPL+Arms).
Target frequency: 5 sessions per week.
All weights in kg.
"""

# ── Body composition ───────────────────────────────────────────────────────
# GOAL_MODE: "cut" | "bulk" | "maintain"
#   cut:      slight caloric deficit — reduce accessory volume, prioritise compounds,
#             avoid excessive fatigue; don't chase PRs on accessories
#   bulk:     caloric surplus — push accessory volume, progress aggressively
#   maintain: balanced — steady progression, standard volume
GOAL_MODE = "maintain"
TARGET_WEIGHT_KG: float | None = None          # goal bodyweight
WEIGHT_RATE_KG_PER_WEEK: float | None = None   # negative = losing, positive = gaining

# ── Session duration (weekday vs weekend) ──────────────────────────────────
# Weekday sessions are shorter — same exercise count, tighter rest periods.
TARGET_DURATION_MINUTES = {
    "weekday": 60,
    "weekend": 90,
}

# ── Main lifts + Hevy exercise template IDs ───────────────────────────────
# Template IDs must be fetched from Hevy API (see hevy.py:get_template_id)
# and filled in here after first run.
MAIN_LIFTS = {
    "Incline Barbell Bench Press": {
        "hevy_template_id": "50DFDFAB",
        "hevy_name": "Incline Bench Press (Barbell)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
    "Strict Military Press": {
        "hevy_template_id": "7B8D84E8",
        "hevy_name": "Overhead Press (Barbell)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
    "Pull Up": {
        "hevy_template_id": "1B2B1E7C",
        "hevy_name": "Pull Up",
        "session_type": "pull",
        "target_sets": 4,
        "rep_range": (4, 8),
        "progression_kg": 2.5,      # added weight once BW sets are easy
        "is_bodyweight": True,
    },
    "Weighted Dip": {
        "hevy_template_id": "29472BE1",
        "hevy_name": "Chest Dip (Weighted)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (6, 10),
        "progression_kg": 2.5,
        "is_bodyweight": True,
    },
    "Front Squat": {
        "hevy_template_id": "5046D0A9",
        "hevy_name": "Front Squat",
        "session_type": "legs",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
}

# Session type → which main lifts belong there
SESSION_LIFTS = {
    "push": ["Incline Barbell Bench Press", "Strict Military Press", "Weighted Dip"],
    "pull": ["Pull Up"],
    "legs": ["Front Squat"],
    "arms": [],  # accessory-only session
}

# Hevy routine folder — routines are posted into this folder
HEVY_ROUTINE_FOLDER_ID = 2770378   # "Dynamic"

# Plateau detection: flag if e1RM hasn't improved across this many sessions
PLATEAU_SESSIONS = 4

# Fixed cycle order for session type rotation
SESSION_CYCLE = ["push", "pull", "legs", "arms"]

# ── Focus lift system ──────────────────────────────────────────────────────
# Primary lift to progress per session type. Override with --set-focus.
DEFAULT_FOCUS_LIFTS = {
    "push": "Incline Barbell Bench Press",
    "pull": "Pull Up",
    "legs": "Front Squat",
    "arms": "Close Grip Bench Press",
}

# Complementary lifts: when focus lift is progressing well, shift emphasis
# to these to build supporting strength before returning to the focus lift.
LIFT_COMPLEMENTS = {
    "Incline Barbell Bench Press": ["Weighted Dip", "Strict Military Press"],
    "Strict Military Press":  ["Barbell Bench Press", "Weighted Dip"],
    "Weighted Dip":           ["Close Grip Bench Press", "Barbell Bench Press"],
    "Pull Up":                ["Barbell Row", "Deadlift"],
    "Deadlift":               ["Romanian Deadlift", "Barbell Row"],
    "Front Squat":            ["Romanian Deadlift", "Leg Press"],
    "Romanian Deadlift":      ["Front Squat", "Good Morning (Barbell)"],
    "Close Grip Bench Press": ["Weighted Dip", "Triceps Pushdown"],
    "Barbell Curl":           ["Hammer Curl", "Chin Up"],
}

# Sessions of consecutive e1RM improvement before entering complement phase
COMPLEMENT_TRIGGER_SESSIONS = 3
# Days to stay in complement phase before returning to focus
COMPLEMENT_PHASE_DAYS = 21

# Minimum days between training the same muscle group
MIN_RECOVERY_DAYS = 3

# Maximum consecutive training days before a mandatory rest day
MAX_CONSECUTIVE_DAYS = 5

# ── Equipment increments ───────────────────────────────────────────────────
# Minimum realistic weight jump per equipment type. Used by Claude when
# calculating progressions and prescribing weights.
EQUIPMENT_INCREMENTS = {
    "barbell":   2.5,   # 1.25kg plate each side
    "cable":     2.5,   # standard cable stack step
    "dumbbell":  2.0,   # one increment = 2kg (next dumbbell pair)
    "machine":   5.0,   # typical plate/pin increment
}

# ── Exercise exclusions ────────────────────────────────────────────────────
# Exercises that should never be prescribed. Add names exactly as they appear
# in the Hevy exercise library (check exercises.json).
# Use --exclude "exercise name" to append from the command line.
EXCLUDED_EXERCISES: list[str] = [
    "Calf Raise (Barbell)",
]

# ── Creator content ingestion ──────────────────────────────────────────────
# YouTube channels whose content informs exercise selection.
# weight: how much to trust this creator relative to others (1.0 = full trust).
# channel_id: from the channel URL — youtube.com/channel/CHANNEL_ID
TRUSTED_CREATORS: list[dict] = [
    {"name": "Jeff Nippard", "channel_id": "UCjTp-nBKswYLumqmVeBPwYw", "weight": 1.0},
]

# Only count videos published within this window for scoring
CREATOR_SCORE_LOOKBACK_DAYS = 365

# Minimum weighted score for an exercise to surface to Claude
CREATOR_SCORE_MIN = 0.3
