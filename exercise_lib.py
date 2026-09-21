"""
exercise_lib.py — Local mirror of the Hevy exercise template library.

Source of truth: exercises.json (keyed by Hevy template ID).
Each entry has:
  hevy_title  — exact title Hevy uses in its API
  canonical   — name used throughout this codebase and DB
  aliases     — extra names that should resolve to this exercise
  muscle/equipment/session_type — metadata

Edit exercises.json to add canonical overrides or aliases.
Run sync_exercises.py to refresh from the Hevy API.
"""
import re
import json
from pathlib import Path
from typing import Optional

_PATH = Path(__file__).parent / "exercises.json"
_db: dict = {}

# Valid Hevy API enum values — used to constrain Claude's output schema
VALID_MUSCLE_GROUPS = {
    "abdominals", "shoulders", "biceps", "triceps", "forearms",
    "quadriceps", "hamstrings", "calves", "glutes", "abductors",
    "adductors", "lats", "upper_back", "traps", "lower_back",
    "chest", "cardio", "neck", "full_body", "other",
}
VALID_EQUIPMENT_CATEGORIES = {
    "barbell", "dumbbell", "kettlebell", "machine", "plate",
    "resistance_band", "suspension", "none", "other",
}
VALID_EXERCISE_TYPES = {
    "weight_reps", "reps_only", "bodyweight_weighted", "bodyweight_assisted",
    "duration", "distance_duration",
}
VALID_MOVEMENT_PATTERNS = {
    "vertical_push",        # OHP, pike push-up
    "horizontal_push",      # bench press, push-up
    "vertical_pull",        # pull-up, lat pulldown
    "horizontal_pull",      # row variants
    "hip_hinge",            # deadlift, RDL, good morning
    "quad_dominant",        # squat, leg press, lunge
    "knee_flexion",         # leg curl variants
    "elbow_flexion",        # curl variants
    "elbow_extension",      # triceps variants
    "shoulder_abduction",   # lateral raise, face pull
    "ankle_plantarflexion", # calf raise
    "core_flexion",         # crunch, sit-up
    "core_anti_extension",  # plank, ab wheel, pallof press
}


def _load() -> dict:
    global _db
    if not _db:
        _db = json.loads(_PATH.read_text())
    return _db


def _flush(db: dict) -> None:
    global _db
    _PATH.write_text(json.dumps(db, indent=2))
    _db = db


def _norm(name: str) -> str:
    """Strip parenthetical qualifiers, lowercase, collapse whitespace."""
    name = re.sub(r'\s*\([^)]*\)', '', name)
    return re.sub(r'\s+', ' ', name.lower()).strip()


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _slug(name: str) -> str:
    """Collapse a name to lowercase alphanumerics: 'Push-up' / 'push up' → 'push_up'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def resolve_id(name: str) -> Optional[str]:
    """Return Hevy template ID for a canonical name, hevy_title, or alias.
    Falls back to slug-normalised comparison so punctuation/spacing variants
    ('push up', 'Push-up') resolve to the same entry."""
    db = _load()
    name_lower = name.lower()
    for hevy_id, ex in db.items():
        if ex["canonical"].lower() == name_lower:
            return hevy_id
        if ex["hevy_title"].lower() == name_lower:
            return hevy_id
        if any(a.lower() == name_lower for a in ex.get("aliases", [])):
            return hevy_id
    name_slug = _slug(name)
    if name_slug:
        for hevy_id, ex in db.items():
            if _slug(ex["canonical"]) == name_slug or _slug(ex["hevy_title"]) == name_slug:
                return hevy_id
            if any(_slug(a) == name_slug for a in ex.get("aliases", [])):
                return hevy_id
    return None


def find_close_match(name: str, muscle_group: str, equipment_category: str) -> Optional[str]:
    """
    Look for an existing exercise that is likely the same as `name` to avoid duplicates.
    Filters by muscle_group + equipment_category first, then compares normalised names.
    Returns hevy_id of the best match, or None.
    """
    db = _load()
    name_norm = _norm(name)
    best: tuple[float, str] | None = None

    for hevy_id, ex in db.items():
        if ex.get("muscle") != muscle_group:
            continue
        if ex.get("equipment") != equipment_category:
            continue
        ex_norm = _norm(ex["canonical"])
        if name_norm == ex_norm:
            return hevy_id                        # exact normalised match
        score = _jaccard(name_norm, ex_norm)
        if score >= 0.6 and (best is None or score > best[0]):
            best = (score, hevy_id)

    return best[1] if best else None


def add_alias(hevy_id: str, alias: str) -> None:
    """Add an alias to an existing exercise entry."""
    db = _load()
    if hevy_id not in db:
        return
    aliases = db[hevy_id].setdefault("aliases", [])
    if alias not in aliases:
        aliases.append(alias)
        _flush(db)


def save_exercise(hevy_id: str, title: str, canonical_name: str,
                  muscle: str, equipment: str, exercise_type: str) -> None:
    """Add a newly created custom exercise to exercises.json."""
    db = _load()
    db[hevy_id] = {
        "hevy_title":    title,
        "canonical":     canonical_name,
        "aliases":       [],
        "muscle":        muscle,
        "equipment":     equipment,
        "exercise_type": exercise_type,
        "session_type":  "",
    }
    _flush(db)


def save_custom_exercise(hevy_id: str, title: str, muscle: str, equipment: str,
                         exercise_type: str, session_type: str = "",
                         movement_pattern: str = "", is_compound: bool = False,
                         secondary_muscles: Optional[list] = None) -> None:
    """Add/replace a custom (athlete-added) exercise with full taxonomy fields.
    Used when the logger classifies a name that wasn't in the library."""
    db = _load()
    db[hevy_id] = {
        "hevy_title":        title,
        "canonical":         title,
        "aliases":           [],
        "muscle":            muscle,
        "equipment":         equipment,
        "exercise_type":     exercise_type,
        "session_type":      session_type or "",
        "movement_pattern":  movement_pattern or "",
        "is_compound":       bool(is_compound),
        "secondary_muscles": secondary_muscles or [],
        "custom":            True,
    }
    _flush(db)


def canonical(hevy_title: str) -> str:
    """Return canonical name for a Hevy exercise title. Falls back to hevy_title."""
    db = _load()
    title_lower = hevy_title.lower()
    for ex in db.values():
        if ex["hevy_title"].lower() == title_lower:
            return ex["canonical"]
        if any(a.lower() == title_lower for a in ex.get("aliases", [])):
            return ex["canonical"]
    return hevy_title


def all_exercises() -> dict:
    return _load()
