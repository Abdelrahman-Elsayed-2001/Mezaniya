"""Who is logged in, CSRF guard, login_required and the forced password change."""
from __future__ import annotations

import hashlib
import hmac
from functools import wraps

from flask import current_app, g, jsonify, redirect, request, session, url_for

from app.db import get_db
from app.db.repo import NotFound, users
from app.i18n import current_lang, t

from .passwords import is_manual

CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "XMLHttpRequest"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# reachable while the user still has to replace a manually set password
MUST_CHANGE_ALLOWED = frozenset({"auth.change_password_page", "auth.change_password", "auth.logout",
                                 "auth.me", "static", "healthz", "i18n.set_language"})


def json_error(status: int, code: str, message_key: str, params: dict | None = None, **extra):
    """JSON error whose message is the translation of `message_key` in the request's language."""
    return jsonify({"error": code, "message": t(message_key, current_lang(), **(params or {})), **extra}), status


def wants_json() -> bool:
    return request.path.startswith(("/api/", "/auth/")) or request.is_json


def fingerprint(user: dict) -> str:
    """Ties a session to the current password and session_version: changing either
    (new password, or "log out everywhere" from the server manager) ends every session.
    Keyed with SECRET_KEY so the cookie reveals nothing about the stored value."""
    key = current_app.secret_key.encode() if isinstance(current_app.secret_key, str) else current_app.secret_key
    material = f"{user['password_hash']}|{user['session_version']}"
    return hmac.new(key, material.encode(), hashlib.sha256).hexdigest()[:32]


def start_session(user: dict) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["fp"] = fingerprint(user)


def current_user() -> dict | None:
    return g.get("user")


def must_change_password() -> bool:
    user = current_user()
    return bool(user and is_manual(user["password_hash"]))


def _load_user() -> None:
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    try:
        user = users.get_user(get_db(), uid)
    except NotFound:
        session.clear()
        return
    if not hmac.compare_digest(session.get("fp", ""), fingerprint(user)):
        session.clear()          # password changed / reset by hand / logged out by the server manager
        return
    g.user = user
    users.touch_seen(get_db(), user)


def _guard():
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        return json_error(403, "csrf", "auth.err.csrf")
    _load_user()
    if must_change_password() and request.endpoint not in MUST_CHANGE_ALLOWED:
        if wants_json():
            return json_error(403, "must_change_password", "auth.err.must_change")
        return redirect(url_for("auth.change_password_page"))
    return None


def init_guard(app) -> None:
    app.before_request(_guard)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            if wants_json():
                return json_error(401, "unauthorized", "auth.err.unauthorized")
            return redirect(url_for("auth.login_page", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper
