"""
run.py — Morning workout orchestrator (per user).

All real commands require --user <name>. The wizard `--add-user <name>`
creates a new user.

Runs:
  1. Sync Withings bodyweight → user DB
  2. Sync Hevy completed workouts → user DB
  3. Build training context from DB
  4. Call Claude → get workout prescription
  5. POST to Hevy as a routine (PUT-updates the pinned slot)
  6. Log everything to users/<name>/logs/

Usage:
  python run.py --user john                            # post as routine (default)
  python run.py --user john --as-workout               # post as a completed workout
  python run.py --user john --dry-run                  # build context + call Claude, skip Hevy
  python run.py --user john --confirm                  # print workout, ask y/n before posting
  python run.py --user john --context-only             # print today's context, no Claude call
  python run.py --user john --find-templates           # print Hevy template IDs for main lifts
  python run.py --user john --note "left shoulder tight"
  python run.py --user john --set-focus push "Strict Military Press"
  python run.py --user john --force                    # override rest day
  python run.py --user john --creator-recs             # include creator recs
  python run.py --user john --exclude "Calf Raise (Barbell)"   # append to profile.py exclusions
  python run.py --user john --withings-auth            # one-time Withings OAuth
  python run.py --user john --activity-list / --activity-add / --activity-remove
  python run.py --add-user alice                       # interactive wizard
"""
import sys
import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)


def _setup_logging(log_dir: str) -> None:
    """Configure root logger to write to <user>/logs/<date>.log and stdout."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / f"{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _send_proposal_notification(workout: dict, session_date: str) -> None:
    """Best-effort iMessage send of today's prescription. Silent on failure."""
    try:
        from notify import imessage_send
        stype = (workout.get("session_type") or "?").upper()
        lines = [f"== Session proposal — {session_date} {stype} =="]
        if workout.get("title"):
            lines.append(workout["title"])
        lines.append("")
        if workout.get("reasoning"):
            lines.append(workout["reasoning"])
            lines.append("")
        lines.append("Exercises:")
        for ex in workout.get("exercises", []):
            working = [s for s in ex.get("sets", []) if not s.get("is_warmup")]
            if not working:
                continue
            reps = working[0].get("reps")
            top = max((s.get("weight_kg") or 0) for s in working)
            load = f"{top:g}kg" if top else "BW"
            line = f"- {ex['exercise_name']}: {len(working)}×{reps} @ {load}"
            alts = [a for a in (ex.get("alternates") or []) if a]
            if alts:
                line += f"  (alt: {', '.join(alts)})"
            lines.append(line)
        imessage_send("\n".join(lines))
    except Exception:
        pass


def main(dry_run: bool = False, context_only: bool = False, find_templates: bool = False,
         as_workout: bool = False, confirm: bool = False, note: str = "",
         set_focus: tuple = (), force: bool = False, creator_recs: bool = False):

    # ── Template lookup helper ─────────────────────────────────────────────
    if find_templates:
        from hevy import print_template_ids_for_main_lifts
        print_template_ids_for_main_lifts()
        return

    # ── Session note (--note "...") ────────────────────────────────────────
    if note:
        from feedback import add_session_note
        add_session_note(note)
        return

    # ── Focus lift override (--set-focus push "Strict Military Press") ─────
    if set_focus:
        session_t, lift_name = set_focus
        from focus import set_focus_lift
        set_focus_lift(session_t, lift_name)
        return

    # ── 1. Sync Withings ───────────────────────────────────────────────────
    print("\n===== SYNC =====")
    try:
        from withings import sync_to_db
        sync_to_db(days=30)
    except Exception as e:
        msg = str(e).lower()
        if "refresh_token" in msg or "invalid_grant" in msg or "unauthorized" in msg:
            print("\n" + "!" * 60)
            print("⚠️  WITHINGS AUTH EXPIRED — bodyweight data will be stale")
            print("    Fix: python run.py --withings-auth")
            print("!" * 60 + "\n")
            log.error(f"Withings auth failure: {e}")
        else:
            log.warning(f"Withings sync failed (continuing without bodyweight): {e}")

    # ── 1b. Sync Hevy workouts → sets table ────────────────────────────────
    try:
        from hevy_sync import sync_to_db as hevy_sync
        hevy_sync(days=14)
    except Exception as e:
        log.warning(f"Hevy sync failed (continuing with existing data): {e}")

    # ── 1c. Diff most-recently completed workout vs its prescription ───────────
    import config as _cfg
    print("\n===== PREVIOUS =====")
    try:
        from feedback import run_feedback_for_date
        import sqlite3 as _sqlite3
        _fcon = _sqlite3.connect(_cfg.DB_PATH)
        _fcon.row_factory = _sqlite3.Row
        _last = _fcon.execute(
            "SELECT MAX(date) AS d FROM sets WHERE session_type != 'unknown'"
        ).fetchone()
        _fcon.close()
        _last_date = _last["d"] if _last else None
        if _last_date:
            run_feedback_for_date(_last_date)
    except Exception as e:
        log.warning(f"Feedback diff failed (non-critical): {e}")

    # ── 1d. Recurring activity check (e.g. wrestling Thursday) ─────────────
    from context import recurring_activity_role
    _role = recurring_activity_role()
    if _role["role"] == "on":
        act = _role["activity"]
        if force:
            log.warning(f"{act['name'].title()} day overridden (--force) — generating gym session anyway.")
        else:
            print(f"\n🤼  {act['name'].upper()} DAY  🤼")
            print(f"   {act['duration_minutes']} min of {act['name']} — no gym session today.")
            print(f"   Log it after with: python run.py --note \"{act['name']}: ...\"")
            print("   Run with --force to override.\n")
            return

    # ── 1e. Rest day check ─────────────────────────────────────────────────
    from context import consecutive_training_days
    consecutive = consecutive_training_days()
    if consecutive >= _cfg.MAX_CONSECUTIVE_DAYS:
        if force:
            log.warning(f"Rest day overridden (--force). {consecutive} consecutive days trained.")
        else:
            print("\n💤  REST DAY  💤")
            print(f"   {consecutive} consecutive days trained — recovery required.")
            print("   Run with --force to override.\n")
            return
    elif consecutive == _cfg.MAX_CONSECUTIVE_DAYS - 1:
        log.warning(f"Tomorrow is a rest day ({consecutive} consecutive days trained today).")

    # ── 2. Build context ───────────────────────────────────────────────────
    print("\n===== CURRENT =====")
    from context import build_context
    ctx = build_context()

    from context import recent_session_types
    import sqlite3 as _sqlite3
    stype      = ctx['suggested_session_type']
    days_since = ctx.get('days_since_last_session_of_type')
    days_str   = f"{days_since}d" if days_since is not None else "never"
    recent     = recent_session_types()
    history    = " / ".join(recent)
    from focus import phase_summary
    _last = _sqlite3.connect(_cfg.DB_PATH).execute(
        "SELECT date, session_type FROM sets WHERE session_type != 'unknown' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if _last:
        _days_ago = (date.today() - date.fromisoformat(_last[0])).days
        _last_label = "today" if _days_ago == 0 else f"{_days_ago}d ago"
        last_str = f"{_last[1].title()} ({_last_label})"
    else:
        last_str = "?"
    log.info(f"Session type today: {stype} (last {stype}: {days_str} ago)")
    log.info(f"Focus phases: {phase_summary()}")
    log.info(f"Last session: {last_str}  |  Recent: {history}")
    log.info(f"Sessions last 7 days: {ctx['sessions_last_7_days']}")
    _ra = ctx.get("recurring_activity") or {}
    if _ra.get("role") == "pre":
        _act = _ra["activity"]
        log.warning(f"Pre-{_act['name']}: {_act['name']} in {_ra['days_until']}d → avoid {'/'.join(_act['movement_load'])}, cap RPE 7")
    elif _ra.get("role") == "post":
        _act = _ra["activity"]
        log.warning(f"Post-{_act['name']}: {_act['name']} {_ra['days_since']}d ago → deload 5%, cap RPE 7, push/arms preferred")

    _dsa = ctx.get("days_since_any_session")
    if _dsa is not None and _dsa >= 4:
        if   _dsa <=  7: _re = "repeat last weight, RPE cap 8"
        elif _dsa <= 14: _re = "−5% deload, rebuild over 2 sessions"
        else:            _re = "−10% deload, rebuild over 3 sessions"
        log.warning(f"Re-entry: {_dsa} days since last session → {_re}")

    print("\n===== MONITORING =====")
    print("\n--- STATS ---")
    _ch = ctx.get("composition_history") or {}
    _weight_latest = (_ch.get("weight") or {}).get("latest") or {}
    if _weight_latest.get("date"):
        _wage = (date.today() - date.fromisoformat(_weight_latest["date"])).days
        if _wage >= 3:
            log.warning(f"⚠️  Latest weigh-in is {_wage}d old ({_weight_latest['date']}) — "
                        f"check Withings sync (python run.py --withings-auth)")
    def _line(label: str, key: str, unit: str = "kg") -> str:
        entry = _ch.get(key) or {}
        cur, prev = entry.get("latest"), entry.get("previous")
        if not cur: return ""
        line = f"{label}: {cur['value']}{unit}  (on {cur['date']})"
        if prev:
            delta = cur["value"] - prev["value"]
            line += f"  | prev {prev['value']}{unit} on {prev['date']}  ({delta:+.2f}{unit})"
        return line
    bw_line = _line("Bodyweight", "weight")
    if bw_line:
        log.info(f"{bw_line}  | 7d avg: {ctx.get('bodyweight_avg_7d')}kg")
    mm_line = _line("Muscle mass", "muscle")
    if mm_line:
        log.info(mm_line)
    bf_line = _line("Body fat",   "body_fat", unit="%")
    if bf_line:
        log.info(bf_line)
    _comp = ctx.get("body_composition_trends") or {}
    def _arrow(v):
        if v is None: return "?"
        if v >  0.05: return "↑"
        if v < -0.05: return "↓"
        return "→"
    def _fmt(v, unit="kg"):
        return f"{v:+.2f}{unit}/wk" if v is not None else "no data"
    log.info(f"  {_arrow(_comp.get('weight_kg_per_week'))} Weight: {_fmt(_comp.get('weight_kg_per_week'))}  (target {_cfg.TARGET_WEIGHT_KG}kg)")
    log.info(f"  {_arrow(_comp.get('muscle_kg_per_week'))} Muscle: {_fmt(_comp.get('muscle_kg_per_week'))}")
    bf = _comp.get('body_fat_pct_per_week')
    log.info(f"  {_arrow(-bf if bf is not None else None)} Body fat: {_fmt(bf, '%')}")

    print("\n--- FATIGUE ---")
    fat = ctx.get("fatigue") or {}
    fat_emoji = {"rest": "🔴", "caution": "⚠️", "ok": "🟢"}.get(fat.get("verdict"), "?")
    log.info(f"  {fat_emoji} Score: {fat.get('score')}/100  ({fat.get('verdict')})")
    _fc = fat.get("components", {})
    log.info(f"     last-set RPE avg: {_fc.get('last_set_rpe_avg')}  (+{_fc.get('rpe_points')})")
    log.info(f"     e1RM 14d slope:   {_fc.get('e1rm_slope_kg_per_week_14d')}kg/wk  (+{_fc.get('e1rm_slope_points')})")
    log.info(f"     consecutive days: {_fc.get('consecutive_training_days')}/{_cfg.MAX_CONSECUTIVE_DAYS}  (+{_fc.get('consecutive_points')})")
    log.info(f"     bodyweight 14d:   {_fc.get('bodyweight_slope_kg_per_week_14d')}kg/wk  (+{_fc.get('bodyweight_points')})")
    if _fc.get("notes_hits"):
        log.info(f"     note flags:       {', '.join(_fc['notes_hits'])}  (+{_fc.get('notes_points')})")

    print("\n--- TRACKED LIFTS ---")
    from context import e1rm_trends
    _SHORT = {
        "Incline Barbell Bench Press": "Incline Bench",
        "Strict Military Press":       "OHP",
        "Pull Up":                     "Pull Up",
        "Weighted Dip":                "Dip",
        "Front Squat":                 "Front Squat",
    }
    trends = e1rm_trends(weeks=4)
    for lift, t in trends.items():
        short = _SHORT.get(lift, lift)
        if t["current_e1rm"]:
            delta_str = f"  {t['delta_kg']:+.1f}kg" if t["delta_kg"] is not None else ""
            log.info(f"  {t['trend']} {short}: {t['current_e1rm']}kg e1RM{delta_str}")
        else:
            log.info(f"  {t['trend']} {short}: no data")

    if not creator_recs:
        ctx["creator_recommendations"] = []

    # Save context to log
    ctx_file = Path(_cfg.LOG_DIR) / f"{date.today().isoformat()}_context.json"
    ctx_file.write_text(json.dumps(ctx, indent=2, default=str))

    if context_only:
        print(json.dumps(ctx, indent=2, default=str))
        return

    # ── 3. Call Claude ─────────────────────────────────────────────────────
    print("\n===== PRESCRIPTION =====")
    from claude_api import get_workout, check_mode_change
    block_directive = check_mode_change(ctx)
    log.info("Calling Claude for workout prescription...")
    workout = get_workout(ctx, block_directive=block_directive)

    if not workout:
        log.error("Claude returned no workout. Aborting.")
        return

    if workout.get("rest_recommended"):
        if force:
            log.warning(f"Claude recommended rest but --force was set. Reason: {workout.get('reason')}")
            log.warning("Rest recommendation ignored. Re-run without --force to honour it.")
            return
        print("\n💤  REST DAY (Claude-recommended)  💤")
        print(f"   {workout.get('reason')}")
        print("   Run with --force to override.\n")
        return

    log.info(f"Workout: {workout.get('title')}")
    log.info(f"Reasoning: {workout.get('reasoning')}")

    # Save workout prescription to log
    workout_file = Path(_cfg.LOG_DIR) / f"{date.today().isoformat()}_workout.json"
    workout_file.write_text(json.dumps(workout, indent=2))

    _send_proposal_notification(workout, date.today().isoformat())

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

    # ── 3c. Confirm before posting ─────────────────────────────────────────
    if confirm:
        print("\n── Proposed workout ──────────────────────────────────────────")
        for ex in workout.get("exercises", []):
            sets = ex.get("sets", [])
            working = [s for s in sets if not s.get("is_warmup")]
            warmups = [s for s in sets if s.get("is_warmup")]
            w_str = f"{len(warmups)} warm-up + " if warmups else ""
            if working:
                top = max(s["weight_kg"] for s in working)
                reps = working[0]["reps"]
                print(f"  {ex['exercise_name']}: {w_str}{len(working)}×{reps} @ {top}kg")
            alts = [a for a in (ex.get("alternates") or []) if a]
            if alts:
                print(f"    alt: {', '.join(alts)}")
            notes = ex.get("notes", "")
            if notes:
                print(f"    → {notes}")
        print(f"\nReasoning: {workout.get('reasoning')}")
        print("──────────────────────────────────────────────────────────────")
        answer = input("Post to Hevy? [y/n]: ").strip().lower()
        if answer != "y":
            log.info("Aborted by user.")
            return

    # ── 3d. Template diff: capture edits made in Hevy app before session ───
    try:
        from hevy import _load_pinned_routine_id
        from feedback import diff_hevy_template_vs_prescription
        pinned_id = _load_pinned_routine_id()
        if pinned_id:
            diff_hevy_template_vs_prescription(pinned_id, date.today().isoformat())
    except Exception as e:
        log.warning(f"Template diff failed (non-critical): {e}")

    # ── 4. Post to Hevy ────────────────────────────────────────────────────
    print("\n===== HEVY =====")
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


def _arg_value(flag: str) -> str | None:
    """Return the argument after `flag`, or None if missing."""
    if flag not in sys.argv:
        return None
    idx = sys.argv.index(flag)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None


def _append_exclusion(name: str) -> None:
    """Append an exercise to the active user's profile.py EXCLUDED_EXERCISES list."""
    import config as _cfg
    import re as _re
    profile_path = Path(_cfg.USER_DIR) / "profile.py"
    text = profile_path.read_text()
    if _re.search(r'EXCLUDED_EXERCISES\s*[:=]', text):
        text = _re.sub(
            r'(EXCLUDED_EXERCISES[^=]*=\s*\[)(.*?)(\])',
            lambda m: m.group(1) + m.group(2) + f'    "{name}",\n' + m.group(3),
            text, flags=_re.DOTALL, count=1,
        )
    else:
        text += f'\nEXCLUDED_EXERCISES = ["{name}"]\n'
    profile_path.write_text(text)
    print(f"Added '{name}' to EXCLUDED_EXERCISES in {profile_path}")


if __name__ == "__main__":
    # --add-user runs the wizard; doesn't need an existing user.
    if "--add-user" in sys.argv:
        new_user = _arg_value("--add-user")
        if not new_user:
            print("Usage: python run.py --add-user <name>")
            sys.exit(1)
        from add_user import run_wizard
        run_wizard(new_user)
        sys.exit(0)

    user = _arg_value("--user")
    if not user:
        print("ERROR: --user <name> is required.\n"
              "  Run a session:  python run.py --user <name>\n"
              "  Create a user:  python run.py --add-user <name>")
        sys.exit(2)

    import config
    config.activate(user)
    _setup_logging(config.LOG_DIR)

    # ── Argless flags ─────────────────────────────────────────────────────
    dry_run        = "--dry-run"        in sys.argv
    context_only   = "--context-only"   in sys.argv
    find_templates = "--find-templates" in sys.argv
    as_workout     = "--as-workout"     in sys.argv
    confirm        = "--confirm"        in sys.argv
    force          = "--force"          in sys.argv
    creator_recs   = "--creator-recs"   in sys.argv

    note = _arg_value("--note") or ""

    set_focus: tuple = ()
    if "--set-focus" in sys.argv:
        idx = sys.argv.index("--set-focus")
        if idx + 2 < len(sys.argv):
            set_focus = (sys.argv[idx + 1], sys.argv[idx + 2])

    # ── One-shot subcommands ─────────────────────────────────────────────
    if "--withings-auth" in sys.argv:
        from withings import start_oauth
        start_oauth()
        sys.exit(0)

    if "--activity-list" in sys.argv:
        from activities import list_activities, weekday_name
        items = list_activities()
        if not items:
            print("No recurring activities configured.")
        else:
            print("Recurring activities:")
            for a in items:
                load = ",".join(a.get("movement_load", [])) or "—"
                print(f"  {a['name']:12s}  {weekday_name(a['weekday'])}  "
                      f"{a.get('duration_minutes', '?')}min {a.get('intensity', '?')}  "
                      f"load=[{load}]  pre={a.get('pre_buffer_days', 0)}d post={a.get('post_buffer_days', 0)}d")
        sys.exit(0)

    if "--activity-add" in sys.argv:
        from activities import add_activity, TEMPLATES, weekday_name
        idx = sys.argv.index("--activity-add")
        if idx + 2 >= len(sys.argv):
            print(f"Usage: --activity-add NAME WEEKDAY\n"
                  f"  Templates: {', '.join(TEMPLATES.keys())}\n"
                  f"  Weekday: mon|tue|...|sun or 0..6 (0=Mon)")
            sys.exit(1)
        try:
            entry = add_activity(sys.argv[idx + 1], sys.argv[idx + 2])
        except ValueError as e:
            print(e)
            sys.exit(1)
        print(f"Added activity: {entry['name']} on {weekday_name(entry['weekday'])} "
              f"({entry['duration_minutes']}min, {entry['intensity']}, "
              f"load={entry['movement_load']})")
        sys.exit(0)

    if "--activity-remove" in sys.argv:
        from activities import remove_activity
        name = _arg_value("--activity-remove")
        if not name:
            print("Usage: --activity-remove NAME")
            sys.exit(1)
        if remove_activity(name):
            print(f"Removed activity: {name}")
        else:
            print(f"No activity named {name!r} found.")
        sys.exit(0)

    if "--exclude" in sys.argv:
        name = _arg_value("--exclude")
        if name:
            _append_exclusion(name)
        sys.exit(0)

    main(dry_run=dry_run, context_only=context_only, find_templates=find_templates,
         as_workout=as_workout, confirm=confirm, note=note, set_focus=set_focus, force=force,
         creator_recs=creator_recs)
