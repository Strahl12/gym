"""
hevy.py — Hevy API read/write.

Two responsibilities:
  1. Fetch exercise template IDs (run once to populate config.MAIN_LIFTS)
  2. Write a Claude-prescribed workout as a Hevy workout session
"""
import json
import requests
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


# ── Workout write ──────────────────────────────────────────────────────────

def _resolve_template_id(exercise_name: str, templates: list[dict]) -> Optional[str]:
    """
    First check config for a pre-set ID, then search templates.
    """
    for lift_name, cfg in config.MAIN_LIFTS.items():
        if (lift_name.lower() == exercise_name.lower() or
                cfg.get("hevy_name", "").lower() == exercise_name.lower()):
            if cfg.get("hevy_template_id"):
                return cfg["hevy_template_id"]
    return find_template_id(exercise_name, templates)


def build_hevy_payload(workout: dict, templates: list[dict]) -> dict:
    """
    Convert Claude's workout JSON → Hevy API payload.

    Claude workout schema:
      {title, session_type, reasoning, exercises: [{exercise_name, is_main_lift, sets: [{reps, weight_kg, is_warmup?}]}]}

    Hevy workout payload schema:
      {workout: {title, description, exercises: [{exercise_template_id, sets: [{type, weight_kg, reps}]}]}}
    """
    hevy_exercises = []

    for ex in workout.get("exercises", []):
        name = ex["exercise_name"]
        tid  = _resolve_template_id(name, templates)

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

        hevy_exercises.append({
            "exercise_template_id": tid,
            "sets": hevy_sets,
            "notes": ex.get("notes", ""),
        })

    return {
        "workout": {
            "title":       workout.get("title", "AI Prescribed Workout"),
            "description": workout.get("reasoning", ""),
            "exercises":   hevy_exercises,
        }
    }


def post_workout(workout: dict) -> dict:
    """
    Resolve template IDs and POST the workout to Hevy.
    Returns the created workout object from Hevy's API.
    """
    templates = get_all_templates()
    payload   = build_hevy_payload(workout, templates)

    print(f"[hevy] Posting workout: {payload['workout']['title']}")
    print(f"[hevy] {len(payload['workout']['exercises'])} exercises")

    resp = requests.post(
        f"{BASE_URL}/workouts",
        headers=_headers(),
        json=payload,
    )

    if not resp.ok:
        print(f"[hevy] Error {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    result = resp.json()
    print(f"[hevy] Workout created: {result.get('workout', {}).get('id', '?')}")
    return result


if __name__ == "__main__":
    print_template_ids_for_main_lifts()
