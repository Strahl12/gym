"""
add_user.py — Interactive wizard to create a new user profile.

Invoked via:  python run.py --add-user <name>

Creates users/<name>/ from users/_template/, prompts for API keys and a
handful of training-goal values, optionally verifies the Hevy key, and
seeds an empty gym.db via migrate.

This is intentionally minimal: anything not asked here can be edited
later in users/<name>/profile.py or set via flags
(--exclude, --activity-add, --withings-auth, --find-templates).
"""
import re
import shutil
import sys
from pathlib import Path

_ROOT       = Path(__file__).parent
_USERS_ROOT = _ROOT / "users"
_TEMPLATE   = _USERS_ROOT / "_template"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


def _ask(prompt: str, default: str = "", required: bool = False) -> str:
    """Prompt for input; show default in brackets if provided."""
    hint = f" [{default}]" if default else ""
    while True:
        val = input(f"  {prompt}{hint}: ").strip()
        if not val and default:
            return default
        if val:
            return val
        if not required:
            return ""
        print("    (required)")


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    options = "/".join(f"[{c.upper()[0]}]{c[1:]}" if c == default else c for c in choices)
    while True:
        val = (input(f"  {prompt} ({options}): ").strip().lower() or default)
        if val in choices:
            return val
        # accept first-letter shortcut
        match = [c for c in choices if c.startswith(val)]
        if len(match) == 1:
            return match[0]
        print(f"    pick one of: {', '.join(choices)}")


def _ask_float(prompt: str, default: float | None = None, allow_blank: bool = False) -> float | None:
    hint = f" [{default}]" if default is not None else (" [skip]" if allow_blank else "")
    while True:
        raw = input(f"  {prompt}{hint}: ").strip()
        if not raw:
            return default if default is not None else None
        try:
            return float(raw)
        except ValueError:
            print("    enter a number, e.g. 87.5")


def _ask_int(prompt: str, default: int | None = None) -> int | None:
    hint = f" [{default}]" if default is not None else " [skip]"
    while True:
        raw = input(f"  {prompt}{hint}: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("    enter a whole number")


def _ask_yn(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} ({d}): ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def _verify_hevy_key(api_key: str) -> bool:
    """Hit the Hevy API to confirm the key works. Returns True on success."""
    import requests
    try:
        r = requests.get(
            "https://api.hevyapp.com/v1/workouts",
            headers={"api-key": api_key},
            params={"page": 1, "pageSize": 1},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _list_hevy_folders(api_key: str) -> list[dict]:
    """Return [{id, title}, ...] of routine folders for this Hevy account."""
    import requests
    try:
        r = requests.get(
            "https://api.hevyapp.com/v1/routine_folders",
            headers={"api-key": api_key},
            params={"page": 1, "pageSize": 50},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        return r.json().get("routine_folders", []) or []
    except Exception:
        return []


def _render_profile(values: dict) -> str:
    """Take the template profile.py and substitute the user-provided values."""
    text = (_TEMPLATE / "profile.py").read_text()

    def sub(pattern: str, replacement: str) -> None:
        nonlocal text
        text = re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)

    sub(r'^TRAINING_MODE = .*$',          f'TRAINING_MODE = "{values["training_mode"]}"')
    sub(r'^GOAL_MODE = .*$',              f'GOAL_MODE = "{values["goal_mode"]}"')
    sub(r'^TARGET_WEIGHT_KG = .*$',
        f'TARGET_WEIGHT_KG = {values["target_weight_kg"]!r}')
    sub(r'^WEIGHT_RATE_KG_PER_WEEK = .*$',
        f'WEIGHT_RATE_KG_PER_WEEK = {values["weight_rate"]!r}')
    sub(r'^HEVY_ROUTINE_FOLDER_ID = .*$',
        f'HEVY_ROUTINE_FOLDER_ID = {values["folder_id"]!r}')
    return text


def _render_secrets(values: dict) -> str:
    """Take the template secrets.env and substitute the user-provided values."""
    text = (_TEMPLATE / "secrets.env").read_text()
    text = re.sub(r'^HEVY_API_KEY=.*$',           f'HEVY_API_KEY={values["hevy_key"]}',         text, count=1, flags=re.MULTILINE)
    text = re.sub(r'^WITHINGS_CLIENT_ID=.*$',     f'WITHINGS_CLIENT_ID={values["withings_id"]}',     text, count=1, flags=re.MULTILINE)
    text = re.sub(r'^WITHINGS_CLIENT_SECRET=.*$', f'WITHINGS_CLIENT_SECRET={values["withings_secret"]}', text, count=1, flags=re.MULTILINE)
    text = re.sub(r'^WITHINGS_REFRESH_TOKEN=.*$', f'WITHINGS_REFRESH_TOKEN={values["withings_refresh"]}', text, count=1, flags=re.MULTILINE)
    return text


def run_wizard(name: str) -> None:
    if not _NAME_RE.match(name):
        print(f"Invalid name {name!r}. Use lowercase letters, digits, _ or - (start with a letter).")
        sys.exit(1)
    if name == "_template":
        print("'_template' is reserved.")
        sys.exit(1)

    user_dir = _USERS_ROOT / name
    if user_dir.exists():
        print(f"User '{name}' already exists at {user_dir}. Edit profile.py directly or delete the directory first.")
        sys.exit(1)

    if not _TEMPLATE.is_dir():
        print(f"Missing template directory at {_TEMPLATE}.")
        sys.exit(1)

    print(f"\nCreating user '{name}'...\n")

    # ── Hevy ──────────────────────────────────────────────────────────────
    print("Hevy")
    hevy_key = _ask("Hevy API key (https://hevy.com/settings?developer)", required=True)
    if not _verify_hevy_key(hevy_key):
        if not _ask_yn("Hevy API key check failed. Use it anyway?", default=False):
            print("Aborted.")
            sys.exit(1)

    folders = _list_hevy_folders(hevy_key)
    folder_id: int | None = None
    if folders:
        print("\n  Routine folders in your Hevy account:")
        for f in folders:
            print(f"    {f.get('id'):>10}  {f.get('title')}")
        raw = _ask("Folder ID to post routines into (the system pins one routine slot here)", required=True)
        try:
            folder_id = int(raw)
        except ValueError:
            print(f"    Not a number: {raw!r}")
            sys.exit(1)
    else:
        print("  Could not list folders — enter ID manually.")
        folder_id = _ask_int("Folder ID")

    # ── Withings (optional) ───────────────────────────────────────────────
    print("\nWithings (optional bodyweight tracking)")
    withings_id     = ""
    withings_secret = ""
    withings_refresh = ""
    if _ask_yn("Configure Withings?", default=False):
        withings_id     = _ask("Withings client ID")
        withings_secret = _ask("Withings client secret")
        withings_refresh = _ask("Withings refresh token (leave blank — you'll run --withings-auth)")

    # ── Training profile ──────────────────────────────────────────────────
    print("\nTraining")
    training_mode = _ask_choice("Training mode", ["strength", "hypertrophy", "mixed"], default="hypertrophy")
    goal_mode     = _ask_choice("Goal mode",     ["cut", "bulk", "maintain"],        default="maintain")
    target_weight = _ask_float("Target bodyweight in kg", default=None, allow_blank=True)
    weight_rate   = _ask_float("Target weight change kg/wk (e.g. 0.25 bulk, -0.25 cut, blank = none)",
                               default=None, allow_blank=True)

    values = {
        "hevy_key":         hevy_key,
        "withings_id":      withings_id,
        "withings_secret":  withings_secret,
        "withings_refresh": withings_refresh,
        "folder_id":        folder_id,
        "training_mode":    training_mode,
        "goal_mode":        goal_mode,
        "target_weight_kg": target_weight,
        "weight_rate":      weight_rate,
    }

    # ── Write files ───────────────────────────────────────────────────────
    user_dir.mkdir(parents=True)
    (user_dir / "profile.py").write_text(_render_profile(values))
    (user_dir / "secrets.env").write_text(_render_secrets(values))
    (user_dir / "logs").mkdir()

    # ── Seed the DB ───────────────────────────────────────────────────────
    print("\nSeeding gym.db...")
    import config
    config.activate(name)
    import migrate
    migrate.migrate()

    print(f"\nDone. User '{name}' created at {user_dir}/\n")
    print("Next steps:")
    print(f"  1. Populate main-lift template IDs:")
    print(f"       python run.py --user {name} --find-templates")
    print(f"     Then paste the IDs into {user_dir}/profile.py (MAIN_LIFTS).")
    if withings_id:
        print(f"  2. Run Withings OAuth:")
        print(f"       python run.py --user {name} --withings-auth")
    print(f"  3. Trigger today's session:")
    print(f"       python run.py --user {name}")
