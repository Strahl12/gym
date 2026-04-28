"""
run.py — Morning cron job orchestrator.

Runs each morning (e.g. 07:30 via crontab):
  1. Sync Withings bodyweight → DB
  2. Build training context from DB
  3. Call Claude → get workout prescription
  4. POST to Hevy as a routine (open Hevy at the gym and start it)
  5. Log everything to ~/gym_ai/logs/

Usage:
  python run.py                    # post as routine (default — start at the gym)
  python run.py --as-workout       # post as a completed workout instead
  python run.py --dry-run          # build context + call Claude, don't post to Hevy
  python run.py --context-only     # just print today's context, no Claude call
  python run.py --find-templates   # print Hevy template IDs for main lifts then exit
"""
import sys
import json
import logging
from datetime import date
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────
LOG_DIR = Path.home() / "gym_ai" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"{date.today().isoformat()}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def main(dry_run: bool = False, context_only: bool = False, find_templates: bool = False, as_workout: bool = False):

    # ── Template lookup helper ─────────────────────────────────────────────
    if find_templates:
        from hevy import print_template_ids_for_main_lifts
        print_template_ids_for_main_lifts()
        return

    # ── 1. Sync Withings ───────────────────────────────────────────────────
    try:
        from withings import sync_to_db
        sync_to_db(days=7)
    except Exception as e:
        log.warning(f"Withings sync failed (continuing without bodyweight): {e}")

    # ── 1b. Sync Hevy workouts → sets table ────────────────────────────────
    try:
        from hevy_sync import sync_to_db as hevy_sync
        hevy_sync(days=14)
    except Exception as e:
        log.warning(f"Hevy sync failed (continuing with existing data): {e}")

    # ── 2. Build context ───────────────────────────────────────────────────
    from context import build_context
    ctx = build_context()

    stype      = ctx['suggested_session_type']
    days_since = ctx.get('days_since_last_session_of_type')
    days_str   = f"{days_since}d" if days_since is not None else "never"
    log.info(f"Session type today: {stype} (last {stype}: {days_str} ago)")
    log.info(f"Bodyweight: {ctx.get('bodyweight_kg')}kg")
    log.info(f"Sessions last 7 days: {ctx['sessions_last_7_days']}")

    # Save context to log
    ctx_file = LOG_DIR / f"{date.today().isoformat()}_context.json"
    ctx_file.write_text(json.dumps(ctx, indent=2, default=str))

    if context_only:
        print(json.dumps(ctx, indent=2, default=str))
        return

    # ── 3. Call Claude ─────────────────────────────────────────────────────
    log.info("Calling Claude for workout prescription...")
    from claude_api import get_workout
    workout = get_workout(ctx)

    if not workout:
        log.error("Claude returned no workout. Aborting.")
        return

    log.info(f"Workout: {workout.get('title')}")
    log.info(f"Reasoning: {workout.get('reasoning')}")

    # Save workout prescription to log
    workout_file = LOG_DIR / f"{date.today().isoformat()}_workout.json"
    workout_file.write_text(json.dumps(workout, indent=2))

    # ── 3b. Log prescription to DB ─────────────────────────────────────────
    from context import log_prescription, mark_posted_to_hevy, active_block_id
    block_id     = active_block_id()
    prescription_id = log_prescription(workout, block_id=block_id)
    if prescription_id > 0:
        log.info(f"Prescription logged to DB (id={prescription_id}, block={block_id})")

    if dry_run:
        log.info("[dry-run] Skipping Hevy POST.")
        print(json.dumps(workout, indent=2))
        return

    # ── 4. Post to Hevy ────────────────────────────────────────────────────
    if as_workout:
        log.info("Posting to Hevy as completed workout...")
        from hevy import post_workout
        result  = post_workout(workout)
        hevy_id = result.get("workout", {}).get("id")
    else:
        log.info("Creating Hevy routine (open Hevy at the gym to start it)...")
        from hevy import post_routine
        result  = post_routine(workout)
        hevy_id = result.get("routine", {}).get("id")
    log.info(f"Hevy workout created: {hevy_id}")
    mark_posted_to_hevy(prescription_id)

    log.info("Done. Open Hevy to see today's session.")


if __name__ == "__main__":
    dry_run       = "--dry-run"       in sys.argv
    context_only  = "--context-only"  in sys.argv
    find_templates= "--find-templates" in sys.argv
    as_workout    = "--as-workout"    in sys.argv
    main(dry_run=dry_run, context_only=context_only, find_templates=find_templates, as_workout=as_workout)
