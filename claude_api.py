"""
claude_api.py — Sends training context to Claude and returns a structured workout.

Claude is given:
  - A system prompt encoding goals, progression rules, and output schema
  - A user message with today's derived context (lift history, balance, bodyweight)

Returns a validated dict matching the Hevy write schema.
"""
import json
import requests
from typing import Optional
import config

CLAUDE_MODEL   = "claude-sonnet-4-20250514"
ANTHROPIC_URL  = "https://api.anthropic.com/v1/messages"
MAX_TOKENS     = 2000

SYSTEM_PROMPT = """
You are a strength programming assistant. Your job is to prescribe today's gym session
based on the athlete's training history, goals, and current context.

## Training mode: {mode}
{mode_detail}

## Goals
{goal}

## Progression rules — follow these strictly
1. If a lift shows NO plateau: prescribe the same weight as last session, or +2.5kg if
   the athlete hit the top of their rep range (all sets clean).
2. If a lift shows a PLATEAU (e1RM flat for 4+ sessions): prescribe a RESET — drop
   working weight by 10%, rebuild with higher reps (6-8), note the reset in reasoning.
3. For bodyweight exercises (Pull Up, Weighted Dip):
   - If currently using added weight: treat like any barbell lift — apply rules 1 and 2
     to the ADDED weight only (e.g. plateau at +10kg → reset to +9kg, NOT to bodyweight).
   - Only prescribe pure bodyweight if the athlete has never used added weight, or
     explicitly returned from injury.
4. Never prescribe more than 5kg increase on any lift in one session.
5. If a lift hasn't been trained in >14 days, treat as returning from deload:
   prescribe 80% of last working weight, higher reps.
6. Always start the session with 2 compound movements, including on arm days.
   Follow with 2-3 isolation accessories. Compounds go first in the exercises list.

## Session templates — follow these slots exactly, in order

Each session has fixed muscle-group SLOTS. Use the priority list to pick the best available
exercise for each slot. Never skip a slot or add exercises outside the template.

### PUSH
1. Chest compound (main lift): always Barbell Bench Press
2. Shoulder compound (main lift): always Strict Military Press
3. Tricep compound (main lift): always Weighted Dip
4. Lateral raise: pick highest-priority lateral raise variant from the priority list
5. Tricep isolation: pick highest-priority tricep isolation from the priority list

### PULL
1. Hip-hinge / lower back: pick highest-priority from [Deadlift, Romanian Deadlift, Good Morning (Barbell)]
2. Horizontal row: pick highest-priority from [Barbell Row, Pendlay Row (Barbell), Landmine Row, Cable Row]
3. Vertical pull (main lift): always Pull Up
4. Lat / vertical pull accessory: highest-priority lat pulldown or straight-arm pulldown variant
5. Rear delt / upper back: highest-priority face pull, reverse fly, or rear-delt accessory

### LEGS
1. Quad compound (main lift): always Front Squat
2. Posterior chain: highest-priority from [Romanian Deadlift, Leg Curl, Good Morning (Barbell)]
3. Quad accessory: highest-priority from [Leg Press, Leg Extension]
4. Calf: highest-priority calf exercise from the priority list

### ARMS
1. Tricep compound: highest-priority from [Close Grip Bench Press, Weighted Dip]
   — NEVER use Barbell Bench Press or Strict Military Press on arms day
2. Bicep compound: highest-priority from [Barbell Curl, Chin Up]
3. Tricep isolation: highest-priority from [Triceps Pushdown, Cable Triceps Extension, Dumbbell Skullcrusher]
4. Bicep isolation: highest-priority from [Hammer Curl, Preacher Curl, Incline Curl (Dumbbell)]
5. Optional 5th if time remains: next-highest-priority isolation from the list

### CORE (final slot on every session)
Always finish every session with 1 core exercise. Pick the highest-priority from the core
priority list. 2–3 sets. Notes field: "Rest 60s between sets."

## Main lifts in context
The context includes main lift history for push lifts (Bench, OHP, Dip) and legs (Front Squat).
On PULL and ARMS days, ignore that data — it is shown for reference only.
Do NOT include push or legs main lifts in pull or arms sessions.

## Muscle clash rule
The 3 mandatory template slots are always included regardless of what was trained yesterday.
For accessory slots (4–5 only): skip exercises targeting muscles trained in the previous session.
- After arms: skip bicep and tricep accessories (replace with a different slot-appropriate exercise)
- After push: skip tricep isolation accessories
- After pull: skip rear-delt accessories
- After legs: skip posterior-chain accessories in slots 4–5

## Exercise selection — use the priority list
You will receive a pre-ranked exercise priority list for today's session.
priority = days_since_last / target_freq_days. Values > 1.0 are overdue.
- Always include all main lifts (is_main=True) regardless of priority, unless progression rules require skipping (e.g. insufficient recovery).
- Pick accessories from the TOP of the priority list — prefer exercises most overdue.
- Select 2–4 accessories total; skip any that duplicate a main lift's muscle pattern.
- If you skip a high-priority exercise for a valid reason, note it in reasoning.
- If no priority list is provided, fall back to the session type defaults above.
- CRITICAL: use exercise names EXACTLY as they appear in the priority list. Copy the name character-for-character. Do NOT paraphrase, abbreviate, rename, or invent any exercise name. If you use a name not in the priority list, the exercise will be silently dropped from the session.

## Session duration and rest times
You will be given a target session duration. Use these estimates to fill it:
- General warm-up: 10 min (not counted as an exercise)
- Main barbell lift (with 2 warm-up sets + 4 working sets): ~20 min
- Bodyweight main lift (4 working sets): ~15 min
- Accessory compound (3–4 sets): ~12 min
- Isolation exercise (3–4 sets): ~8 min
Add accessories until you reach the target duration. Do not exceed it by more than 10 min.

For every exercise, include a "notes" field with the recommended rest time:
- Main barbell compound: "Rest 3–4 min between working sets"
- Accessory compound: "Rest 2–3 min between sets"
- Isolation: "Rest 60–90s between sets"
- Bodyweight / core: "Rest 60s between sets"

## Output format — return ONLY valid JSON, no markdown, no explanation outside the JSON
{
  "session_type": "push|pull|legs|arms",
  "title": "short descriptive title",
  "reasoning": "2-3 sentences explaining today's prescription and any adjustments",
  "exercises": [
    {
      "exercise_name": "exact name matching Hevy exercise library",
      "is_main_lift": true,
      "sets": [
        {"reps": 5, "weight_kg": 90.0},
        ...
      ],
      "notes": "optional cue or instruction"
    }
  ]
}

All weights in kg. Include warm-up sets only for main barbell lifts (2 warm-up sets
at 50% and 75% of working weight). Label them with "is_warmup": true.
""".strip()


def _headers() -> dict:
    return {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _build_user_message(context: dict) -> str:
    """Formats the context dict into a clear natural-language + JSON prompt."""
    today          = context["today"]
    stype          = context["suggested_session_type"]
    bw             = context.get("bodyweight_kg")
    bw_trend       = context.get("bodyweight_trend_kg_per_week")
    sessions_7d    = context.get("sessions_last_7_days", 0)
    balance        = context.get("session_balance_last_28_days", {})
    last_type      = context.get("last_session_type")

    lines = [
        f"Date: {today}",
        f"Target session duration: {config.TARGET_DURATION_MINUTES} minutes",
        f"Suggested session type: {stype}",
        f"Last session type: {last_type or 'unknown'}",
        f"Sessions in last 7 days: {sessions_7d}",
        f"Session balance (last 28 days): {balance}",
    ]

    if bw:
        lines.append(f"Bodyweight: {bw}kg")
    if bw_trend is not None:
        direction = "gaining" if bw_trend > 0 else "losing"
        lines.append(f"Weight trend: {direction} {abs(bw_trend):.2f}kg/week")

    lines.append("\n## Main lift status")

    for lift, data in context["main_lifts"].items():
        if data["session_type"] != stype:
            continue   # only show lifts relevant to today's session

        days_ago = data["days_since_last_session"]
        plateau  = data["plateau_detected"]
        history  = data["recent_sessions"]

        lines.append(f"\n### {lift}")
        lines.append(f"  Days since last session: {days_ago}")
        lines.append(f"  Plateau detected: {plateau}")
        lines.append(f"  Is bodyweight exercise: {data['is_bodyweight']}")
        lines.append(f"  Target sets: {data['target_sets']}, rep range: {data['rep_range']}")

        if history:
            lines.append("  Recent sessions (newest first):")
            for h in history[:5]:
                e1rm_str = f"e1RM={h['best_e1rm']}kg" if h.get('best_e1rm') else "bodyweight"
                lines.append(f"    {h['date']}: {h['top_weight']}kg × {h['max_reps']} reps — {e1rm_str}")
        else:
            lines.append("  No recent history.")

    priorities = context.get("exercise_priorities", [])
    if priorities:
        from hevy import _resolve_template_id
        from context import exercise_priorities as _core_priorities
        # Only show exercises that can actually be posted to Hevy
        postable = [p for p in priorities if p["is_main_lift"] or _resolve_template_id(p["exercise_name"])]
        lines.append("\n## Exercise priority list (pick accessories from the top)")
        lines.append("  (* = main lift)  format: name | days_since | target_freq | priority")
        lines.append("  These are the ONLY valid exercise names. Use them verbatim.")
        for p in postable[:20]:
            marker = "*" if p["is_main_lift"] else " "
            days   = p["days_since_last"] if p["days_since_last"] is not None else "never"
            lines.append(
                f"  {marker} {p['exercise_name']}: "
                f"days={days}, freq={p['target_freq_days']}d, priority={p['priority']}"
            )
        # Core priority list (separate — always appended)
        core = [p for p in _core_priorities("core") if _resolve_template_id(p["exercise_name"])]
        if core:
            lines.append("\n## Core priority list (pick 1 for the final slot)")
            lines.append("  These are the ONLY valid core names. Use them verbatim.")
            for p in core[:8]:
                days = p["days_since_last"] if p["days_since_last"] is not None else "never"
                lines.append(
                    f"  {p['exercise_name']}: days={days}, freq={p['target_freq_days']}d, priority={p['priority']}"
                )

    feedback = context.get("recent_workout_feedback", [])
    if feedback:
        lines.append("\n## Recent workout feedback (what athlete actually did vs prescription)")
        lines.append("  Use this to calibrate weights, rep ranges, and exercise selection.")
        for fb in feedback:
            d    = fb["date"]
            st   = fb["session_type"]
            diff = fb["diff"]
            parts = []
            for name in diff.get("skipped", []):
                parts.append(f"skipped '{name}'")
            for name in diff.get("added", []):
                parts.append(f"added '{name}'")
            for w in diff.get("weight_adjustments", []):
                sign = "+" if w["delta_pct"] > 0 else ""
                parts.append(f"'{w['exercise']}' weight {w['prescribed_kg']}→{w['actual_kg']}kg ({sign}{w['delta_pct']}%)")
            for r in diff.get("reps_adjustments", []):
                parts.append(f"'{r['exercise']}' reps {r['prescribed_reps']}→{r['actual_reps']}")
            if parts:
                lines.append(f"  {d} ({st}): " + "; ".join(parts))

    lines.append("\nPrescribe today's full session as JSON.")
    return "\n".join(lines)


def get_workout(context: dict) -> Optional[dict]:
    """
    Calls Claude with the training context. Returns parsed workout dict or None on failure.
    """
    MODE_DETAILS = {
        "strength":     "Rep range 4–6, load 80–90% 1RM, rest 2–4 min between sets. Prioritise adding weight over adding reps.",
        "hypertrophy":  "Rep range 8–12, load 65–80% 1RM, rest 60–90s. Prioritise volume and time under tension.",
        "powerlifting": "Rep range 1–5 on main lifts, load 85–95% 1RM, rest 4–6 min on compounds. Accessories at 6–8 reps.",
    }
    system = (SYSTEM_PROMPT
              .replace("{goal}", config.GOAL)
              .replace("{mode}", config.TRAINING_MODE)
              .replace("{mode_detail}", MODE_DETAILS.get(config.TRAINING_MODE, "")))
    user   = _build_user_message(context)

    payload = {
        "model":      CLAUDE_MODEL,
        "max_tokens": MAX_TOKENS,
        "system":     system,
        "messages":   [{"role": "user", "content": user}],
    }

    resp = requests.post(ANTHROPIC_URL, headers=_headers(), json=payload)
    resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        workout = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[claude_api] JSON parse error: {e}\nRaw response:\n{raw}")
        return None

    # Validate exercise names resolve to known templates
    from hevy import _resolve_template_id
    priority_names = {p["exercise_name"] for p in context.get("exercise_priorities", [])}
    for ex in workout.get("exercises", []):
        name = ex.get("exercise_name", "")
        if not _resolve_template_id(name):
            closest = next((n for n in priority_names if name.lower() in n.lower() or n.lower() in name.lower()), None)
            print(f"[claude_api] WARNING: '{name}' has no template ID — will be dropped. "
                  f"{'Did you mean: ' + repr(closest) + '?' if closest else 'Not in priority list.'}")

    return workout


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "..")
    from context import build_context
    ctx = build_context()
    print("[context]\n", json.dumps(ctx, indent=2, default=str))
    print("\n[calling Claude...]\n")
    workout = get_workout(ctx)
    print(json.dumps(workout, indent=2))
