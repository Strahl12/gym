# TODO

- [ ] Create remote repo (GitHub) and push existing commits.
- [ ] No hard-coded keys etc. Audit the codebase for hard-coded secrets/IDs/paths before going public; move anything sensitive to `secrets.env` (already gitignored).
- [ ] Set up on Raspberry Pi and have it run automatically every morning (cron `python run.py` ~07:30; ensure Python deps, `secrets.env`, and Withings token file are present on the Pi).

## From field notes (2026-11-08)

- [ ] Learn from weight overrides: detect consistent prescribed-vs-logged weight
      deviation per exercise (same direction, 2-3 sessions) and surface it to the
      coach context so prescriptions adapt. (No such mechanism exists yet —
      verified; block_overrides is unrelated.)
- [ ] Learn rest-time preference: persist the per-exercise added/removed rest
      (now displayed in the timer sub-line) with saved sessions; after
      consistent adjustment in one direction, nudge rest_seconds conservatively.
- [ ] Swipe-left to delete exercises in the logger (replacing/augmenting the ✕).
- [ ] Hevy-sync vs in-app double logging audit: in-app saves are idempotent per
      (date, session_type), but a session logged in-app AND arriving via Hevy
      sync would double-count. Needs a dedupe rule in hevy_sync.
- [ ] "Rest timer should be on break" note — unclear, ask John what he meant.
- [ ] Generative animation model for unmatched exercises — parked: at 180px the
      vendor art can't be cleanly learned from on a Pi, and matching (a
      classification problem) already covers 426/433. Revisit only if a proper
      open licensed animation corpus appears.
- [ ] App Store Connect listing still reads "Gym Coach" (portal change, John).
