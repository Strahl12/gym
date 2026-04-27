"""
run.py — Morning cron job orchestrator.

Runs each morning (e.g. 07:30 via crontab):
  1. Sync Withings bodyweight → DB
  2. Build training context from DB
  3. Call Claude → get workout prescription
  4. POST workout to Hevy
  5. Log everything to ~/gym_ai/logs/

Usage:
  python run.py                    # full run
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


def main(dry_run: bool = False, context_only: bool = False, find_templates: bool = False):

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

    # ── 2. Build context ───────────────────────────────────────────────────
    from context import build_context
    ctx = build_context()

    log.info(f"Session type today: {ctx['suggested_session_type']}")
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

    if dry_run:
        log.info("[dry-run] Skipping Hevy POST.")
        print(json.dumps(workout, indent=2))
        return

    # ── 4. Post to Hevy ────────────────────────────────────────────────────
    log.info("Posting workout to Hevy...")
    from hevy import post_workout
    result = post_workout(workout)
    log.info(f"Hevy workout created: {result.get('workout', {}).get('id')}")

    log.info("Done. Open Hevy to see today's session.")


if __name__ == "__main__":
    dry_run       = "--dry-run"       in sys.argv
    context_only  = "--context-only"  in sys.argv
    find_templates= "--find-templates" in sys.argv
    main(dry_run=dry_run, context_only=context_only, find_templates=find_templates)
