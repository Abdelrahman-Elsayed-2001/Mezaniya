"""Password hashing and the manual-reset convention.

A stored value that starts and ends with '#' (e.g. "#1234#") was set by hand in the
database to recover an account: it is compared as plain text against what the user
types — including the '#' marks — and the user must change it right after logging in.
Anything else is a werkzeug hash ("scrypt:..."), which never starts with '#'.
"""
from __future__ import annotations

import hmac
import re

from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD = 6
MAX_PASSWORD = 128
USERNAME_RE = re.compile(r"^[\w.\-]{3,32}$")   # \w covers Arabic letters too

# verified against when the username doesn't exist, so both paths cost the same
_DUMMY_HASH = generate_password_hash("timing-equaliser")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def is_manual(stored: str) -> bool:
    return len(stored) >= 3 and stored.startswith("#") and stored.endswith("#")


def verify(stored: str | None, given: str) -> bool:
    if stored is None:
        check_password_hash(_DUMMY_HASH, given)
        return False
    if is_manual(stored):
        return hmac.compare_digest(stored.encode(), given.encode())
    try:
        return check_password_hash(stored, given)
    except (ValueError, TypeError):   # malformed value edited by hand
        return False


def username_error(username: str) -> tuple[str, dict] | None:
    """(translation key, params) when the username is not acceptable."""
    if not USERNAME_RE.match(username):
        return "auth.err.username_invalid", {}
    return None


def password_error(password: str) -> tuple[str, dict] | None:
    if len(password) < MIN_PASSWORD:
        return "auth.err.password_short", {"min": MIN_PASSWORD}
    if len(password) > MAX_PASSWORD:
        return "auth.err.password_long", {"max": MAX_PASSWORD}
    return None
