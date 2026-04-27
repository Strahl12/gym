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

## Session types
- push: bench, OHP, weighted dips — accessories: lateral raises, triceps work
- pull: pull ups — accessories: lat pulldown, cable row, face pulls, curls
- legs: front squat — accessories: leg press, leg curl, calf raises
- arms: start with 2 compounds (e.g. Close Grip Bench Press + Barbell/EZ Curl, or Dip + Chin Up),
        then isolation work (cable curls, pushdowns, hammer curls, etc.)

## Exercise selection — use the priority list
You will receive a pre-ranked exercise priority list for today's session.
priority = days_since_last / target_freq_days. Values > 1.0 are overdue.
- Always include all main lifts (is_main=True) regardless of priority, unless progression rules require skipping (e.g. insufficient recovery).
- Pick accessories from the TOP of the priority list — prefer exercises most overdue.
- Select 2–4 accessories total; skip any that duplicate a main lift's muscle pattern.
- If you skip a high-priority exercise for a valid reason, note it in reasoning.
- If no priority list is provided, fall back to the session type defaults above.

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
        if data["session_type"] != stype and stype != "arms":
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
        lines.append("\n## Exercise priority list (pick accessories from the top)")
        lines.append("  (* = main lift)  format: name | days_since | target_freq | priority")
        for p in priorities[:20]:
            marker = "*" if p["is_main_lift"] else " "
            days   = p["days_since_last"] if p["days_since_last"] is not None else "never"
            lines.append(
                f"  {marker} {p['exercise_name']}: "
                f"days={days}, freq={p['target_freq_days']}d, priority={p['priority']}"
            )

    lines.append("\nPrescribe today's full session as JSON.")
    return "\n".join(lines)


def get_workout(context: dict) -> Optional[dict]:
    """
    Calls Claude with the training context. Returns parsed workout dict or None on failure.
    """
    system = SYSTEM_PROMPT.replace("{goal}", config.GOAL)  # ← change this line
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
