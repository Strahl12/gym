"""
claude_api.py — Sends training context to Claude and returns a structured workout.

Claude is given:
  - A system prompt encoding goals, progression rules, and output schema
  - A user message with today's derived context (lift history, balance, bodyweight)

Returns a validated dict matching the Hevy write schema.
"""
import json
import requests
from pathlib import Path
from typing import Optional
import config

_STATE_FILE = Path(config.DB_PATH).parent / "app_state.json"


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[claude_api] Could not read {_STATE_FILE.name} ({e}); resetting state.")
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def generate_block_directive(context: dict) -> str:
    """
    Called when TRAINING_MODE changes. Uses Sonnet to synthesise a strategic
    block directive from the athlete's history. Stored and injected into every
    subsequent prescription until the mode changes again.
    """
    from datetime import date
    lifts_summary = "\n".join(
        f"  {lift}: last {data['days_since_last_session']}d ago, "
        f"plateau={data['plateau_detected']}, "
        f"recent e1RMs: {[h['best_e1rm'] for h in data['recent_sessions'][:4]]}"
        for lift, data in context.get("main_lifts", {}).items()
    )
    prompt = (
        f"An athlete is switching their training mode to: {config.TRAINING_MODE}\n\n"
        f"Training goal:\n{config.GOAL.strip()}\n\n"
        f"Current main lift status:\n{lifts_summary}\n\n"
        f"Session balance (last 28 days): {context.get('session_balance_last_28_days', {})}\n\n"
        f"Write a concise block directive (150-200 words) for the AI programming this athlete. Cover:\n"
        f"1. Given the mode change to {config.TRAINING_MODE}, what's the strategic priority for the next 8-12 weeks?\n"
        f"2. Which lifts need the most attention and why?\n"
        f"3. Any red flags in the current data that should shape programming?\n"
        f"4. Suggested progression structure for this block.\n"
        f"Be specific to the data. This will be injected into every prescription prompt for this block."
    )
    try:
        resp = requests.post(
            ANTHROPIC_URL,
            headers=_headers(),
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        directive = resp.json()["content"][0]["text"].strip()
        print(f"[claude_api] Block directive generated for {config.TRAINING_MODE} mode.")
        return directive
    except Exception as e:
        print(f"[claude_api] Block directive generation failed: {e}")
        return ""


def check_mode_change(context: dict) -> Optional[str]:
    """
    Returns the active block directive. Regenerates it if TRAINING_MODE changed
    since the last run. Call after build_context() and before get_workout().
    """
    from datetime import date
    state = _load_state()
    directive = state.get("block_directive", "")

    if state.get("last_training_mode") != config.TRAINING_MODE or not directive:
        print(f"[claude_api] Training mode {'changed' if state.get('last_training_mode') else 'initialised'} "
              f"→ {config.TRAINING_MODE}. Generating block directive...")
        directive = generate_block_directive(context)
        state["last_training_mode"]    = config.TRAINING_MODE
        state["block_directive"]       = directive
        state["block_directive_date"]  = date.today().isoformat()
        _save_state(state)

    return directive or None

CLAUDE_MODEL   = "claude-sonnet-4-6"
ANTHROPIC_URL  = "https://api.anthropic.com/v1/messages"
MAX_TOKENS     = 2000

LEGACY_SYSTEM_PROMPT = """
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
1. Chest compound (main lift): always Incline Barbell Bench Press
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
priority list. 2–3 sets.
Prefer exercises that can be progressively loaded when training alone:
  good: Cable Crunch, Ab Wheel Rollout, Hanging Leg Raise, Weighted Sit Up, Dragon Flag
  avoid unless top-priority: Plank, Hollow Body Hold (hard to weight solo)

## Body composition mode
If a goal mode is provided, adjust accessory volume accordingly:
- cut:      keep main lifts unchanged; reduce accessory sets to 3; don't push for PRs on accessories
- bulk:     increase accessory sets to 4–5 where time allows; push progression aggressively
- maintain: standard volume (default)

## Main lifts in context
The context includes main lift history for push lifts (Bench, OHP, Dip) and legs (Front Squat).
On PULL and ARMS days, ignore that data — it is shown for reference only.
Do NOT include push or legs main lifts in pull or arms sessions.

## Muscle clash rule
The 3 mandatory template slots are always included regardless of what was trained yesterday.
For accessory slots only: you will receive the full exercise list from the last session.
Do not include any accessory that trains the same PRIMARY muscle group as an exercise done
in the last session. Use the exercise name to infer the primary muscle.
Example: if "Lat Pulldown - Close Grip (Cable)" was done yesterday, skip all lat/vertical-pull
accessories today even if today is a pull day — those lats trained less than 24 hours ago.

## Exercise selection — use the priority list
You will receive a pre-ranked exercise priority list for today's session.
priority = days_since_last / target_freq_days. Values > 1.0 are overdue.
- Always include all main lifts (is_main=True) regardless of priority, unless progression rules require skipping (e.g. insufficient recovery).
- Pick accessories from the TOP of the priority list — prefer exercises most overdue.
- Select 2–4 accessories total; skip any that duplicate a main lift's muscle pattern.
- If you skip a high-priority exercise for a valid reason, note it in reasoning.
- If no priority list is provided, fall back to the session type defaults above.
- CRITICAL: use exercise names EXACTLY as they appear in the priority list. Copy the name character-for-character. Do NOT paraphrase, abbreviate, rename, or invent any exercise name. If you use a name not in the priority list, the exercise will be silently dropped from the session.

## Exercise variant preference
1. For compound main lifts: prefer barbell variants (best overload potential).
2. For isolation and single-joint exercises (calves, curls, lateral raises, pushdowns, etc.):
   machine and cable variants are often equal or superior to barbell — use priority score and
   history to decide, not a blanket barbell preference.
3. NEVER choose a bodyweight/unloaded version if a loaded equivalent exists in the priority list.
   Example: "Standing Calf Raise (Machine)" beats "Standing Calf Raise" every time.
4. Designated bodyweight main lifts (Pull Up, Weighted Dip) are always preferred over alternatives.
5. The athlete trains at a gym with full equipment. Assume all variants are available.

## Session duration and rest times
You will be given a target session duration and day type (weekday/weekend).
Use these estimates to fill the session:
- General warm-up: 10 min (not counted as an exercise)
- Main barbell lift (with 2 warm-up sets + 4 working sets): ~20 min
- Bodyweight main lift (4 working sets): ~15 min
- Accessory compound (3–4 sets): ~12 min
- Isolation exercise (3–4 sets): ~8 min
Add accessories until you reach the target duration. Do not exceed it by more than 10 min.

Set rest_seconds per exercise based on day type (Hevy uses this for the built-in rest timer).
Do NOT put rest times in the notes field.

Weekday (60 min target — tighter rest to fit session into a busy day):
- Main barbell compound: 150
- Accessory compound: 90
- Isolation: 60
- Bodyweight / core: 45

Weekend (90 min target — full rest, no compromise on recovery between sets):
- Main barbell compound: 210
- Accessory compound: 150
- Isolation: 75
- Bodyweight / core: 60

## Output format — return ONLY valid JSON, no markdown, no explanation outside the JSON
{
  "session_type": "push|pull|legs|arms",
  "title": "short descriptive title",
  "reasoning": "2-3 sentences explaining today's prescription and any adjustments",
  "exercises": [
    {
      "exercise_name": "exact name matching Hevy exercise library",
      "is_main_lift": true,
      "muscle_group": "<see valid values below>",
      "equipment_category": "<see valid values below>",
      "exercise_type": "<see valid values below>",
      "rest_seconds": 180,
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

## Exercise metadata — use ONLY these exact values

muscle_group (pick the primary muscle):
  abdominals | shoulders | biceps | triceps | forearms | quadriceps | hamstrings |
  calves | glutes | abductors | adductors | lats | upper_back | traps | lower_back |
  chest | neck | full_body | other

equipment_category:
  barbell | dumbbell | kettlebell | machine | plate | resistance_band | suspension | none | other

exercise_type:
  weight_reps        — standard loaded exercise (most exercises)
  reps_only          — pure bodyweight with no load option
  bodyweight_weighted — bodyweight exercise with optional added weight (Pull Up, Dip)
  duration           — timed sets (planks, carries)
""".strip()


def _build_system_prompt(block_directive: Optional[str] = None) -> str:
    """Build the system prompt from config data structures."""
    pr  = config.PROGRESSION
    inc = config.EQUIPMENT_INCREMENTS
    te  = config.SESSION_TIME_ESTIMATES

    MODE_DETAILS = {
        "strength":     "Rep range 4–6, load 80–90% 1RM. Prioritise adding weight over reps.",
        "hypertrophy":  "Rep range 8–12, load 65–80% 1RM, rest 60–90s. Volume and time under tension.",
        "powerlifting": "Rep range 1–5 on main lifts, load 85–95% 1RM, rest 4–6 min. Accessories 6–8 reps.",
    }
    mode_detail = MODE_DETAILS.get(config.TRAINING_MODE, "")

    # ── Block directive (generated on mode change) ─────────────────────────
    directive_section = ""
    if block_directive:
        directive_section = f"\n## Block directive\n{block_directive}\n"

    # ── Skill work ─────────────────────────────────────────────────────────
    skill_section = ""
    if config.SKILL_WORK:
        skill_list = ", ".join(config.SKILL_WORK)
        skill_section = (
            f"\n## Skill work (optional, after core)\n"
            f"If time remains after the core slot, add one skill item as a duration exercise.\n"
            f"Available: {skill_list}\n"
            f"Prescribe as exercise_type=duration, ~{config.SESSION_TIME_ESTIMATES.get('skill', 5)} min."
        )

    # ── Session slot tables ────────────────────────────────────────────────
    slot_lines = []
    for stype, slots in config.SESSION_TEMPLATES.items():
        slot_lines.append(f"\n### {stype.upper()}")
        for i, s in enumerate(slots, 1):
            action = f"FIXED: {s['fixed']}" if s.get("fixed") else "pick from priority list"
            pattern = s.get("movement_pattern") or "any"
            tags = []
            if s.get("is_compound") is True:
                tags.append("compound")
            elif s.get("is_compound") is False:
                tags.append("isolation")
            if s.get("muscle"):
                tags.append(f"muscle={s['muscle']}")
            if s.get("exclude"):
                tags.append(f"exclude={s['exclude']}")
            if s.get("optional"):
                tags.append("optional")
            tag_str = f"  [{', '.join(tags)}]" if tags else ""
            slot_lines.append(f"  {i}. {s['slot']}: {pattern}{tag_str} — {action}")
        slot_lines.append("  +. core: core_flexion or core_anti_extension — pick from core priority list, 2–3 sets, prefer progressively loaded variants (Cable Crunch, Ab Wheel, Hanging Leg Raise)")

    # ── Progression block ──────────────────────────────────────────────────
    pr_lines = [
        f"BASELINE: anchor next prescription on the previous session's WORKING WEIGHT (the mode/most-common load drilled across sets), NOT on the top set. A one-off heavy single does not move the baseline.",
        f"  Recent-session lines show: `working {{w}}kg × {{r}} × {{n}} sets [top set: {{w}}kg × {{r}}]`. Use the `working` value as your baseline; the `top set` is context only.",
        f"no_plateau — gate on last_set_rpe (RPE of the final working set) from most recent session:",
        f"  last_set_rpe ≤7 (capacity remaining): +{pr['increase_kg']}kg",
        f"  last_set_rpe 8–9 (optimal stimulus): same weight",
        f"  last_set_rpe ≥9.5 or reps cut short: −{pr['increase_kg']}kg",
        f"  no RPE data: +{pr['increase_kg']}kg if all sets hit top of rep range; else same weight",
        f"plateau (≥{config.PLATEAU_SESSIONS} sessions flat): reset to {int(pr['plateau_reset_pct']*100)}% working weight rounded down to nearest {config.EQUIPMENT_INCREMENTS['barbell']}kg, reps {pr['plateau_reps'][0]}–{pr['plateau_reps'][1]}, note in reasoning",
        "bodyweight lifts: apply rules to added weight only; prescribe pure BW only if no added-weight history",
        f"HARD CAP: prescribed weight must NOT exceed previous session's working_weight + {pr['max_increase_kg']}kg. Reject any output that would jump further, even if the top set was higher.",
        f"absent >{pr['deload_threshold_days']} days: deload to {int(pr['deload_weight_pct']*100)}% of last weight, higher reps",
        f"warm-up sets: {len(pr['warmup_pcts'])} sets at {' / '.join(str(int(p*100))+'%' for p in pr['warmup_pcts'])} of working weight (main barbell lifts only), label is_warmup: true",
    ]

    # ── Timing block ──────────────────────────────────────────────────────
    timing_lines = [
        f"  warmup (not an exercise): {te['warmup_general']} min",
        f"  main barbell (incl. warmup sets): {te['main_barbell']} min",
        f"  main bodyweight: {te['main_bodyweight']} min",
        f"  accessory compound: {te['accessory_compound']} min",
        f"  isolation: {te['isolation']} min",
        f"  core: {te['core']} min",
    ]

    return f"""You are a strength programming assistant. Prescribe today's gym session from the provided context.

## Training mode: {config.TRAINING_MODE}
{mode_detail}
{directive_section}

## Goal
{config.GOAL.strip()}

## Session structure
Fill slots in order. FIXED = always use the named exercise. PICK = highest-priority matching exercise from the priority list.
Never assign the same exercise to two slots. Compounds always precede isolations.
{"".join(slot_lines)}

## Progression
{chr(10).join("  " + l for l in pr_lines)}

## Re-entry override (global, applies to ALL lifts this session)
If the user message contains a "Re-entry status" section with a rule, that rule OVERRIDES the
per-lift progression above for THIS session — apply the global re-entry adjustment first, then
resume normal progression rules from the next session onward. Note the re-entry in reasoning.

## Recovery ramp override (global, applies to ALL lifts this session)
If the user message contains a "Recovery state: RAMPING" section, the ramp rule OVERRIDES the
bulk-mode volume increase: keep accessory sets at 3 (not 4–5). RPE cap 8 on all working sets.
No progression on main lifts. Note the ramping state in reasoning.

## Conditioning override (global, applies to ALL lifts this session)
If the user message contains a "Conditioning context" section, the buffer rule OVERRIDES per-lift
progression for THIS session. Apply the RPE cap and weight adjustment as stated. Note the
conditioning context in reasoning. If both Re-entry AND Conditioning apply, take the MORE
conservative of the two (whichever reduces load/RPE more).

## Session timing
Add slots until you reach the target duration; do not exceed by more than 10 min.
{chr(10).join(timing_lines)}
Rest seconds per exercise type are provided in the user message — set rest_seconds on each exercise. Do NOT put rest times in notes.

## Body composition mode
  cut:      keep main lifts; reduce accessory sets to 3; no PR chasing on accessories
  bulk:     push accessory sets to 4–5; progress aggressively
  maintain: standard volume

## Training mode (rep range bias)
  strength:    main lifts at BOTTOM of rep range (1–6); accessories 4–8. Prioritise load over volume.
  hypertrophy: main lifts at TOP of rep range or extend to 12; accessories 8–12. Prioritise volume/TUT.
  mixed:       main lifts at MIDDLE of rep range (5–8); accessories 6–10.
The user message will state the active training mode — apply it to ALL rep prescriptions this session.

## Muscle clash rule
FIXED slots are always included regardless of last session. For PICK slots only: skip any
exercise whose primary muscle was already trained last session.

## RPE interpretation (accessories and general calibration)
Main lift progression is gated by last_set_rpe per the rules above.
For accessories, apply the same logic directionally:
- RPE ≤7: capacity remaining — consider progressing weight or reps
- RPE 8–9: appropriate stimulus — maintain or small increment
- RPE ≥9.5 / reps cut short: reduce load next session

## Exercise selection
- Names VERBATIM from the priority list — character-for-character. Names not in the list will be silently dropped.
- Pick accessories from the top of the priority list (highest priority = most overdue).
- Prefer loaded variants over unloaded (e.g. Machine Calf Raise > Standing Calf Raise).
- Compounds prefer barbell; isolations use priority score and history — no blanket barbell preference.
{skill_section}

## Output — return ONLY valid JSON
If the user message contains a Fatigue section with verdict "rest" OR signals indicate excessive fatigue
(very high last-set RPE combined with negative e1RM slope, etc.), return INSTEAD:
{{ "rest_recommended": true, "reason": "<2-3 sentences citing the specific signals>" }}
Otherwise return the workout schema below.

{{
  "session_type": "push|pull|legs|arms",
  "title": "short descriptive title",
  "reasoning": "2-3 sentences",
  "exercises": [
    {{
      "exercise_name": "exact name",
      "is_main_lift": true,
      "muscle_group": "<valid value>",
      "equipment_category": "<valid value>",
      "exercise_type": "<valid value>",
      "rest_seconds": 180,
      "sets": [
        {{"reps": 5, "weight_kg": 45.0, "is_warmup": true}},
        {{"reps": 5, "weight_kg": 67.5, "is_warmup": true}},
        {{"reps": 5, "weight_kg": 90.0}},
        {{"reps": 5, "weight_kg": 90.0}}
      ],
      "notes": "optional"
    }}
  ]
}}
All weights in kg. Each exercise appears ONCE. Warmup and working sets go in the same
sets array for that exercise — never output the same exercise name twice.

## Valid metadata values
muscle_group: abdominals | shoulders | biceps | triceps | forearms | quadriceps | hamstrings | calves | glutes | abductors | adductors | lats | upper_back | traps | lower_back | chest | neck | full_body | other
equipment_category: barbell | dumbbell | kettlebell | machine | plate | resistance_band | suspension | none | other
exercise_type: weight_reps | reps_only | bodyweight_weighted | bodyweight_assisted | duration | distance_duration""".strip()


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

    phase = context.get("focus_phase")
    phase_lines = []
    if phase:
        if phase["phase"] == "complement" and phase["complement_lift"]:
            phase_lines = [
                f"Focus lift: {phase['focus_lift']} (progressing well — currently in complement phase)",
                f"Emphasis this session: {phase['complement_lift']} — choose accessories that build "
                f"supporting strength for {phase['complement_lift']}, which feeds back into {phase['focus_lift']}",
                f"Complement phase day {phase['phase_age_days']} of {config.COMPLEMENT_PHASE_DAYS}",
            ]
        else:
            phase_lines = [
                f"Focus lift: {phase['focus_lift']} — this is the primary lift to progress",
                f"Choose accessories that directly support {phase['focus_lift']} strength",
            ]

    inc = config.EQUIPMENT_INCREMENTS
    last_exercises = context.get("last_session_exercises", [])
    is_weekend     = context.get("is_weekend", False)
    day_type       = "weekend" if is_weekend else "weekday"
    duration       = config.TARGET_DURATION_MINUTES.get(day_type, 90)
    rest           = config.REST_SECONDS[day_type]

    lines = [
        f"Date: {today}  ({day_type})",
        f"Target session duration: {duration} minutes",
        f"Rest seconds: main_barbell={rest['main_barbell']}  accessory_compound={rest['accessory_compound']}  isolation={rest['isolation']}  bodyweight_core={rest['bodyweight_core']}",
        f"Suggested session type: {stype}",
        f"Last session type: {last_type or 'unknown'}",
        f"Last session exercises: {', '.join(last_exercises) if last_exercises else 'none'}",
        f"Sessions in last 7 days: {sessions_7d}",
        f"Session balance (last 28 days): {balance}",
        f"Equipment increments — barbell: {inc['barbell']}kg | cable: {inc['cable']}kg"
        f" | dumbbell: {inc['dumbbell']}kg/side | machine: {inc['machine']}kg",
    ]

    if phase_lines:
        lines.append("\n## Focus lift phase")
        lines.extend(f"  {l}" for l in phase_lines)

    if bw:
        bw_parts = [f"{bw}kg"]
        if context.get("muscle_mass_kg"):
            bw_parts.append(f"muscle {context['muscle_mass_kg']}kg")
        if context.get("body_fat_pct"):
            bw_parts.append(f"BF {context['body_fat_pct']}%")
        if context.get("bodyweight_avg_7d"):
            bw_parts.append(f"7d avg {context['bodyweight_avg_7d']}kg")
        lines.append(f"Bodyweight: {' | '.join(bw_parts)}")
    if bw_trend is not None:
        direction = "gaining" if bw_trend > 0 else "losing"
        lines.append(f"Weight trend (30d): {direction} {abs(bw_trend):.2f}kg/week")

    comp = context.get("body_composition_trends") or {}
    muscle_dt = comp.get("muscle_kg_per_week")
    fat_dt    = comp.get("body_fat_pct_per_week")
    if muscle_dt is not None or fat_dt is not None:
        parts = []
        if muscle_dt is not None:
            parts.append(f"muscle {muscle_dt:+.2f}kg/wk")
        if fat_dt is not None:
            parts.append(f"BF {fat_dt:+.2f}%/wk")
        lines.append(f"Composition trend (30d): {' | '.join(parts)}")

    target = config.TARGET_WEIGHT_KG
    if target and bw:
        delta = round(target - bw, 1)
        if abs(delta) > 0.1:
            verb = "to gain" if delta > 0 else "to lose"
            rate = config.WEIGHT_RATE_KG_PER_WEEK
            eta = f", ~{abs(delta / rate):.0f}wk at {rate:+.2f}kg/wk pace" if rate else ""
            lines.append(f"Target: {target}kg ({abs(delta)}kg {verb}{eta})")

    bw_hist = context.get("bodyweight_history") or []
    if len(bw_hist) >= 3:
        recent = ", ".join(f"{h['date'][5:]}={h['weight_kg']}" for h in bw_hist[:7])
        lines.append(f"Recent weights (last 7): {recent}")

    # Body composition goal
    goal_mode = config.GOAL_MODE
    if goal_mode != "maintain" or config.TARGET_WEIGHT_KG:
        comp_parts = [f"Goal mode: {goal_mode}"]
        if config.TARGET_WEIGHT_KG:
            comp_parts.append(f"target weight {config.TARGET_WEIGHT_KG}kg")
        if config.WEIGHT_RATE_KG_PER_WEEK is not None:
            sign = "+" if config.WEIGHT_RATE_KG_PER_WEEK > 0 else ""
            comp_parts.append(f"rate {sign}{config.WEIGHT_RATE_KG_PER_WEEK}kg/wk")
        lines.append("Body composition: " + " | ".join(comp_parts))

    tm = getattr(config, "TRAINING_MODE", "mixed")
    tm_guide = {
        "strength":    "Use BOTTOM of each main lift's rep range. Accessories 4-8 reps. Load priority.",
        "hypertrophy": "Use TOP of each main lift's rep range (extend to 12 if range permits). Accessories 8-12 reps. Volume priority.",
        "mixed":       "Use MIDDLE of each main lift's rep range (5-8). Accessories 6-10 reps.",
    }.get(tm, "Use middle of each main lift's rep range.")
    lines.append(f"\n## Training mode: {tm}\n  {tm_guide}")

    rec = context.get("recovery") or {}
    if rec.get("state") == "ramping":
        lines.append(
            f"\n## Recovery state: RAMPING (post-{rec.get('reason', 'break')})"
            f"\n  Clean sessions back: {rec.get('clean_streak', 0)}/{rec.get('clean_needed', 2)}"
            f"\n  Rule: RPE cap 8 on all working sets. Bulk volume increase SUSPENDED (cap accessories at 3 sets)."
            f"\n  Repeat last weight on main lifts unless re-entry rule below dictates a deload."
            f"\n  Exit condition: 2 consecutive sessions where first_set_rpe ≤7.5 AND last_set_rpe ≤8.5."
        )

    ra = context.get("recurring_activity") or {}
    if ra.get("role") in ("pre", "post"):
        act  = ra["activity"]
        load = ", ".join(act.get("movement_load", []))
        safe = ", ".join(act.get("safe_session_types", []))
        if ra["role"] == "pre":
            lines.append(
                f"\n## Conditioning context"
                f"\n  {act['name'].title()} TOMORROW ({act['duration_minutes']} min, {act['intensity']} intensity)."
                f"\n  Heavily taxes: {load}. Safe session types today: {safe}."
                f"\n  Rule: Cap RPE at 7 on ALL working sets. Reduce accessory volume by ~25%."
                f"\n  Do not chase PRs. Goal: leave body fresh for {act['name']}."
            )
        else:
            lines.append(
                f"\n## Conditioning context"
                f"\n  {act['name'].title()} YESTERDAY ({act['duration_minutes']} min, {act['intensity']} intensity)."
                f"\n  CNS + connective tissue fatigued. Heavily taxed: {load}."
                f"\n  Rule: −5% on all working weights. Cap RPE at 7. Avoid heavy {load}."
                f"\n  Treat this session as deload/recovery — quality movement, not load chasing."
            )

    dsa = context.get("days_since_any_session")
    if dsa is not None:
        if dsa <= 3:
            re_entry = "normal — no re-entry adjustment needed"
        elif dsa <= 7:
            re_entry = "REPEAT last weight (no progression). RPE cap 8 on all sets. Full warm-up."
        elif dsa <= 14:
            re_entry = "DELOAD −5% on all working weights. Rebuild over 2 sessions before resuming progression."
        else:
            re_entry = "DELOAD −10% on all working weights. Rebuild over 3 sessions before resuming progression."
        lines.append(f"\n## Re-entry status\n  Days since last session: {dsa}\n  Rule: {re_entry}")

    fat = context.get("fatigue") or {}
    if fat:
        fc = fat.get("components", {})
        lines.append("\n## Fatigue signals")
        lines.append(f"  Score: {fat.get('score')}/100  ({fat.get('verdict')})")
        lines.append(f"  Last-session avg last-set RPE: {fc.get('last_set_rpe_avg')}")
        lines.append(f"  Focus-lift e1RM 14d slope: {fc.get('e1rm_slope_kg_per_week_14d')}kg/wk")
        lines.append(f"  Consecutive training days: {fc.get('consecutive_training_days')}/{config.MAX_CONSECUTIVE_DAYS}")
        lines.append(f"  Bodyweight 14d slope: {fc.get('bodyweight_slope_kg_per_week_14d')}kg/wk (goal: {config.GOAL_MODE})")
        if fc.get("notes_hits"):
            lines.append(f"  Note flags: {'; '.join(fc['notes_hits'])}")
        lines.append("  If verdict is 'rest' OR you judge accumulated fatigue is excessive, return"
                     ' {"rest_recommended": true, "reason": "..."} INSTEAD of a workout.')
        lines.append("  If verdict is 'caution', you MAY still prescribe a deload session (lower volume,"
                     " RPE cap 7) — note this in the reasoning.")

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
                if h.get('last_set_rpe') is not None:
                    rpe_str = f"  last-set RPE {h['last_set_rpe']}"
                    if h.get('avg_rpe') is not None:
                        rpe_str += f" (avg {h['avg_rpe']})"
                elif h.get('avg_rpe') is not None:
                    rpe_str = f"  RPE avg {h['avg_rpe']}"
                else:
                    rpe_str = ""
                ww, wr, sc = h.get('working_weight'), h.get('working_reps'), h.get('working_set_count')
                tw, tr = h.get('top_weight'), h.get('top_reps')
                working_str = f"{ww}kg × {wr} × {sc} sets" if ww else f"BW × {wr or tr} × {sc} sets"
                top_str = ""
                if ww and (tw != ww or tr != wr):
                    top_str = f"  [top set: {tw}kg × {tr}]"
                lines.append(f"    {h['date']}: working {working_str}{top_str} — {e1rm_str}{rpe_str}")
        else:
            lines.append("  No history — treat as first session: start conservative (≈60% estimated 1RM, 4×8), no warm-up sets, note in reasoning.")

    recent_workouts = context.get("recent_workouts", [])
    if recent_workouts:
        lines.append("\n## Recent workouts (last 28 days, newest first)")
        for session in recent_workouts:
            lines.append(f"\n  {session['date']} ({session['session_type']}):")
            for ex in session["exercises"]:
                if ex.get("last_set_rpe") is not None:
                    rpe_str = f"  RPE {ex['last_set_rpe']}"
                    if ex.get("avg_rpe") is not None:
                        rpe_str += f" (avg {ex['avg_rpe']})"
                elif ex.get("avg_rpe") is not None:
                    rpe_str = f"  RPE avg {ex['avg_rpe']}"
                else:
                    rpe_str = ""
                ww = ex.get("working_weight_kg")
                wr = ex.get("working_reps")
                tw = ex.get("top_weight_kg")
                tr = ex.get("top_reps")
                sc = ex.get("sets")
                base = f"{ww}kg × {wr} × {sc} sets" if ww else f"BW × {wr or tr} × {sc} sets"
                top_str = ""
                if ww and (tw != ww or tr != wr):
                    top_str = f"  [top: {tw}kg × {tr}]"
                lines.append(f"    {ex['exercise']}: {base}{top_str}{rpe_str}")

    priorities = context.get("exercise_priorities", [])
    ex_stats = context.get("exercise_stats", {})

    def _stats_str(name: str) -> str:
        s = ex_stats.get(name)
        if not s:
            return ""
        parts = []
        if s.get("current_e1rm"):
            parts.append(f"e1RM {s['current_e1rm']}kg")
        if s.get("best_e1rm") and s.get("current_e1rm") and s["best_e1rm"] > s["current_e1rm"]:
            parts.append(f"best {s['best_e1rm']}kg")
        if s.get("total_sessions"):
            parts.append(f"{s['total_sessions']}×")
        if s.get("trend"):
            parts.append(s["trend"])
        return f"  [{', '.join(parts)}]" if parts else ""

    if priorities:
        from hevy import _resolve_template_id
        from context import exercise_priorities as _core_priorities
        postable = [p for p in priorities if p["is_main_lift"] or _resolve_template_id(p["exercise_name"])]
        lines.append("\n## Exercise priority list (pick accessories from the top)")
        lines.append("  (* = main lift)  format: name | movement_pattern | days_since | priority | hist | [e1RM, trend]")
        lines.append("  These are the ONLY valid exercise names. Use them verbatim.")
        lines.append("  VARIANT SELECTION (mandatory):")
        lines.append("    1. For each PICK slot, find all candidates matching the slot's movement pattern.")
        lines.append("    2. Among those candidates, pick the one with the highest hist=N× session count.")
        lines.append("    3. Only override to a lower-session variant if the preferred one was done within 7 days.")
        lines.append("    4. Never choose a 0-session exercise if any established variant (hist≥3) exists for the slot.")
        for p in postable[:25]:
            marker = "*" if p["is_main_lift"] else " "
            days   = p["days_since_last"] if p["days_since_last"] is not None else "never"
            sc     = p.get("session_count")
            sc_str = f"  hist={sc}×" if sc and sc > 0 else ""
            mp     = p.get("movement_pattern", "")
            mp_str = f"  [{mp}]" if mp else ""
            lines.append(
                f"  {marker} {p['exercise_name']}:{mp_str}"
                f"  days={days}, priority={p['priority']}"
                f"{sc_str}"
                f"{_stats_str(p['exercise_name'])}"
            )
        core = [p for p in _core_priorities("core") if _resolve_template_id(p["exercise_name"])]
        if core:
            lines.append("\n## Core priority list (pick 1 for the final slot)")
            lines.append("  These are the ONLY valid core names. Use them verbatim.")
            for p in core[:8]:
                days = p["days_since_last"] if p["days_since_last"] is not None else "never"
                lines.append(
                    f"  {p['exercise_name']}: days={days}, freq={p['target_freq_days']}d, priority={p['priority']}"
                    f"{_stats_str(p['exercise_name'])}"
                )

    creator_recs = context.get("creator_recommendations", [])
    if creator_recs:
        lines.append("\n## Creator-recommended exercises (from trusted YouTube channels)")
        lines.append("  These exercises have been positively mentioned by trusted fitness creators.")
        lines.append("  Prefer these when choosing between equally-ranked accessories.")
        lines.append("  format: canonical_name | score (higher = stronger endorsement)")
        for r in creator_recs:
            lines.append(f"  {r['canonical']}: score={r['score']:.2f}")

    notes = context.get("session_notes", [])
    directives    = [n for n in notes if n.get("source") == "user_directive"]
    regular_notes = [n for n in notes if n.get("source") not in ("user_directive",)]

    if directives:
        lines.append("\n## User directives (athlete explicitly flagged these — follow them)")
        for n in directives:
            lines.append(f"  {n['date']}: {n['note']}")

    if regular_notes:
        lines.append("\n## Session notes (injury signs / observations from Hevy and manual logs)")
        lines.append("  Reduce load or substitute exercises for any flagged movements.")
        SOURCE_LABEL = {"manual": "note", "hevy_workout": "hevy", "hevy_exercise": "hevy",
                        "overwrite_review": "review"}
        for n in regular_notes:
            label = SOURCE_LABEL.get(n.get("source", "manual"), "note")
            lines.append(f"  [{label}] {n['date']}: {n['note']}")

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

    coverage = context.get("movement_coverage", {})
    gaps = coverage.get("gaps", [])
    covered = coverage.get("covered", {})
    if gaps or covered:
        lines.append("\n## Movement pattern coverage (last 14 days)")
        if covered:
            covered_summary = ", ".join(sorted(covered.keys()))
            lines.append(f"  Covered: {covered_summary}")
        if gaps:
            lines.append(f"  GAPS (not trained in 14 days): {', '.join(gaps)}")
            lines.append("  If any session-appropriate accessory slot can address a gap, prefer it.")
            lines.append("  Gaps are not mandatory overrides — only fill if the exercise fits this session type.")

    lines.append("\nPrescribe today's full session as JSON.")
    return "\n".join(lines)


def get_workout(context: dict, legacy: bool = False,
                block_directive: Optional[str] = None) -> Optional[dict]:
    """
    Calls Claude with the training context. Returns parsed workout dict or None on failure.
    Pass legacy=True to use the old hardcoded system prompt instead of the data-driven one.
    """
    if legacy:
        MODE_DETAILS = {
            "strength":     "Rep range 4–6, load 80–90% 1RM, rest 2–4 min between sets. Prioritise adding weight over adding reps.",
            "hypertrophy":  "Rep range 8–12, load 65–80% 1RM, rest 60–90s. Prioritise volume and time under tension.",
            "powerlifting": "Rep range 1–5 on main lifts, load 85–95% 1RM, rest 4–6 min on compounds. Accessories at 6–8 reps.",
        }
        system = (LEGACY_SYSTEM_PROMPT
                  .replace("{goal}", config.GOAL)
                  .replace("{mode}", config.TRAINING_MODE)
                  .replace("{mode_detail}", MODE_DETAILS.get(config.TRAINING_MODE, "")))
    else:
        system = _build_system_prompt(block_directive=block_directive)
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

    # Deduplicate exercises (keep first occurrence)
    seen: set[str] = set()
    deduped = []
    for ex in workout.get("exercises", []):
        name = ex.get("exercise_name", "")
        if name.lower() in seen:
            print(f"[claude_api] Removed duplicate exercise: '{name}'")
            continue
        seen.add(name.lower())
        deduped.append(ex)
    workout["exercises"] = deduped

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
