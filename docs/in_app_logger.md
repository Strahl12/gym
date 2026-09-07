# In-app workout logger — Phase 1

Goal: let a user log a training session **inside the app**, writing the same
data Hevy would, so the coach/engine works without a Hevy account. Hevy stays
fully supported; this is additive and opt-in.

## Key architectural fact

Nothing in the engine reads Hevy. Everything downstream — `context`, `stats`,
e1RM, `feedback`, session forecasting — reads the **`sets` table**. Hevy is only
an *edge* that fills `sets` on the way in. So the logger's whole job is to write
rows into `sets` in the same shape `hevy_sync` writes, tagged `source='app'`.
Once that's done, an app-logged session is indistinguishable from a Hevy-synced
one. **Zero engine changes.**

## Scope (Phase 1)

In:
- Editable logging view of today's prescribed session (weight / reps / RPE per set).
- Add / remove sets within an exercise; tick sets done.
- "Finish & save" → writes to `sets` (`source='app'`).
- Draft autosave to `localStorage` so a refresh / signal drop mid-gym doesn't lose entries.
- Re-open an already-saved session to edit it (idempotent re-write).

Out (later phases, noted so they aren't forgotten):
- Adding an arbitrary exercise not in the prescription (swap already covers substitution).
- Full offline PWA / service worker, rest timers, supersets, plate math.

## Phase 2 — Hevy optional per user (done)

Per-user profile flag **`LOG_SOURCE`** (`"hevy"` default, or `"app"`), on
`config.LOG_SOURCE` via the profile overlay. Reset at the top of
`config.activate()` so an `"app"` user can't leak the flag onto the next user;
`config.uses_hevy()` gates every Hevy call-site:

- `run.py`: skip the Hevy sync (1b), the pinned-template diff (3d) and the
  post to Hevy (4) for app users — but still write the prescription JSON and
  `mark_posted_to_hevy` (marks delivered so it isn't regenerated). The feedback
  diff (1c) is **kept**: it reads the `sets` table, which the in-app logger
  fills, so prescribe → log → feedback → next prescription closes with no Hevy.
- `chat_server`: `_sync_recent` returns early; `_swap_response` edits/persists
  the prescription JSON without the Hevy re-post. `_user_uses_hevy(user)` reads
  the profile directly (no `activate`) and is surfaced to the Workout tab as
  `uses_hevy` so its copy adapts.
- `profile_editor` exposes `log_source` and now honours `GYM_USERS_ROOT`.

**Switch an existing user to app-only:** add `LOG_SOURCE = "app"` to their
`users/<name>/profile.py` (the Hevy key can be left blank). No migration.

Deferred to **Phase 2.5**: onboarding a brand-new user with *no* Hevy account
at all — `add_user.py` still requires a Hevy key and seeds the roster from the
Hevy library; an app-only signup needs the roster seeded from the global
`exercises.json` instead.

## Server

### `applog.py` (new)
- `log_session(db_path, payload) -> dict` — validates, resolves each exercise's
  muscle / bodyweight / main-lift flags from `exercise_lib` + the per-user
  `exercise_roster`, computes e1RM (reuses `hevy_sync._epley` +
  bodyweight lookup), and inserts into `sets`. Idempotent per
  `(date, session_type)`: deletes the prior `source='app'` session with the same
  `session_id` first, so saving again edits rather than duplicates.
  `session_id = f"app_{YYYYMMDD}_{session_type}"`.
- `logged_session(db_path, date=None) -> dict | None` — reads back an app-logged
  session grouped by exercise, for re-opening in the editor.

Reused from `hevy_sync`: `_epley`, `_bodyweight_lookup`, `_bw_on_or_before`,
`MUSCLE_TO_SESSION`, `REPS_ONLY_BODYWEIGHT_LIFTS`. Same column set as the Hevy
write path, so the two are byte-compatible in the table.

### Endpoints (both route families — `/u/<token>/…` and `/app/…`)
- `POST …/workout/log` → `_applog_response(user)` — body is the payload below.
- `GET  …/workout/logged[?date=]` → `_logged_response(user)` — for re-opening.

Payload:
```json
{
  "date": "2026-09-06",
  "session_type": "push",
  "workout_name": "Push A",
  "exercises": [
    {"exercise_name": "Bench Press",
     "sets": [{"weight_kg": 80, "reps": 5, "rpe": 8, "is_warmup": false}]}
  ]
}
```

## Front end (`templates/workout.html`)

- Banner gains a **"▸ Log this session"** toggle. Off by default → existing
  read-only view is unchanged.
- Logging mode re-renders each card with editable set rows
  `[✓] [weight] × [reps] @[rpe] [×]`, prefilled from the prescription (or from a
  saved draft / already-logged session if present). `+ set` adds a row.
- A sticky footer bar: **Finish & save** / **Cancel**.
- Every edit writes a `localStorage` draft keyed `log:<user>:<date>`; cleared on
  successful save.

## Testing

- `applog.py` write path is covered by `test_applog.py`, which runs it against a
  throwaway SQLite DB (real schema from `migrate.py`) and asserts the rows,
  e1RM, flags and idempotent re-write. Run: `python test_applog.py`.
- JS validated with `node --check` (see repo verification convention).
- Nothing here touches the daily generation path or Hevy; the branch can be
  exercised end-to-end against a test user before any rollout.
