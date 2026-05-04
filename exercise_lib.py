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


def resolve_id(name: str) -> Optional[str]:
    """Return Hevy template ID for a canonical name, hevy_title, or alias."""
    db = _load()
    name_lower = name.lower()
    for hevy_id, ex in db.items():
        if ex["canonical"].lower() == name_lower:
            return hevy_id
        if ex["hevy_title"].lower() == name_lower:
            return hevy_id
        if any(a.lower() == name_lower for a in ex.get("aliases", [])):
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
