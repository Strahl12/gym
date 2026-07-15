# gym_ai — AI-Powered Workout Prescriber

Pulls training data from Hevy + Withings (bodyweight), builds context,
calls Claude to prescribe today's session, and PUT-updates a pinned Hevy
routine — ready to log when you walk into the gym.

Supports multiple users on the same machine: each user has their own DB,
secrets, profile, and pinned routine.

## File structure

```
gym/
├── config.py              # infra constants (paths, training rules, templates)
├── context.py / hevy.py / claude_api.py / withings.py / ...   # use config.activate(name)
├── run.py                 # main orchestrator — REQUIRES --user <name>
├── run_all.py             # iterates every user dir; one Pi cron line covers all
├── add_user.py            # wizard launched via `python run.py --add-user <name>`
├── secrets.env            # SHARED ANTHROPIC_API_KEY (gitignored)
└── users/
    ├── _template/         # profile.py + secrets.env starter (tracked)
    └── <name>/            # per-user (gitignored)
        ├── profile.py             # goals, main lifts, focus lifts, exclusions
        ├── secrets.env            # HEVY_API_KEY, WITHINGS_*
        ├── gym.db                 # sets, prescriptions, body composition
        ├── app_state.json         # mode-change tracking
        ├── recurring_activities.json
        ├── hevy_routine_id.txt    # pinned routine slot
        ├── withings_token.json    # OAuth refresh cache
        └── logs/                  # daily <date>.log + context.json + workout.json
```

## Setup (macOS or Linux/Pi)

```bash
git clone <repo>
cd gym
python3 -m venv .env
.env/bin/pip install -r requirements.txt

# Shared key
cp secrets.env.example secrets.env
# edit secrets.env, set ANTHROPIC_API_KEY=sk-ant-...
```

## Add a user

```bash
.env/bin/python run.py --add-user <name>
```

The wizard prompts for the Hevy API key (verified live), Withings credentials
(optional), training mode, goal mode, target weight, and the Hevy routine
folder ID (picked from your account). It creates `users/<name>/` with
`profile.py`, `secrets.env`, an empty `gym.db`, and a `logs/` directory.

Then:

```bash
# 1. Find Hevy template IDs for the user's main lifts and paste them into profile.py
.env/bin/python run.py --user <name> --find-templates

# 2. (optional) Withings OAuth — one-time
.env/bin/python run.py --user <name> --withings-auth

# 3. Test without posting to Hevy
.env/bin/python run.py --user <name> --dry-run

# 4. Go live
.env/bin/python run.py --user <name>
```

## Daily usage

```bash
# One user
.env/bin/python run.py --user john

# All users (good for cron)
.env/bin/python run_all.py

# Subset
.env/bin/python run_all.py --only john,alice

# Pass-through flags
.env/bin/python run_all.py --dry-run
```

Other per-user flags:

```bash
--note "..."                       # log a session note
--set-focus push "Bench Press"     # override focus lift for a session type
--exclude "Calf Raise (Barbell)"   # append to user's profile.py EXCLUDED_EXERCISES
--activity-list / --activity-add wrestling thu / --activity-remove wrestling
--force                            # override rest-day check
--confirm                          # show prescription, prompt y/n before posting
--context-only                     # print today's context, no Claude call
--creator-recs                     # include creator recommendations
--find-templates                   # print Hevy template IDs for main lifts
```

## Raspberry Pi deployment

Tested on Raspberry Pi OS (Bookworm, Pi 4/5) with Python 3.11+.

```bash
# 1. System prep
sudo apt update
sudo apt install -y python3 python3-venv git

# 2. Clone + venv
git clone <repo> /home/pi/gym
cd /home/pi/gym
python3 -m venv .env
.env/bin/pip install -r requirements.txt

# 3. Shared secrets
cp secrets.env.example secrets.env
nano secrets.env             # paste ANTHROPIC_API_KEY

# 4. Add yourself
.env/bin/python run.py --add-user john
.env/bin/python run.py --user john --find-templates    # fill in profile.py
nano users/john/profile.py
.env/bin/python run.py --user john --withings-auth     # optional
.env/bin/python run.py --user john --dry-run           # smoke-test

# 5. Manual morning run
.env/bin/python run_all.py
```

### Cron (optional — fully automated)

```bash
crontab -e
# Add a single line that handles every user:
30 7 * * * cd /home/pi/gym && /home/pi/gym/.env/bin/python run_all.py >> /home/pi/gym/run_all.log 2>&1
```

Adding a new user later requires no cron edit: drop a `users/<newname>/`
(via the wizard) and `run_all.py` picks them up automatically the next morning.

### Delivery

There are no push notifications. The prescription lands directly in each
user's Hevy app as the updated routine — open Hevy, today's session is there.
Post-session reviews are logged to the DB and each user's daily log file.

### Web chat

`chat_server.py` serves a minimal per-user chat page where each user can talk
to the AI coach about their training. Replies are grounded in the same context
the morning engine uses (`build_context`), plus today's prescription.

Each user has a secret link `/u/<CHAT_TOKEN>` (token in their
`users/<name>/secrets.env`; the add-user wizard generates one). Chat history
is stored in their `gym.db`. Messages are rate-limited (30/hour/user) to cap
API spend.

The server listens on `127.0.0.1:8090` and is published with Tailscale Funnel
on its own port, leaving any tailnet-only serve on 443 untouched:

```
tailscale funnel --bg --https=8443 http://127.0.0.1:8090
```

Chat URLs are then `https://<machine>.<tailnet>.ts.net:8443/u/<token>`.

Cron keeps it alive (no root needed — `flock` no-ops while it's running):

```
@reboot     sleep 20 && flock -n /tmp/gym_chat.lock -c '<venv-python> chat_server.py >> chat_server.log 2>&1'
*/5 * * * * flock -n /tmp/gym_chat.lock -c '<venv-python> chat_server.py >> chat_server.log 2>&1'
```

Restart after adding a user (new tokens load at startup):
`pkill -f chat_server.py` — cron relaunches it within 5 minutes.
