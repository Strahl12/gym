"""
classify_exercises.py — Batch-classify exercises.json with taxonomy fields.

Adds to each entry:
  movement_pattern  — one of VALID_MOVEMENT_PATTERNS
  is_compound       — bool (multi-joint)
  secondary_muscles — list[str] (muscle groups significantly worked)

Uses Claude API in batches of 50. Skips entries already classified.
Safe to re-run; only unclassified entries are sent.

Usage:
  python classify_exercises.py           # classify all missing
  python classify_exercises.py --force   # reclassify everything
"""
import json
import sys
import time
from pathlib import Path
import anthropic

_PATH = Path(__file__).parent / "exercises.json"

VALID_MOVEMENT_PATTERNS = {
    "vertical_push",       # OHP, pike push-up
    "horizontal_push",     # bench press, push-up
    "vertical_pull",       # pull-up, lat pulldown
    "horizontal_pull",     # row variants
    "hip_hinge",           # deadlift, RDL, good morning
    "quad_dominant",       # squat, leg press, lunge
    "knee_flexion",        # leg curl variants
    "elbow_flexion",       # curl variants
    "elbow_extension",     # triceps variants
    "shoulder_abduction",  # lateral raise, face pull
    "ankle_plantarflexion",# calf raise
    "core_flexion",        # crunch, sit-up
    "core_anti_extension", # plank, ab wheel, pallof press
}

VALID_MUSCLE_GROUPS = {
    "abdominals", "shoulders", "biceps", "triceps", "forearms",
    "quadriceps", "hamstrings", "calves", "glutes", "abductors",
    "adductors", "lats", "upper_back", "traps", "lower_back",
    "chest", "cardio", "neck", "full_body", "other",
}

BATCH_SIZE = 50

SYSTEM_PROMPT = f"""You are classifying gym exercises for a workout AI system.

For each exercise, return:
- movement_pattern: one of {sorted(VALID_MOVEMENT_PATTERNS)}
- is_compound: true if the exercise crosses 2+ joints and recruits multiple muscle groups; false for isolation
- secondary_muscles: list of muscle groups significantly worked (not the primary). Use only values from: {sorted(VALID_MUSCLE_GROUPS)}. Empty list if none.

Rules:
- Choose the SINGLE best movement_pattern
- secondary_muscles should be muscles meaningfully loaded, not just stabilisers
- If ambiguous, pick the pattern that best describes the primary loading mechanic

Return a JSON object keyed by exercise name, exactly matching the input names.
Example output:
{{
  "Romanian Deadlift": {{
    "movement_pattern": "hip_hinge",
    "is_compound": true,
    "secondary_muscles": ["glutes", "lower_back", "calves"]
  }},
  "Bicep Curl (Barbell)": {{
    "movement_pattern": "elbow_flexion",
    "is_compound": false,
    "secondary_muscles": ["forearms"]
  }}
}}"""


def _classify_batch(client: anthropic.Anthropic, exercises: list[dict]) -> dict:
    """Send a batch of exercises to Claude and return classification dict."""
    payload = {ex["canonical"]: {"muscle": ex["muscle"], "equipment": ex["equipment"]} for ex in exercises}
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    text = msg.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def _validate(result: dict) -> dict:
    """Clamp classification values to valid enums."""
    out = {}
    mp = result.get("movement_pattern", "")
    out["movement_pattern"] = mp if mp in VALID_MOVEMENT_PATTERNS else ""
    out["is_compound"] = bool(result.get("is_compound", False))
    secondary = [m for m in result.get("secondary_muscles", []) if m in VALID_MUSCLE_GROUPS]
    out["secondary_muscles"] = secondary
    return out


def classify(force: bool = False) -> None:
    db = json.loads(_PATH.read_text())

    if force:
        pending = list(db.items())
    else:
        pending = [
            (hid, ex) for hid, ex in db.items()
            if "movement_pattern" not in ex
        ]

    if not pending:
        print("All exercises already classified. Use --force to reclassify.")
        return

    print(f"Classifying {len(pending)} exercises in batches of {BATCH_SIZE}...")
    client = anthropic.Anthropic()
    failed = []

    for i in range(0, len(pending), BATCH_SIZE):
        batch_items = pending[i:i + BATCH_SIZE]
        batch_exs = [ex for _, ex in batch_items]
        batch_ids = [hid for hid, _ in batch_items]
        n = len(batch_items)
        print(f"  Batch {i // BATCH_SIZE + 1}: exercises {i+1}–{i+n}")

        try:
            results = _classify_batch(client, batch_exs)
        except Exception as e:
            print(f"    ERROR: {e}")
            failed.extend(batch_ids)
            time.sleep(2)
            continue

        for hid, ex in zip(batch_ids, batch_exs):
            name = ex["canonical"]
            if name in results:
                classification = _validate(results[name])
                db[hid].update(classification)
            else:
                print(f"    MISSING in response: {name}")
                failed.append(hid)

        # Flush after each batch so progress is saved incrementally
        _PATH.write_text(json.dumps(db, indent=2))
        time.sleep(0.5)

    classified = len(pending) - len(failed)
    print(f"\nDone. {classified}/{len(pending)} classified. {len(failed)} failed.")
    if failed:
        print(f"Failed IDs: {failed}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    classify(force=force)
