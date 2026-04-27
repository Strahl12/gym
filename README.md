# gym_ai — AI-Powered Workout Prescriber

Pulls training data from Strong (CSV/Numbers) + Withings (bodyweight),
builds context, calls Claude to prescribe today's session, and POSTs it
to Hevy — ready to log when you walk into the gym.

## Setup

```bash
pip install requests numbers-parser
```

Set env vars:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export HEVY_API_KEY=sk_live_...
export WITHINGS_CLIENT_ID=...
export WITHINGS_CLIENT_SECRET=...
export WITHINGS_REFRESH_TOKEN=...
```

## First-time setup

### 1. Seed the DB from Strong export
```bash
python ingest_strong.py
```

### 2. Find your Hevy exercise template IDs
```bash
python run.py --find-templates
```
Paste the printed IDs into `config.py` → `MAIN_LIFTS[...]["hevy_template_id"]`.

### 3. Set up Withings OAuth (one-time)
See: https://developer.withings.com/oauth2/
Get a refresh token and set WITHINGS_REFRESH_TOKEN.

### 4. Test without posting to Hevy
```bash
python run.py --dry-run
```

### 5. Go live
```bash
python run.py
```

## Cron setup (runs at 07:30 every day)
```bash
crontab -e
# add:
30 7 * * * cd /path/to/gym_ai && /usr/bin/python3 run.py >> logs/cron.log 2>&1
```

## File structure
```
gym_ai/
├── config.py          # goals, lift config, API keys
├── context.py         # derives training metrics from DB
├── claude_api.py      # calls Claude, returns workout JSON
├── hevy.py            # posts workout to Hevy API
├── withings.py        # syncs bodyweight from Withings
├── ingest_strong.py   # one-time Strong CSV/Numbers import
├── run.py             # main orchestrator (cron entry point)
├── gym.db             # SQLite database
└── logs/              # daily context + workout JSON logs
```

## Updating with new Strong data
Re-run `ingest_strong.py` with a fresh export — it drops and rebuilds the
`sets` table. Withings data in `bodyweight` table is preserved.
