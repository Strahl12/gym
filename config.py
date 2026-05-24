"""
Central config.

Two layers:
  - Infrastructure constants (cross-user): training rules, session templates,
    progression knobs, equipment increments. Defined directly in this file.
  - Per-user profile: goals, main lifts, focus lifts, target weight, Hevy
    folder ID, API keys. Defined in users/<name>/profile.py and overlaid by
    config.activate(<name>).

Usage:
    import config
    config.activate("john")     # required before any DB / API access
    # ... config.DB_PATH, config.MAIN_LIFTS, config.HEVY_API_KEY now populated

Shared secrets (ANTHROPIC_API_KEY) live in the root secrets.env or shell env.
Per-user secrets (HEVY_API_KEY, WITHINGS_*) live in users/<name>/secrets.env.
"""
import os
from pathlib import Path

_ROOT = Path(__file__).parent
_USERS_ROOT = _ROOT / "users"


# ── .env loader ────────────────────────────────────────────────────────────

def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env-style file into a dict. No os.environ mutation."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
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
        out[key] = val
    return out


# Root secrets.env: shared keys only (ANTHROPIC_API_KEY). Loaded into os.environ
# so it stays available across activate() calls. Per-user secrets are read fresh
# per activate() so run_all.py can switch users in a single process.
for _k, _v in _read_dotenv(_ROOT / "secrets.env").items():
    os.environ.setdefault(_k, _v)


# ── Per-user paths (set by activate) ───────────────────────────────────────
USER_NAME: str | None      = None
USER_DIR: str | None       = None
DB_PATH: str | None        = None
APP_STATE_PATH: str | None = None
ACTIVITIES_PATH: str | None = None
WITHINGS_TOKEN_PATH: str | None = None
ROUTINE_ID_PATH: str | None = None
LOG_DIR: str | None        = None


# ── API Keys ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
HEVY_API_KEY           = ""    # populated per-user by activate()
WITHINGS_ACCESS_TOKEN  = ""
WITHINGS_REFRESH_TOKEN = ""
WITHINGS_CLIENT_ID     = ""
WITHINGS_CLIENT_SECRET = ""


# ── Per-user profile defaults (overridden by users/<name>/profile.py) ─────
# These values are sensible-but-generic defaults. Real users override via profile.py.

# TRAINING_MODE: "strength" | "hypertrophy" | "mixed"
TRAINING_MODE = "hypertrophy"

GOAL = """
Training split: Push / Pull / Legs / Arms (PPL+Arms).
Target frequency: 5 sessions per week.
All weights in kg.
"""

# GOAL_MODE: "cut" | "bulk" | "maintain"
GOAL_MODE = "maintain"
TARGET_WEIGHT_KG: float | None       = None
WEIGHT_RATE_KG_PER_WEEK: float | None = None

# Session duration (weekday vs weekend, minutes)
TARGET_DURATION_MINUTES = {
    "weekday": 60,
    "weekend": 90,
}

# Main lifts + Hevy template IDs — per-user (template IDs vary by Hevy library)
MAIN_LIFTS: dict[str, dict] = {}

# Hevy routine folder — per-user (folder ID is account-specific)
HEVY_ROUTINE_FOLDER_ID: int | None = None

# Per-session-type default focus lift
DEFAULT_FOCUS_LIFTS: dict[str, str] = {}

# Exercises that should never be prescribed for this user
EXCLUDED_EXERCISES: list[str] = []

# Skill / practice work — optional list of duration-type drills
SKILL_WORK: list[str] = []

# Trusted YouTube creators for exercise selection (per-user — interests vary)
TRUSTED_CREATORS: list[dict] = []


# ── Infrastructure constants (cross-user, edit here to tune system-wide) ──

# Fixed cycle order for session type rotation
SESSION_CYCLE = ["push", "pull", "legs", "arms"]

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

# Plateau detection: flag if e1RM hasn't improved across this many sessions
PLATEAU_SESSIONS = 4

# Equipment increments (kg)
EQUIPMENT_INCREMENTS = {
    "barbell":   2.5,
    "cable":     2.5,
    "dumbbell":  2.0,
    "machine":   5.0,
}

# Progression rules
PROGRESSION = {
    "increase_kg":           2.5,
    "max_increase_kg":       5.0,
    "plateau_reset_pct":     0.90,
    "plateau_reps":          (6, 8),
    "deload_threshold_days": 14,
    "deload_weight_pct":     0.80,
    "warmup_pcts":           [0.50, 0.75],
}

# Session slot templates
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

SESSION_TIME_ESTIMATES = {
    "warmup_general":     10,
    "main_barbell":       20,
    "main_bodyweight":    15,
    "accessory_compound": 12,
    "isolation":           8,
    "core":                6,
    "skill":               5,
}

REST_SECONDS: dict[str, dict[str, int]] = {
    "weekday": {"main_barbell": 150, "accessory_compound":  90, "isolation": 60, "bodyweight_core": 45},
    "weekend": {"main_barbell": 210, "accessory_compound": 150, "isolation": 75, "bodyweight_core": 60},
}

CREATOR_SCORE_LOOKBACK_DAYS = 365
CREATOR_SCORE_MIN = 0.3


# ── Derived (recomputed by activate from MAIN_LIFTS) ──────────────────────
SESSION_LIFTS: dict[str, list[str]] = {}


def _derive_session_lifts(main_lifts: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {st: [] for st in SESSION_CYCLE}
    for name, lift in main_lifts.items():
        out.setdefault(lift["session_type"], []).append(name)
    return out


# ── activate ──────────────────────────────────────────────────────────────

def activate(user_name: str) -> None:
    """
    Switch active user: resolve paths under users/<name>/, load their
    secrets.env, and overlay their profile.py onto this module's globals.

    Must be called before any DB / API access. Safe to call repeatedly
    (e.g. run_all.py iterating over users).
    """
    global USER_NAME, USER_DIR, DB_PATH, APP_STATE_PATH, ACTIVITIES_PATH
    global WITHINGS_TOKEN_PATH, ROUTINE_ID_PATH, LOG_DIR
    global HEVY_API_KEY, ANTHROPIC_API_KEY
    global WITHINGS_ACCESS_TOKEN, WITHINGS_REFRESH_TOKEN
    global WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET
    global SESSION_LIFTS

    user_dir = _USERS_ROOT / user_name
    if not user_dir.is_dir():
        raise FileNotFoundError(
            f"No user directory at {user_dir}. "
            f"Create one with: python run.py --add-user {user_name}"
        )

    USER_NAME           = user_name
    USER_DIR            = str(user_dir)
    DB_PATH             = str(user_dir / "gym.db")
    APP_STATE_PATH      = str(user_dir / "app_state.json")
    ACTIVITIES_PATH     = str(user_dir / "recurring_activities.json")
    WITHINGS_TOKEN_PATH = str(user_dir / "withings_token.json")
    ROUTINE_ID_PATH     = str(user_dir / "hevy_routine_id.txt")
    LOG_DIR             = str(user_dir / "logs")

    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

    # Per-user secrets: file > shell env (explicit isolation when run_all switches users)
    user_secrets = _read_dotenv(user_dir / "secrets.env")
    def _pick(key: str) -> str:
        v = user_secrets.get(key, "")
        return v if v else os.environ.get(key, "")

    HEVY_API_KEY           = _pick("HEVY_API_KEY")
    WITHINGS_ACCESS_TOKEN  = _pick("WITHINGS_ACCESS_TOKEN")
    WITHINGS_REFRESH_TOKEN = _pick("WITHINGS_REFRESH_TOKEN")
    WITHINGS_CLIENT_ID     = _pick("WITHINGS_CLIENT_ID")
    WITHINGS_CLIENT_SECRET = _pick("WITHINGS_CLIENT_SECRET")
    # Anthropic key may live in user's secrets.env too (rare); root is fallback
    ANTHROPIC_API_KEY      = _pick("ANTHROPIC_API_KEY")

    # Overlay profile.py
    profile_path = user_dir / "profile.py"
    if not profile_path.is_file():
        raise FileNotFoundError(f"No profile.py at {profile_path}")

    ns: dict = {}
    exec(compile(profile_path.read_text(), str(profile_path), "exec"), ns)
    module = __import__(__name__)
    for k, v in ns.items():
        if k.startswith("_") or k == "__builtins__":
            continue
        setattr(module, k, v)

    # Recompute derived state from overlaid MAIN_LIFTS
    SESSION_LIFTS = _derive_session_lifts(MAIN_LIFTS)
