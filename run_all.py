"""
run_all.py — Run the morning workout flow for every user on this machine.

Discovers users from users/*/profile.py and invokes `run.py --user <name>`
as a subprocess per user. Subprocess isolation means one user's failure
(bad token, network error, malformed profile) doesn't take down the others.

Each user's full output goes to their own users/<name>/logs/<date>.log.
This script prints a one-line summary per user to stdout, suitable for a
single Pi cron line:

    30 7 * * * cd /home/pi/gym && .venv/bin/python run_all.py >> /home/pi/gym/run_all.log 2>&1

Usage:
    python run_all.py                       # run for all users
    python run_all.py --only john,alice     # run for a subset
    python run_all.py --dry-run             # pass --dry-run through to each
"""
import sys
import subprocess
from datetime import datetime
from pathlib import Path

USERS_ROOT = Path(__file__).parent / "users"
PYTHON     = sys.executable   # use the same interpreter that started us


def discover_users() -> list[str]:
    """Names of users with a profile.py (excluding _template)."""
    if not USERS_ROOT.is_dir():
        return []
    return sorted(
        p.parent.name
        for p in USERS_ROOT.glob("*/profile.py")
        if not p.parent.name.startswith("_")
    )


def run_for_user(name: str, extra_args: list[str]) -> tuple[bool, str]:
    """Run `python run.py --user <name>` and return (success, summary)."""
    cmd = [PYTHON, str(Path(__file__).parent / "run.py"), "--user", name, *extra_args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        # Pull last 'Workout:' line if present, fall back to "ok"
        title = next(
            (line for line in reversed(proc.stdout.splitlines())
             if "Workout:" in line or "REST DAY" in line),
            "ok",
        )
        return True, title.strip()
    err = (proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    return False, f"FAILED: {err}"


def main() -> int:
    extra: list[str] = [a for a in sys.argv[1:] if a not in ("--only",) and not a.startswith("--only=")]
    only: list[str] = []
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = [n.strip() for n in sys.argv[idx + 1].split(",") if n.strip()]
            extra = [a for a in extra if a != sys.argv[idx + 1]]

    users = discover_users()
    if only:
        users = [u for u in users if u in only]

    if not users:
        print(f"[run_all] No users found in {USERS_ROOT}.", file=sys.stderr)
        return 1

    stamp = datetime.now().isoformat(timespec="seconds")
    print(f"[run_all] {stamp} — running for: {', '.join(users)}")

    fail_count = 0
    for name in users:
        ok, summary = run_for_user(name, extra)
        status = "OK" if ok else "FAIL"
        print(f"[run_all] {name:12s} {status:4s}  {summary}")
        if not ok:
            fail_count += 1

    print(f"[run_all] done — {len(users) - fail_count}/{len(users)} succeeded")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
