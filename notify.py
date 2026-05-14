"""
notify.py — macOS iMessage notification helper.

Best-effort: failures are non-fatal and never interrupt the workout flow.
Set IMESSAGE_RECIPIENT (phone number or Apple ID) in secrets.env to enable.
"""
import os
import sys
import time
import hashlib
import subprocess
from pathlib import Path


_APPLESCRIPT = '''
on run argv
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy (item 1 of argv) of targetService
        send (item 2 of argv) to targetBuddy
    end tell
end run
'''

_DEDUPE_PATH = Path.home() / "gym_ai" / ".imessage_recent.log"
_DEDUPE_WINDOW_SEC = 30 * 60  # 30 minutes


def _recently_sent(body: str) -> bool:
    """True if an identical body was sent within the dedupe window."""
    if not _DEDUPE_PATH.exists():
        return False
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = time.time()
    for line in _DEDUPE_PATH.read_text().splitlines():
        ts, sha = (line.split(" ", 1) + [""])[:2]
        try:
            if sha == digest and (now - float(ts)) < _DEDUPE_WINDOW_SEC:
                return True
        except ValueError:
            continue
    return False


def _record_sent(body: str) -> None:
    """Append a hash entry; prune entries older than the dedupe window."""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = time.time()
    keep = []
    if _DEDUPE_PATH.exists():
        for line in _DEDUPE_PATH.read_text().splitlines():
            ts, _, sha = line.partition(" ")
            try:
                if (now - float(ts)) < _DEDUPE_WINDOW_SEC:
                    keep.append(line)
            except ValueError:
                continue
    keep.append(f"{now:.0f} {digest}")
    _DEDUPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DEDUPE_PATH.write_text("\n".join(keep) + "\n")


def imessage_send(body: str, recipient: str | None = None) -> bool:
    """Send `body` via iMessage. Returns True on success, False otherwise.

    Recipient resolution order: argument > IMESSAGE_RECIPIENT env var.
    No-ops (returns False) if no recipient is configured or if the same
    body was sent within the last 30 minutes (dedupe guard).
    """
    recipient = recipient or os.environ.get("IMESSAGE_RECIPIENT", "").strip()
    if not recipient:
        return False
    if _recently_sent(body):
        print("[notify] iMessage skipped — identical body sent recently", file=sys.stderr)
        return False
    try:
        subprocess.run(
            ["osascript", "-e", _APPLESCRIPT, recipient, body],
            check=True, capture_output=True, timeout=15,
        )
        _record_sent(body)
        return True
    except Exception as e:
        print(f"[notify] iMessage send failed: {e}", file=sys.stderr)
        return False
