"""
announce.py — post a coach announcement into users' chat histories.

Usage:
    python announce.py "Programming update: rep ranges now adjust ..."
    python announce.py --user oliver "Your Withings link is ready ..."

The message is stored as an assistant turn in each user's chat_messages, so
it appears in their chat the next time they open their coach link — same
two-way channel as everything else, no separate notification path. Because
it's in the history, the coach also sees it and can answer follow-ups.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

USERS_ROOT = Path(__file__).parent / "users"


def announce(message: str, only: set[str] | None = None) -> None:
    posted = 0
    for db in sorted(USERS_ROOT.glob("*/gym.db")):
        user = db.parent.name
        if user.startswith("_"):
            continue
        if only is not None and user not in only:
            continue
        con = sqlite3.connect(db)
        try:
            # Same schema as migrate.py / chat_server.py
            con.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      TEXT NOT NULL,
                    role    TEXT NOT NULL,
                    content TEXT NOT NULL
                )
            """)
            con.execute(
                "INSERT INTO chat_messages (ts, role, content) VALUES (?, 'assistant', ?)",
                (datetime.now().isoformat(timespec="seconds"), message),
            )
            con.commit()
        finally:
            con.close()
        print(f"[announce] {user}: posted")
        posted += 1
    if only is not None:
        missing = only - {p.parent.name for p in USERS_ROOT.glob("*/gym.db")}
        for m in sorted(missing):
            print(f"[announce] WARNING: no such user {m!r}")
    if posted == 0:
        print("[announce] nothing posted")


if __name__ == "__main__":
    args = sys.argv[1:]
    only: set[str] | None = None
    if args and args[0] == "--user":
        if len(args) < 2:
            print("Usage: python announce.py [--user name1,name2] \"message\"")
            sys.exit(1)
        only = {u.strip() for u in args[1].split(",") if u.strip()}
        args = args[2:]
    if len(args) != 1 or not args[0].strip():
        print("Usage: python announce.py [--user name1,name2] \"message\"")
        sys.exit(1)
    announce(args[0].strip(), only)
