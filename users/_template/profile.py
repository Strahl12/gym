"""
users/<name>/profile.py — per-user profile template.

Copy this file to users/<your-name>/profile.py (or use the wizard:
    python run.py --add-user <your-name>
)
and edit the values below. This file is exec'd into config's namespace
when config.activate(<name>) runs, overriding the defaults in config.py.
"""

# ── Training mode ─────────────────────────────────────────────────────────
# "strength" | "hypertrophy" | "mixed"
TRAINING_MODE = "hypertrophy"

# ── Training goals ────────────────────────────────────────────────────────
GOAL = """
Training split: Push / Pull / Legs / Arms (PPL+Arms).
Target frequency: 5 sessions per week.
All weights in kg.
"""

# ── Body composition ──────────────────────────────────────────────────────
# "cut" | "bulk" | "maintain"
GOAL_MODE = "maintain"
TARGET_WEIGHT_KG = None          # e.g. 95.0
WEIGHT_RATE_KG_PER_WEEK = None   # e.g. 0.25 for slow bulk, -0.25 for slow cut

# ── Session duration (weekday vs weekend, minutes) ────────────────────────
TARGET_DURATION_MINUTES = {
    "weekday": 60,
    "weekend": 90,
}

# ── Main lifts + Hevy exercise template IDs ───────────────────────────────
# After filling in the rest, run:
#     python run.py --user <your-name> --find-templates
# and paste the printed hevy_template_id for each lift below.
MAIN_LIFTS = {
    "Incline Barbell Bench Press": {
        "hevy_template_id": "",
        "hevy_name": "Incline Bench Press (Barbell)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
    "Strict Military Press": {
        "hevy_template_id": "",
        "hevy_name": "Overhead Press (Barbell)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
    "Pull Up": {
        "hevy_template_id": "",
        "hevy_name": "Pull Up",
        "session_type": "pull",
        "target_sets": 4,
        "rep_range": (4, 8),
        "progression_kg": 2.5,
        "is_bodyweight": True,
    },
    "Weighted Dip": {
        "hevy_template_id": "",
        "hevy_name": "Chest Dip (Weighted)",
        "session_type": "push",
        "target_sets": 4,
        "rep_range": (6, 10),
        "progression_kg": 2.5,
        "is_bodyweight": True,
    },
    "Front Squat": {
        "hevy_template_id": "",
        "hevy_name": "Front Squat",
        "session_type": "legs",
        "target_sets": 4,
        "rep_range": (4, 6),
        "progression_kg": 2.5,
    },
}

# ── Hevy routine folder ────────────────────────────────────────────────────
# Create a folder in Hevy app named e.g. "Dynamic", then find its ID via the
# Hevy API. The wizard can do this for you.
HEVY_ROUTINE_FOLDER_ID = None

# ── Focus lifts ───────────────────────────────────────────────────────────
DEFAULT_FOCUS_LIFTS = {
    "push": "Incline Barbell Bench Press",
    "pull": "Pull Up",
    "legs": "Front Squat",
    "arms": "Close Grip Bench Press",
}

# ── Exclusions ────────────────────────────────────────────────────────────
# Add with: python run.py --user <name> --exclude "Exercise Name"
EXCLUDED_EXERCISES: list[str] = []

# ── Skill / practice work (optional duration-type drills) ─────────────────
SKILL_WORK: list[str] = []

# ── Trusted creators (optional — for --creator-recs mode) ─────────────────
TRUSTED_CREATORS: list[dict] = []
