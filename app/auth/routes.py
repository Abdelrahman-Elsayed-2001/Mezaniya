"""Pages and JSON endpoints for register / login / logout / change password."""
from __future__ import annotations

import math
from datetime import date

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session

from app.db import get_db, transaction
from app.db.repo import Conflict, users
from app.i18n import current_lang, plural
from app.services.seed_user import create_seeded_user

from . import passwords
from .session import current_user, json_error, login_required, must_change_password, start_session

bp = Blueprint("auth", __name__)


def safe_next(target: str | None) -> str:
    """Only same-site relative paths (no open redirect)."""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def _body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _limiter():
    return current_app.extensions["login_limiter"]


def _locked(retry_after: int):
    minutes = math.ceil(retry_after / 60)
    return json_error(429, "locked", "auth.err.locked",
                      {"duration": plural("count.minutes", minutes, current_lang())}, retry_after=retry_after)


def _invalid(error: tuple[str, dict], field: str):
    key, params = error
    return json_error(422, "invalid", key, params, field=field)


def _bad_request():
    return json_error(400, "bad_request", "auth.err.bad_request")


# ---------- pages

@bp.get("/login")
def login_page():
    if current_user():
        return redirect(safe_next(request.args.get("next")))
    return render_template("login.html", allow_registration=current_app.config["ALLOW_REGISTRATION"],
                           next=safe_next(request.args.get("next")))


@bp.get("/register")
def register_page():
    if not current_app.config["ALLOW_REGISTRATION"]:
        abort(404)
    if current_user():
        return redirect("/")
    return render_template("register.html")


@bp.get("/change-password")
@login_required
def change_password_page():
    return render_template("change_password.html", forced=must_change_password(),
                           username=current_user()["username"])


# ---------- JSON

@bp.post("/auth/register")
def register():
    if not current_app.config["ALLOW_REGISTRATION"]:
        return json_error(403, "registration_closed", "auth.err.registration_closed")
    data = _body()
    if data is None:
        return _bad_request()
    username = _text(data, "username").strip()
    password = _text(data, "password")
    if err := passwords.username_error(username):
        return _invalid(err, "username")
    if err := passwords.password_error(password):
        return _invalid(err, "password")
    if password != _text(data, "password_confirm"):
        return _invalid(("auth.err.confirm_mismatch", {}), "password_confirm")
    conn = get_db()
    try:
        user_id = create_seeded_user(conn, username, passwords.hash_password(password), date.today(),
                                     current_lang())
    except Conflict:
        return json_error(409, "username_taken", "auth.err.username_taken", field="username")
    user = users.get_user(conn, user_id)
    users.touch_login(conn, user_id)
    start_session(user)
    return jsonify({"ok": True, "user": {"id": user_id, "username": user["username"]}}), 201


@bp.post("/auth/login")
def login():
    data = _body()
    if data is None:
        return _bad_request()
    username = _text(data, "username").strip()
    password = _text(data, "password")
    ip = request.remote_addr or ""
    if retry := _limiter().retry_after(username, ip):
        return _locked(retry)
    conn = get_db()
    user = users.find_by_username(conn, username) if username else None
    if not passwords.verify(user["password_hash"] if user else None, password):
        _limiter().failure(username, ip)
        return json_error(401, "invalid_credentials", "auth.err.bad_credentials")
    _limiter().success(username, ip)
    users.touch_login(conn, user["id"])
    start_session(user)
    forced = passwords.is_manual(user["password_hash"])
    return jsonify({"ok": True, "must_change_password": forced,
                    "next": "/change-password" if forced else safe_next(_text(data, "next"))})


@bp.post("/auth/logout")
def logout():
    session.clear()
    return "", 204


@bp.get("/auth/me")
@login_required
def me():
    user = current_user()
    return jsonify({"id": user["id"], "username": user["username"],
                    "must_change_password": must_change_password()})


@bp.post("/auth/change-password")
@login_required
def change_password():
    data = _body()
    if data is None:
        return _bad_request()
    user = current_user()
    forced = must_change_password()
    current = _text(data, "current_password")
    new = _text(data, "new_password")
    limiter_key, ip = f"pw:{user['username']}", request.remote_addr or ""

    if not forced:   # after a manual reset the user just proved the manual password at login
        if retry := _limiter().retry_after(limiter_key, ip):
            return _locked(retry)
        if not passwords.verify(user["password_hash"], current):
            _limiter().failure(limiter_key, ip)
            return _invalid(("auth.err.current_wrong", {}), "current_password")
        _limiter().success(limiter_key, ip)
    if err := passwords.password_error(new):
        return _invalid(err, "new_password")
    if new != _text(data, "new_password_confirm"):
        return _invalid(("auth.err.confirm_mismatch", {}), "new_password_confirm")
    if passwords.verify(user["password_hash"], new):
        return _invalid(("auth.err.same_password", {}), "new_password")

    conn = get_db()
    with transaction(conn):
        users.set_password_hash(conn, user["id"], passwords.hash_password(new))
    start_session(users.get_user(conn, user["id"]))   # this session stays, every other one is logged out
    return jsonify({"ok": True})
