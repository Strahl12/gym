"""
hevy.py — Hevy API read/write.

Two responsibilities:
  1. Fetch exercise template IDs (run once to populate config.MAIN_LIFTS)
  2. Write a Claude-prescribed workout as a Hevy workout session
"""
import re
import json
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import config

BASE_URL = "https://api.hevyapp.com/v1"


def _headers() -> dict:
    return {
        "api-key": config.HEVY_API_KEY,
        "Content-Type": "application/json",
    }


# ── Exercise template lookup ───────────────────────────────────────────────

def search_exercise_templates(query: str, page_size: int = 20) -> list[dict]:
    """Search Hevy's exercise library. Returns list of {id, title, ...}."""
    resp = requests.get(
        f"{BASE_URL}/exercise_templates",
        headers=_headers(),
        params={"page": 1, "pageSize": page_size},
    )
    resp.raise_for_status()
    templates = resp.json().get("exercise_templates", [])
    query_lower = query.lower()
    return [t for t in templates if query_lower in t["title"].lower()]


def get_all_templates(pages: int = 10) -> list[dict]:
    """Fetch all exercise templates (paginated)."""
    all_templates = []
    for page in range(1, pages + 1):
        resp = requests.get(
            f"{BASE_URL}/exercise_templates",
            headers=_headers(),
            params={"page": page, "pageSize": 100},
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        batch = resp.json().get("exercise_templates", [])
        if not batch:
            break
        all_templates.extend(batch)
    return all_templates


def find_template_id(exercise_name: str, templates: list[dict]) -> Optional[str]:
    """Best-match template ID for a given exercise name."""
    name_lower = exercise_name.lower()
    # Exact match first
    for t in templates:
        if t["title"].lower() == name_lower:
            return t["id"]
    # Partial match
    for t in templates:
        if name_lower in t["title"].lower():
            return t["id"]
    return None


def print_template_ids_for_main_lifts():
    """
    Helper: run once to find Hevy template IDs for your main lifts.
    Paste the output into config.MAIN_LIFTS[...]["hevy_template_id"].
    """
    templates = get_all_templates()
    print(f"Fetched {len(templates)} exercise templates\n")
    for lift_name, cfg in config.MAIN_LIFTS.items():
        hevy_name = cfg.get("hevy_name", lift_name)
        tid = find_template_id(hevy_name, templates)
        print(f"{lift_name}")
        print(f"  Searching for: '{hevy_name}'")
        print(f"  Template ID: {tid or 'NOT FOUND'}")
        print()


# ── Template resolution ────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Lowercase, strip punctuation, collapse spaces — for fuzzy matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", name.lower())).strip()


def _resolve_template_id(exercise_name: str) -> Optional[str]:
    """
    Look up a Hevy template ID for the given exercise name.

    Order of precedence:
      1. Hardcoded ID in config.MAIN_LIFTS (fastest, guaranteed correct)
      2. Exact title match in hevy_exercise_library DB table
      3. Normalised fuzzy match (strips punctuation, case-insensitive)
    """
    # 1. Hardcoded config IDs
    for lift_name, cfg in config.MAIN_LIFTS.items():
        if (lift_name.lower() == exercise_name.lower() or
                cfg.get("hevy_name", "").lower() == exercise_name.lower()):
            if cfg.get("hevy_template_id"):
                return cfg["hevy_template_id"]

    # 2 & 3. DB lookup
    try:
        con = sqlite3.connect(config.DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT hevy_id, title FROM hevy_exercise_library").fetchall()
        con.close()
    except Exception:
        rows = []

    # Equipment qualifiers to deprioritise when not mentioned in the query
    EQUIPMENT_WORDS = {"band", "machine", "smith", "cable", "dumbbell", "assisted", "suspension"}

    query_norm = _norm(exercise_name)
    q_words    = set(query_norm.split())
    exact_match       = None
    fuzzy_preferred   = None   # fuzzy match without unwanted equipment qualifier
    fuzzy_fallback    = None   # fuzzy match with equipment qualifier

    for row in rows:
        title_norm = _norm(row["title"])
        t_words    = set(title_norm.split())

        if row["title"].lower() == exercise_name.lower():
            return row["hevy_id"]                        # exact string match

        if title_norm == query_norm and not exact_match:
            exact_match = row["hevy_id"]                 # normalised exact

        # Fuzzy: query words are a subset of title words (or vice versa)
        if q_words <= t_words or t_words <= q_words:
            extra_words = t_words - q_words
            has_unwanted_equipment = bool(extra_words & EQUIPMENT_WORDS - q_words)
            if not has_unwanted_equipment and not fuzzy_preferred:
                fuzzy_preferred = row["hevy_id"]
            elif has_unwanted_equipment and not fuzzy_fallback:
                fuzzy_fallback = row["hevy_id"]

    return exact_match or fuzzy_preferred or fuzzy_fallback


def build_hevy_payload(workout: dict) -> dict:
    """
    Convert Claude's workout JSON → Hevy API payload.

    Claude workout schema:
      {title, session_type, reasoning, exercises: [{exercise_name, is_main_lift, sets: [{reps, weight_kg, is_warmup?}]}]}

    Hevy workout payload schema:
      {workout: {title, description, start_time, exercises: [{exercise_template_id, sets: [{type, weight_kg, reps}]}]}}
    """
    hevy_exercises = []

    for ex in workout.get("exercises", []):
        name = ex["exercise_name"]
        tid  = _resolve_template_id(name)

        if not tid:
            print(f"[hevy] WARNING: no template ID found for '{name}' — skipping")
            continue

        hevy_sets = []
        for s in ex.get("sets", []):
            hevy_sets.append({
                "type":       "warmup" if s.get("is_warmup") else "normal",
                "weight_kg":  float(s.get("weight_kg", 0)),
                "reps":       int(s.get("reps", 0)),
            })

        entry: dict = {
            "exercise_template_id": tid,
            "sets": hevy_sets,
        }
        if ex.get("notes"):
            entry["notes"] = ex["notes"]
        hevy_exercises.append(entry)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "workout": {
            "title":       workout.get("title", "AI Prescribed Workout"),
            "description": workout.get("reasoning", ""),
            "start_time":  now_iso,
            "end_time":    now_iso,
            "is_private":  True,
            "exercises":   hevy_exercises,
        }
    }


def _post(endpoint: str, payload: dict, label: str) -> dict:
    """POST to a Hevy endpoint; normalise the list-wrapped response."""
    resp = requests.post(f"{BASE_URL}/{endpoint}", headers=_headers(), json=payload)
    if not resp.ok:
        print(f"[hevy] Error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
    result  = resp.json()
    # Hevy wraps the created object in a single-element list, e.g. {"workout": [{...}]}
    key     = list(result.keys())[0] if result else label
    wrapped = result.get(key, [])
    obj     = wrapped[0] if isinstance(wrapped, list) and wrapped else wrapped
    print(f"[hevy] {label.title()} created: {obj.get('id', '?')}")
    return {label: obj}


def build_routine_payload(workout: dict) -> dict:
    """Convert Claude's workout JSON → Hevy routine payload (no timestamps)."""
    exercises = []
    for ex in workout.get("exercises", []):
        name = ex["exercise_name"]
        tid  = _resolve_template_id(name)
        if not tid:
            print(f"[hevy] WARNING: no template ID found for '{name}' — skipping")
            continue
        sets = [
            {"type": "warmup" if s.get("is_warmup") else "normal",
             "weight_kg": float(s.get("weight_kg", 0)),
             "reps": int(s.get("reps", 0))}
            for s in ex.get("sets", [])
        ]
        entry: dict = {"exercise_template_id": tid, "sets": sets}
        if ex.get("notes"):
            entry["notes"] = ex["notes"]
        exercises.append(entry)

    date_prefix = datetime.now().strftime("[%d-%m-%Y]")
    title = f"{date_prefix} {workout.get('title', 'AI Prescribed Workout')}"

    return {
        "routine": {
            "title":     title,
            "notes":     workout.get("reasoning") or "AI prescribed",
            "folder_id": config.HEVY_ROUTINE_FOLDER_ID,
            "exercises": exercises,
        }
    }


ROUTINE_ID_FILE = Path.home() / "gym_ai" / "hevy_routine_id.txt"


def _load_pinned_routine_id() -> Optional[str]:
    if ROUTINE_ID_FILE.exists():
        return ROUTINE_ID_FILE.read_text().strip() or None
    return None


def _save_pinned_routine_id(routine_id: str) -> None:
    ROUTINE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROUTINE_ID_FILE.write_text(routine_id)


def _update_routine(routine_id: str, payload: dict) -> dict:
    """PUT an updated routine onto an existing routine ID."""
    resp = requests.put(
        f"{BASE_URL}/routines/{routine_id}",
        headers=_headers(),
        json=payload,
    )
    if not resp.ok:
        print(f"[hevy] PUT routine failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
    result  = resp.json()
    key     = list(result.keys())[0] if result else "routine"
    wrapped = result.get(key, [])
    obj     = wrapped[0] if isinstance(wrapped, list) and wrapped else wrapped
    return {"routine": obj}


def post_routine(workout: dict) -> dict:
    """
    Always write to the same pinned routine slot (stored in ~/gym_ai/hevy_routine_id.txt).
    PUT to update if the slot exists, POST to create it on first run.
    This keeps exactly one routine in the Dynamic folder — no deletion needed.
    """
    payload = build_routine_payload(workout)
    put_payload = {"routine": {k: v for k, v in payload["routine"].items() if k != "folder_id"}}

    pinned_id = _load_pinned_routine_id()
    if pinned_id:
        try:
            result = _update_routine(pinned_id, put_payload)
            print(f"[hevy] Updated pinned routine: {payload['routine']['title']}")
            print(f"[hevy] {len(payload['routine']['exercises'])} exercises")
            return result
        except Exception as e:
            print(f"[hevy] PUT failed ({e}), creating new routine")

    print(f"[hevy] Creating routine: {payload['routine']['title']}")
    print(f"[hevy] {len(payload['routine']['exercises'])} exercises")
    result = _post("routines", payload, "routine")
    new_id = result.get("routine", {}).get("id")
    if new_id:
        _save_pinned_routine_id(new_id)
        print(f"[hevy] Pinned routine ID saved: {new_id}")
    return result


def post_workout(workout: dict) -> dict:
    """POST the prescription as a completed Hevy workout (logs immediately)."""
    payload = build_hevy_payload(workout)
    print(f"[hevy] Posting workout: {payload['workout']['title']}")
    print(f"[hevy] {len(payload['workout']['exercises'])} exercises")
    return _post("workouts", payload, "workout")


if __name__ == "__main__":
    print_template_ids_for_main_lifts()
