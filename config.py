"""
Central config. Edit this file to update goals, lift definitions, and API keys.
All API keys should be set as environment variables — never hardcoded.

Secrets can also be placed in a `secrets.env` file alongside this module
(KEY=VALUE per line). It loads automatically and never overrides anything
already set in the real environment.
"""
import os
from pathlib import Path
from dataclasses import dataclass, field


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader — no external dep. Real env vars take precedence."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ.setdefault(key, val)


_load_dotenv(Path(__file__).parent / "secrets.env")

# ── Paths ──────────────────────────────────────────────────────────────────
DB_PATH = "/Users/johnparry/projects/projects/personal/gym/gym.db"

# ── API Keys (set as env vars or in secrets.env) ───────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HEVY_API_KEY      = os.environ.get("HEVY_API_KEY", "")
WITHINGS_ACCESS_TOKEN  = os.environ.get("WITHINGS_ACCESS_TOKEN", "")
WITHINGS_REFRESH_TOKEN = os.environ.get("WITHINGS_REFRESH_TOKEN", "")
WITHINGS_CLIENT_ID     = os.environ.get("WITHINGS_CLIENT_ID", "")
WITHINGS_CLIENT_SECRET = os.environ.get("WITHINGS_CLIENT_SECRET", "")

# ── Training mode ──────────────────────────────────────────────────────────
# TRAINING_MODE: "strength" | "hypertrophy" | "mixed" — decoupled from GOAL_MODE.
#   strength:    1–6 rep range, load 80–95% 1RM, longer rests, lower volume
#   hypertrophy: 6–12 rep range, load 65–80% 1RM, moderate rests, higher volume
#   mixed:       blend — main lifts in strength range, accessories in hypertrophy range
TRAINING_MODE = "hypertrophy"

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
GOAL_MODE = "bulk"
TARGET_WEIGHT_KG: float | None = 95.0          # goal bodyweight
WEIGHT_RATE_KG_PER_WEEK: float | None = 0.25   # lean-bulk pace (~1kg/month)

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

# Recurring non-gym activities live in recurring_activities.json (managed via
# `python run.py --activity-add NAME WEEKDAY` / --activity-remove / --activity-list).
# See activities.py for the JSON-backed store and template defaults.

# ── Equipment increments ───────────────────────────────────────────────────
EQUIPMENT_INCREMENTS = {
    "barbell":   2.5,
    "cable":     2.5,
    "dumbbell":  2.0,
    "machine":   5.0,
}

# ── Progression rules ──────────────────────────────────────────────────────
PROGRESSION = {
    "increase_kg":           2.5,   # add when all sets hit top of rep range
    "max_increase_kg":       5.0,   # hard cap per session
    "plateau_reset_pct":     0.90,  # drop to 90% on plateau
    "plateau_reps":          (6, 8),
    "deload_threshold_days": 14,    # absence before deload treatment
    "deload_weight_pct":     0.80,
    "warmup_pcts":           [0.50, 0.75],
}

# ── Session slot templates ─────────────────────────────────────────────────
# Each slot: movement_pattern + compound flag drive exercise selection from
# the priority list. "fixed" overrides selection entirely.
# "muscle" narrows selection within a pattern (e.g. shoulders vs upper_back
# both share shoulder_abduction). "exclude" blocks specific exercise names.
SESSION_TEMPLATES: dict[str, list[dict]] = {
    "push": [
        {"slot": "chest_compound",    "movement_pattern": "horizontal_push",    "fixed": "Incline Barbell Bench Press"},
        {"slot": "shoulder_compound", "movement_pattern": "vertical_push",      "fixed": "Strict Military Press"},
        {"slot": "tricep_compound",   "movement_pattern": "elbow_extension",    "fixed": "Weighted Dip"},
        {"slot": "lateral_raise",     "movement_pattern": "shoulder_abduction", "muscle": "shoulders"},
        {"slot": "tricep_isolation",  "movement_pattern": "elbow_extension",    "is_compound": False},
    ],
    "pull": [
        {"slot": "hip_hinge",         "movement_pattern": "hip_hinge",          "is_compound": True},
        {"slot": "horizontal_row",    "movement_pattern": "horizontal_pull",    "is_compound": True},
        {"slot": "vertical_pull",     "movement_pattern": "vertical_pull",      "fixed": "Pull Up"},
        {"slot": "lat_accessory",     "movement_pattern": "vertical_pull",      "is_compound": False},
        {"slot": "rear_delt",         "movement_pattern": "shoulder_abduction", "muscle": "upper_back"},
    ],
    "legs": [
        {"slot": "quad_compound",     "movement_pattern": "quad_dominant",      "fixed": "Front Squat"},
        {"slot": "posterior_chain",   "movement_pattern": "hip_hinge",          "is_compound": True},
        {"slot": "quad_accessory",    "movement_pattern": "quad_dominant",      "is_compound": False},
        {"slot": "calf",              "movement_pattern": "ankle_plantarflexion"},
    ],
    "arms": [
        {"slot": "tricep_compound",   "movement_pattern": "elbow_extension",    "is_compound": True,
         "exclude": ["Barbell Bench Press", "Strict Military Press"]},
        {"slot": "bicep_compound",    "movement_pattern": "elbow_flexion",      "is_compound": True},
        {"slot": "tricep_isolation",  "movement_pattern": "elbow_extension",    "is_compound": False},
        {"slot": "bicep_isolation",   "movement_pattern": "elbow_flexion",      "is_compound": False},
        {"slot": "optional_isolation","movement_pattern": None,                 "optional": True},
    ],
}

# ── Session timing estimates (minutes per exercise type) ───────────────────
SESSION_TIME_ESTIMATES = {
    "warmup_general":     10,   # fixed overhead, not an exercise
    "main_barbell":       20,   # includes 2 warmup sets
    "main_bodyweight":    15,
    "accessory_compound": 12,
    "isolation":           8,
    "core":                6,
    "skill":               5,
}

# ── Skill / practice work ─────────────────────────────────────────────────
# Added after the core slot if time allows. Prescribed as duration-type sets.
# Examples: "Headstand Practice", "Handstand Hold", "L-Sit", "Ring Support Hold"
SKILL_WORK: list[str] = []

# ── Rest seconds per exercise category ────────────────────────────────────
REST_SECONDS: dict[str, dict[str, int]] = {
    "weekday": {"main_barbell": 150, "accessory_compound":  90, "isolation": 60, "bodyweight_core": 45},
    "weekend": {"main_barbell": 210, "accessory_compound": 150, "isolation": 75, "bodyweight_core": 60},
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
