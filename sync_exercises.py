"""
sync_exercises.py — Refresh exercises.json from the Hevy API.

Preserves any canonical/aliases overrides you've set manually.
Safe to re-run at any time.

Usage:
  python sync_exercises.py
"""
import json
import requests
from pathlib import Path
import config

EXERCISES_FILE = Path(__file__).parent / "exercises.json"
BASE_URL = "https://api.hevyapp.com/v1"


def fetch_all_templates() -> list[dict]:
    headers = {"api-key": config.HEVY_API_KEY}
    templates = []
    for page in range(1, 30):
        resp = requests.get(
            f"{BASE_URL}/exercise_templates",
            headers=headers,
            params={"page": page, "pageSize": 100},
        )
        if not resp.ok:
            break
        batch = resp.json().get("exercise_templates", [])
        if not batch:
            break
        templates.extend(batch)
    return templates


def sync():
    existing = {}
    if EXERCISES_FILE.exists():
        existing = json.loads(EXERCISES_FILE.read_text())

    templates = fetch_all_templates()
    print(f"[sync_exercises] Fetched {len(templates)} templates from Hevy API")

    added = updated = 0
    for t in templates:
        hevy_id = t["id"]
        if hevy_id in existing:
            existing[hevy_id]["hevy_title"] = t["title"]
            existing[hevy_id]["muscle"]     = t.get("primary_muscle_group", existing[hevy_id].get("muscle", ""))
            existing[hevy_id]["equipment"]  = t.get("equipment_category",   existing[hevy_id].get("equipment", ""))
            updated += 1
        else:
            existing[hevy_id] = {
                "hevy_title":   t["title"],
                "canonical":    t["title"],
                "aliases":      [],
                "muscle":       t.get("primary_muscle_group", ""),
                "equipment":    t.get("equipment_category", ""),
                "session_type": "",
            }
            added += 1

    EXERCISES_FILE.write_text(json.dumps(existing, indent=2))
    print(f"[sync_exercises] {added} added, {updated} updated — {len(existing)} total")


if __name__ == "__main__":
    sync()
