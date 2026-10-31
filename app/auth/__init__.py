"""Authentication: sessions, register/login, forced password change after a manual reset."""
from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask

from .ratelimit import LoginLimiter
from .routes import bp
from .session import current_user, init_guard, login_required

__all__ = ["init_app", "current_user", "login_required"]


def load_or_create_secret(path: Path) -> str:
    """Persist the signing key so sessions survive restarts."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key


def init_app(app: Flask) -> None:
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_or_create_secret(Path(app.config["DATA_DIR"]) / "secret_key")
    app.config.update(
        SESSION_COOKIE_NAME="mezaniya_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=app.config["SESSION_DAYS"]),
    )
    app.extensions["login_limiter"] = LoginLimiter(app.config["LOGIN_MAX_ATTEMPTS"],
                                                   app.config["LOGIN_LOCK_SECONDS"])
    init_guard(app)
    app.register_blueprint(bp)
