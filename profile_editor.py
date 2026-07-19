"""
profile_editor.py — Structured, validated edits to users/<name>/profile.py.

Used by the chat server so the coach can update training goals (main lifts,
focus lifts, training/goal mode, target weight) from conversation. All
changes go through typed operations that are validated against the user's
Hevy exercise library before anything is written; the MAIN_LIFTS and
DEFAULT_FOCUS_LIFTS blocks are regenerated from validated data, scalars are
replaced in place. The previous profile.py is kept at profile.py.bak.

Standalone on purpose: takes the user name and derives paths itself, so it
never depends on (or mutates) config's process-global active user.
"""
import difflib
import re
import sqlite3
from pathlib import Path

_USERS_ROOT = Path(__file__).parent / "users"

SESSION_TYPES  = ("push", "pull", "legs", "arms")
TRAINING_MODES = ("strength", "hypertrophy", "mixed")
GOAL_MODES     = ("cut", "bulk", "maintain")

_LIFT_FIELDS = ("session_type", "target_sets", "rep_range", "progression_kg")


class ProfileEditError(Exception):
    """Validation failure — message is safe to show to the model/user."""


def _profile_path(user: str) -> Path:
    p = _USERS_ROOT / user / "profile.py"
    if not p.is_file():
        raise ProfileEditError(f"no profile.py for user {user!r}")
    return p


def _exec_profile(text: str) -> dict:
    ns: dict = {}
    exec(compile(text, "profile.py", "exec"), ns)
    return ns


def read_profile(user: str) -> dict:
    """Current editable values, straight from the user's profile.py."""
    ns = _exec_profile(_profile_path(user).read_text())
    return {
        "needs_onboarding":        ns.get("NEEDS_ONBOARDING", False),
        "training_mode":           ns.get("TRAINING_MODE"),
        "goal_mode":               ns.get("GOAL_MODE"),
        "target_weight_kg":        ns.get("TARGET_WEIGHT_KG"),
        "weight_rate_kg_per_week": ns.get("WEIGHT_RATE_KG_PER_WEEK"),
        "main_lifts":              ns.get("MAIN_LIFTS", {}),
        "default_focus_lifts":     ns.get("DEFAULT_FOCUS_LIFTS", {}),
    }


# ── Hevy exercise library lookup ───────────────────────────────────────────

def search_exercises(user: str, query: str, limit: int = 8) -> list[dict]:
    """Search the user's Hevy exercise library by title (substring + fuzzy)."""
    con = sqlite3.connect(_USERS_ROOT / user / "gym.db")
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT hevy_id, title, primary_muscle, equipment FROM hevy_exercise_library"
        ).fetchall()
    finally:
        con.close()
    q = query.strip().lower()
    scored = []
    for r in rows:
        t = r["title"].lower()
        if q == t:
            score = 3.0
        elif q in t:
            score = 2.0 + difflib.SequenceMatcher(None, q, t).ratio()
        else:
            score = difflib.SequenceMatcher(None, q, t).ratio()
        scored.append((score, r))
    scored.sort(key=lambda s: -s[0])
    return [
        {"title": r["title"], "hevy_id": r["hevy_id"],
         "muscle": r["primary_muscle"], "equipment": r["equipment"]}
        for score, r in scored[:limit] if score >= 0.45
    ]


def _resolve_exercise(user: str, title: str) -> dict:
    """Exact (case-insensitive) library match, or raise with close matches."""
    matches = search_exercises(user, title)
    for m in matches:
        if m["title"].lower() == title.strip().lower():
            return m
    close = ", ".join(m["title"] for m in matches[:5]) or "none"
    raise ProfileEditError(
        f"no exact Hevy exercise titled {title!r} — close matches: {close}. "
        f"Use search_exercises and confirm the exact title with the athlete."
    )


# ── Operation validation ───────────────────────────────────────────────────

def _validate_lift_fields(fields: dict) -> dict:
    out = {}
    if "session_type" in fields:
        st = fields["session_type"]
        if st not in SESSION_TYPES:
            raise ProfileEditError(f"session_type must be one of {SESSION_TYPES}, got {st!r}")
        out["session_type"] = st
    if "target_sets" in fields:
        ts = fields["target_sets"]
        if not isinstance(ts, int) or not 1 <= ts <= 10:
            raise ProfileEditError(f"target_sets must be an integer 1-10, got {ts!r}")
        out["target_sets"] = ts
    if "rep_range" in fields:
        rr = fields["rep_range"]
        if (not isinstance(rr, (list, tuple)) or len(rr) != 2
                or not all(isinstance(x, int) for x in rr)
                or not 1 <= rr[0] <= rr[1] <= 30):
            raise ProfileEditError(f"rep_range must be [low, high] ints with 1 <= low <= high <= 30, got {rr!r}")
        out["rep_range"] = (rr[0], rr[1])
    if "progression_kg" in fields:
        pk = fields["progression_kg"]
        if not isinstance(pk, (int, float)) or not 0.25 <= pk <= 20:
            raise ProfileEditError(f"progression_kg must be a number 0.25-20, got {pk!r}")
        out["progression_kg"] = float(pk)
    if "is_bodyweight" in fields:
        out["is_bodyweight"] = bool(fields["is_bodyweight"])
    return out


def _apply_set_main_lift(user: str, profile: dict, op: dict) -> str:
    name = (op.get("name") or "").strip()
    if not name:
        raise ProfileEditError("set_main_lift needs a 'name'")
    lifts = profile["main_lifts"]
    fields = _validate_lift_fields(op)

    if name in lifts:
        entry = dict(lifts[name])
        if "hevy_exercise_title" in op:
            ex = _resolve_exercise(user, op["hevy_exercise_title"])
            entry["hevy_template_id"] = ex["hevy_id"]
            entry["hevy_name"]        = ex["title"]
        entry.update(fields)
        lifts[name] = entry
        changed = sorted(set(fields) | ({"hevy_name"} if "hevy_exercise_title" in op else set()))
        return f"updated main lift {name!r} ({', '.join(changed)})"

    # New lift: everything required, plus a resolvable Hevy exercise
    missing = [f for f in _LIFT_FIELDS if f not in fields]
    if not op.get("hevy_exercise_title"):
        missing.append("hevy_exercise_title")
    if missing:
        raise ProfileEditError(
            f"{name!r} is a new main lift — missing required fields: {', '.join(missing)}. "
            f"Ask the athlete for these before applying."
        )
    ex = _resolve_exercise(user, op["hevy_exercise_title"])
    entry = {
        "hevy_template_id": ex["hevy_id"],
        "hevy_name":        ex["title"],
        **{f: fields[f] for f in _LIFT_FIELDS},
    }
    if fields.get("is_bodyweight"):
        entry["is_bodyweight"] = True
    lifts[name] = entry
    return f"added main lift {name!r} → {ex['title']} ({entry['session_type']})"


def _apply_remove_main_lift(user: str, profile: dict, op: dict) -> str:
    name = (op.get("name") or "").strip()
    lifts = profile["main_lifts"]
    if name not in lifts:
        raise ProfileEditError(f"no main lift named {name!r} — current: {', '.join(lifts) or 'none'}")
    if len(lifts) == 1:
        raise ProfileEditError("cannot remove the last main lift")
    used_by = [st for st, fl in profile["default_focus_lifts"].items() if fl == name]
    if used_by:
        raise ProfileEditError(
            f"{name!r} is the default focus lift for {', '.join(used_by)} — "
            f"set a new focus lift for that session type first (or in the same request)."
        )
    del lifts[name]
    return f"removed main lift {name!r}"


def _apply_set_focus_lift(user: str, profile: dict, op: dict) -> str:
    st   = op.get("session_type")
    name = (op.get("name") or "").strip()
    if st not in SESSION_TYPES:
        raise ProfileEditError(f"session_type must be one of {SESSION_TYPES}, got {st!r}")
    if not name:
        raise ProfileEditError("set_focus_lift needs a 'name'")
    # Must be a main lift or a real library exercise (focus lifts can be
    # accessories, e.g. Close Grip Bench Press on arms day).
    if name not in profile["main_lifts"]:
        _resolve_exercise(user, name)
    profile["default_focus_lifts"][st] = name
    return f"focus lift for {st}: {name}"


def _apply_scalar(profile: dict, op: dict) -> str:
    kind, val = op["op"], op.get("value")
    if kind == "set_training_mode":
        if val not in TRAINING_MODES:
            raise ProfileEditError(f"training_mode must be one of {TRAINING_MODES}, got {val!r}")
        profile["training_mode"] = val
        return f"training mode: {val}"
    if kind == "set_goal_mode":
        if val not in GOAL_MODES:
            raise ProfileEditError(f"goal_mode must be one of {GOAL_MODES}, got {val!r}")
        profile["goal_mode"] = val
        return f"goal mode: {val}"
    if kind == "set_target_weight_kg":
        if val is not None and (not isinstance(val, (int, float)) or not 30 <= val <= 250):
            raise ProfileEditError(f"target_weight_kg must be 30-250 or null, got {val!r}")
        profile["target_weight_kg"] = float(val) if val is not None else None
        return f"target weight: {val}kg" if val is not None else "target weight: cleared"
    if kind == "set_weight_rate_kg_per_week":
        if val is not None and (not isinstance(val, (int, float)) or not -1.5 <= val <= 1.5):
            raise ProfileEditError(f"weight_rate_kg_per_week must be -1.5 to 1.5 or null, got {val!r}")
        profile["weight_rate_kg_per_week"] = float(val) if val is not None else None
        return f"weight rate: {val:+.2f}kg/wk" if val is not None else "weight rate: cleared"
    if kind == "complete_onboarding":
        profile["needs_onboarding"] = False
        return "onboarding complete"
    raise ProfileEditError(f"unknown op {kind!r}")


# ── Rendering + write ──────────────────────────────────────────────────────

def _render_main_lifts(lifts: dict) -> str:
    out = ["MAIN_LIFTS = {"]
    for name, cfg in lifts.items():
        out.append(f"    {name!r}: {{")
        for key in ("hevy_template_id", "hevy_name", "session_type",
                    "target_sets", "rep_range", "progression_kg", "is_bodyweight"):
            if key in cfg:
                out.append(f"        {key!r}: {cfg[key]!r},")
        out.append("    },")
    out.append("}")
    return "\n".join(out)


def _render_focus_lifts(focus: dict) -> str:
    out = ["DEFAULT_FOCUS_LIFTS = {"]
    for st, name in focus.items():
        out.append(f"    {st!r}: {name!r},")
    out.append("}")
    return "\n".join(out)


def _sub_block(text: str, var: str, rendered: str) -> str:
    new, n = re.subn(rf"^{var} = \{{.*?^\}}", rendered, text, count=1,
                     flags=re.MULTILINE | re.DOTALL)
    if n != 1:
        raise ProfileEditError(f"could not locate the {var} block in profile.py")
    return new


def _sub_line(text: str, var: str, value) -> str:
    new, n = re.subn(rf"^{var} = .*$", f"{var} = {value!r}", text, count=1,
                     flags=re.MULTILINE)
    if n != 1:
        raise ProfileEditError(f"could not locate {var} in profile.py")
    return new


def apply_operations(user: str, operations: list[dict]) -> list[str]:
    """Validate and apply a batch of operations. All-or-nothing: any invalid
    op aborts the whole batch before the file is touched. Returns summaries."""
    if not operations:
        raise ProfileEditError("no operations given")

    path    = _profile_path(user)
    text    = path.read_text()
    profile = read_profile(user)
    profile["main_lifts"]         = {k: dict(v) for k, v in profile["main_lifts"].items()}
    profile["default_focus_lifts"] = dict(profile["default_focus_lifts"])

    summaries = []
    for op in operations:
        kind = op.get("op")
        if kind == "set_main_lift":
            summaries.append(_apply_set_main_lift(user, profile, op))
        elif kind == "remove_main_lift":
            summaries.append(_apply_remove_main_lift(user, profile, op))
        elif kind == "set_focus_lift":
            summaries.append(_apply_set_focus_lift(user, profile, op))
        elif kind in ("set_training_mode", "set_goal_mode",
                      "set_target_weight_kg", "set_weight_rate_kg_per_week",
                      "complete_onboarding"):
            summaries.append(_apply_scalar(profile, op))
        else:
            raise ProfileEditError(f"unknown op {op.get('op')!r}")

    text = _sub_block(text, "MAIN_LIFTS", _render_main_lifts(profile["main_lifts"]))
    text = _sub_block(text, "DEFAULT_FOCUS_LIFTS", _render_focus_lifts(profile["default_focus_lifts"]))
    # Older profiles predate the onboarding flag — only sub when the line exists
    if re.search(r"^NEEDS_ONBOARDING = ", text, flags=re.MULTILINE):
        text = _sub_line(text, "NEEDS_ONBOARDING", profile["needs_onboarding"])
    text = _sub_line(text, "TRAINING_MODE", profile["training_mode"])
    text = _sub_line(text, "GOAL_MODE", profile["goal_mode"])
    text = _sub_line(text, "TARGET_WEIGHT_KG", profile["target_weight_kg"])
    text = _sub_line(text, "WEIGHT_RATE_KG_PER_WEEK", profile["weight_rate_kg_per_week"])

    # Prove the regenerated file is valid Python with the values we intended
    check = _exec_profile(text)
    assert check["MAIN_LIFTS"] == profile["main_lifts"]

    path.with_suffix(".py.bak").write_text(path.read_text())
    tmp = path.with_suffix(".py.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return summaries
