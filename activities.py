"""
activities.py — recurring non-gym activities (wrestling, BJJ, running, etc.).

Stored in `recurring_activities.json` so they can be added/removed via CLI
without editing source. `context.py` reads from here to bias session selection
and inject Conditioning context into Claude's prompt.

Schema per entry:
    name, weekday (0=Mon..6=Sun), intensity, duration_minutes,
    movement_load (list), pre_buffer_days, post_buffer_days,
    safe_session_types (list)
"""
import json
from pathlib import Path

STORE = Path(__file__).parent / "recurring_activities.json"

# Sensible defaults per activity type. User provides name + weekday;
# everything else falls back to the template if not overridden.
TEMPLATES: dict[str, dict] = {
    "wrestling": {
        "intensity": "high", "duration_minutes": 120,
        "movement_load": ["pull", "legs", "core"],
        "pre_buffer_days": 1, "post_buffer_days": 1,
        "safe_session_types": ["push", "arms"],
    },
    "bjj": {
        "intensity": "high", "duration_minutes": 90,
        "movement_load": ["pull", "core"],
        "pre_buffer_days": 1, "post_buffer_days": 1,
        "safe_session_types": ["push", "arms", "legs"],
    },
    "boxing": {
        "intensity": "high", "duration_minutes": 75,
        "movement_load": ["core", "conditioning"],
        "pre_buffer_days": 0, "post_buffer_days": 1,
        "safe_session_types": ["push", "pull", "legs", "arms"],
    },
    "running": {
        "intensity": "moderate", "duration_minutes": 45,
        "movement_load": ["legs"],
        "pre_buffer_days": 0, "post_buffer_days": 1,
        "safe_session_types": ["push", "pull", "arms"],
    },
    "long_run": {
        "intensity": "high", "duration_minutes": 90,
        "movement_load": ["legs"],
        "pre_buffer_days": 1, "post_buffer_days": 1,
        "safe_session_types": ["push", "pull", "arms"],
    },
    "climbing": {
        "intensity": "high", "duration_minutes": 120,
        "movement_load": ["pull", "core"],
        "pre_buffer_days": 1, "post_buffer_days": 1,
        "safe_session_types": ["push", "legs"],
    },
    "football": {
        "intensity": "high", "duration_minutes": 90,
        "movement_load": ["legs", "core"],
        "pre_buffer_days": 1, "post_buffer_days": 1,
        "safe_session_types": ["push", "pull", "arms"],
    },
    "swimming": {
        "intensity": "moderate", "duration_minutes": 60,
        "movement_load": ["pull"],
        "pre_buffer_days": 0, "post_buffer_days": 1,
        "safe_session_types": ["push", "legs", "arms"],
    },
    "cycling": {
        "intensity": "moderate", "duration_minutes": 60,
        "movement_load": ["legs"],
        "pre_buffer_days": 0, "post_buffer_days": 1,
        "safe_session_types": ["push", "pull", "arms"],
    },
}

_WEEKDAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1, "tues": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "thurs": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_weekday(s: str) -> int:
    """Accept '3', 'thu', 'thursday'. Returns 0-6 (Mon-Sun)."""
    s = s.strip().lower()
    if s in _WEEKDAY_NAMES:
        return _WEEKDAY_NAMES[s]
    try:
        n = int(s)
        if 0 <= n <= 6:
            return n
    except ValueError:
        pass
    raise ValueError(f"Unrecognised weekday: {s!r}. Use 'mon'..'sun' or 0..6.")


def weekday_name(n: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][n]


def load_activities() -> list[dict]:
    if not STORE.exists():
        return []
    try:
        return json.loads(STORE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_activities(items: list[dict]) -> None:
    STORE.write_text(json.dumps(items, indent=2))


def add_activity(name: str, weekday: str, overrides: dict | None = None) -> dict:
    """
    Add a recurring activity. Pulls defaults from TEMPLATES[name] if present,
    overlaid with `overrides`. Replaces an existing entry with the same name.
    """
    wd = parse_weekday(weekday)
    template = TEMPLATES.get(name.lower(), {
        "intensity": "moderate", "duration_minutes": 60,
        "movement_load": [], "pre_buffer_days": 0, "post_buffer_days": 0,
        "safe_session_types": ["push", "pull", "legs", "arms"],
    })
    entry = {"name": name.lower(), "weekday": wd, **template}
    if overrides:
        entry.update(overrides)

    items = [a for a in load_activities() if a["name"] != entry["name"]]
    items.append(entry)
    save_activities(items)
    return entry


def remove_activity(name: str) -> bool:
    name = name.lower()
    items = load_activities()
    new_items = [a for a in items if a["name"] != name]
    if len(new_items) == len(items):
        return False
    save_activities(new_items)
    return True


def list_activities() -> list[dict]:
    return load_activities()
