"""REST API under /api. Every route needs a logged-in user and only sees that user's data."""
from __future__ import annotations

from flask import Blueprint

from app.auth.session import current_user, json_error

from .errors import register_error_handlers

bp = Blueprint("api", __name__, url_prefix="/api")
register_error_handlers(bp)


@bp.before_request
def _require_login():
    if current_user() is None:
        return json_error(401, "unauthorized", "auth.err.unauthorized")
    return None


def uid() -> int:
    return current_user()["id"]


from . import budgets, catalog  # noqa: E402,F401  (registers routes)
