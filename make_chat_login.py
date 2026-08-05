"""Provision (or reset) a user's web-chat login; prints a one-time setup link.

    python make_chat_login.py john
    python make_chat_login.py oliver --base https://bahamut.taila2531a.ts.net:8443

The link opens a set-password page; the user's phone offers to generate and
save a strong password to its keychain. The token is single-use — burned the
moment a password is saved. Re-running this resets a forgotten password (the
old one keeps working until the new link is used). Run on the server that
hosts users/ (the Pi); the chat server picks the change up without a restart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import chat_auth

USERS_ROOT = Path(__file__).parent / "users"
DEFAULT_BASE = "https://bahamut.taila2531a.ts.net:8443"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("user", help="user directory name under users/, e.g. john")
    ap.add_argument("--base", default=DEFAULT_BASE, help="public base URL")
    args = ap.parse_args()

    if args.user.startswith("_") or not (USERS_ROOT / args.user).is_dir():
        have = ", ".join(
            p.name for p in sorted(USERS_ROOT.iterdir())
            if p.is_dir() and not p.name.startswith("_")
        )
        sys.exit(f"unknown user {args.user!r} — have: {have or 'none'}")

    record = chat_auth.load_auth(USERS_ROOT, args.user) or {
        # "-gym" keeps the keychain entry distinct from other apps on this host.
        "username": f"{args.user}-gym",
        "password_hash": None,
    }
    record["setup_token"] = chat_auth.new_setup_token()
    chat_auth.save_auth(USERS_ROOT, args.user, record)

    print(f"Setup link for {args.user} (username: {record['username']}):")
    print(f"  {args.base.rstrip('/')}/setup/{record['setup_token']}")


if __name__ == "__main__":
    main()
