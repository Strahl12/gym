"""Password + session-cookie auth for the web chat, stdlib only.

Each user's credential lives in users/<name>/auth.json:

    {"username": "john-gym", "password_hash": "scrypt$...", "setup_token": null}

make_chat_login.py provisions it and prints a one-time /setup link; the
set-password page uses autocomplete="new-password" so the phone's keychain
offers to generate and remember the password. Sessions are a signed cookie
"v1.<user>.<expires_unix>.<hmac>"; the signing secret is persisted under
users/ so restarts don't sign anyone out. The "-gym" username suffix keeps
keychain entries distinct from other apps served on the same hostname.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

SESSION_COOKIE = "gym_session"
SESSION_TTL_S = 365 * 24 * 3600
MIN_PASSWORD_LEN = 8

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

FAIL_LIMIT = 8
FAIL_WINDOW_S = 900
_FAILS: dict[str, list[float]] = {}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"scrypt${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_s, dk_s = stored.split("$")
        salt, want = _unb64(salt_s), _unb64(dk_s)
    except (ValueError, AttributeError, TypeError):
        return False
    got = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return hmac.compare_digest(got, want)


# ---------------------------------------------------------------- accounts


def auth_path(users_root: Path, user: str) -> Path:
    return users_root / user / "auth.json"


def load_auth(users_root: Path, user: str) -> dict | None:
    path = auth_path(users_root, user)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def save_auth(users_root: Path, user: str, record: dict) -> None:
    path = auth_path(users_root, user)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def _iter_auth(users_root: Path):
    for path in sorted(users_root.glob("*/auth.json")):
        user = path.parent.name
        if user.startswith("_"):
            continue
        record = load_auth(users_root, user)
        if record:
            yield user, record


def user_for_username(users_root: Path, username: str) -> tuple[str, dict] | None:
    for user, record in _iter_auth(users_root):
        if record.get("username") == username:
            return user, record
    return None


def user_for_setup_token(users_root: Path, token: str) -> tuple[str, dict] | None:
    if not token:
        return None
    for user, record in _iter_auth(users_root):
        stored = record.get("setup_token")
        if stored and hmac.compare_digest(stored, token):
            return user, record
    return None


def new_setup_token() -> str:
    return secrets.token_urlsafe(24)


# ---------------------------------------------------------------- sessions


def load_secret(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    path.chmod(0o600)
    return secret


def make_session(secret: bytes, user: str) -> str:
    expires = int(time.time()) + SESSION_TTL_S
    msg = f"{user}.{expires}"
    sig = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()
    return f"v1.{msg}.{sig}"


def read_session(secret: bytes, cookie: str) -> str | None:
    """Returns the user name, or None for anything invalid or expired."""
    try:
        version, user, expires, sig = cookie.split(".")
    except (ValueError, AttributeError):
        return None
    if version != "v1":
        return None
    msg = f"{user}.{expires}"
    want = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return None
    try:
        if time.time() > int(expires):
            return None
    except ValueError:
        return None
    return user


# ---------------------------------------------------------------- throttle


def throttled(username: str) -> bool:
    now = time.time()
    fails = [t for t in _FAILS.get(username, []) if now - t < FAIL_WINDOW_S]
    _FAILS[username] = fails
    return len(fails) >= FAIL_LIMIT


def record_failure(username: str) -> None:
    _FAILS.setdefault(username, []).append(time.time())
