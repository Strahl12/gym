"""
compare_prompts.py — Generate two workouts from the same context and compare.

  python compare_prompts.py

Generates one workout with the legacy hardcoded system prompt and one with
the new data-driven prompt, then prints a side-by-side comparison.
"""
import json
from context import build_context
from claude_api import get_workout, _build_system_prompt, LEGACY_SYSTEM_PROMPT
import config


def _prompt_stats(text: str) -> dict:
    return {
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "tokens_est": len(text) // 4,
    }


def _summarise(workout: dict) -> list[str]:
    lines = [f"  Title: {workout.get('title')}",
             f"  Session: {workout.get('session_type')}",
             f"  Reasoning: {workout.get('reasoning')}",
             "  Exercises:"]
    for ex in workout.get("exercises", []):
        sets = ex.get("sets", [])
        working = [s for s in sets if not s.get("is_warmup")]
        warmups = [s for s in sets if s.get("is_warmup")]
        w_str = f"{len(warmups)}wu + " if warmups else ""
        if working:
            top = max((s.get("weight_kg") or 0) for s in working)
            reps = working[0].get("reps", "?")
            weight_str = f"{top}kg × " if top else "BW × "
            lines.append(f"    {ex['exercise_name']}: {w_str}{len(working)}×{reps} @ {weight_str}{ex.get('rest_seconds','')}s rest")
        if ex.get("notes"):
            lines.append(f"      → {ex['notes']}")
    return lines


def main():
    print("Building context...")
    ctx = build_context()
    print(f"Session type: {ctx['suggested_session_type']}\n")

    # ── Prompt stats ──────────────────────────────────────────────────────
    legacy_text = (LEGACY_SYSTEM_PROMPT
                   .replace("{goal}", config.GOAL)
                   .replace("{mode}", config.TRAINING_MODE)
                   .replace("{mode_detail}", "Rep range 4–6, load 80–90% 1RM."))
    new_text = _build_system_prompt()

    ls = _prompt_stats(legacy_text)
    ns = _prompt_stats(new_text)
    print("── System prompt comparison ──────────────────────────────────────────")
    print(f"  Legacy:   {ls['lines']} lines  {ls['chars']} chars  ~{ls['tokens_est']} tokens")
    print(f"  New:      {ns['lines']} lines  {ns['chars']} chars  ~{ns['tokens_est']} tokens")
    reduction = (1 - ns['chars'] / ls['chars']) * 100
    print(f"  Reduction: {reduction:.0f}% smaller\n")

    # ── Generate both workouts ────────────────────────────────────────────
    print("Generating LEGACY workout...")
    legacy_w = get_workout(ctx, legacy=True)
    print("Generating NEW workout...")
    new_w = get_workout(ctx, legacy=False)

    print("\n── LEGACY prompt workout ──────────────────────────────────────────────")
    if legacy_w:
        print("\n".join(_summarise(legacy_w)))
    else:
        print("  FAILED")

    print("\n── NEW (data-driven) workout ──────────────────────────────────────────")
    if new_w:
        print("\n".join(_summarise(new_w)))
    else:
        print("  FAILED")

    # ── Structural diff ───────────────────────────────────────────────────
    if legacy_w and new_w:
        print("\n── Structural comparison ──────────────────────────────────────────────")
        legacy_names = [e["exercise_name"] for e in legacy_w.get("exercises", [])]
        new_names    = [e["exercise_name"] for e in new_w.get("exercises", [])]
        same = set(legacy_names) & set(new_names)
        only_legacy = set(legacy_names) - set(new_names)
        only_new    = set(new_names) - set(legacy_names)
        print(f"  Exercises in common ({len(same)}): {', '.join(sorted(same))}")
        if only_legacy:
            print(f"  Only in legacy ({len(only_legacy)}): {', '.join(sorted(only_legacy))}")
        if only_new:
            print(f"  Only in new ({len(only_new)}):    {', '.join(sorted(only_new))}")

        # Weight comparison for shared main lifts
        legacy_by_name = {e["exercise_name"]: e for e in legacy_w.get("exercises", [])}
        new_by_name    = {e["exercise_name"]: e for e in new_w.get("exercises", [])}
        main_lifts = [n for n in same if legacy_by_name[n].get("is_main_lift")]
        if main_lifts:
            print(f"\n  Main lift weight check:")
            for name in main_lifts:
                l_sets = [s for s in legacy_by_name[name]["sets"] if not s.get("is_warmup")]
                n_sets = [s for s in new_by_name[name]["sets"]    if not s.get("is_warmup")]
                l_top  = max((s.get("weight_kg") or 0) for s in l_sets) if l_sets else None
                n_top  = max((s.get("weight_kg") or 0) for s in n_sets) if n_sets else None
                match  = "✓" if l_top == n_top else "✗ MISMATCH"
                print(f"    {name}: legacy={l_top}kg  new={n_top}kg  {match}")


if __name__ == "__main__":
    main()
