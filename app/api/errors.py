"""JSON errors for the API, worded in the request's language."""
from __future__ import annotations

from flask import jsonify

from app.db.repo import Conflict, NotFound
from app.i18n import current_lang, t


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.field, self.extra = status, code, message, field, extra

    def with_field(self, field: str) -> "ApiError":
        self.field = field
        return self

    def response(self):
        body = {"error": self.code, "message": self.message, **self.extra}
        if self.field:
            body["field"] = self.field
        return jsonify(body), self.status


def field_label(name: str) -> str:
    return t(f"field.{name}", current_lang())


def invalid(key: str, field: str | None = None, *, item_id=None, **params) -> ApiError:
    """422 with the translated `key`; `{field}` in the message is the field's label."""
    if field:
        params.setdefault("field", field_label(field))
    extra = {"item_id": item_id} if item_id is not None else {}
    return ApiError(422, "invalid", t(key, current_lang(), **params), field, **extra)


def error(status: int, code: str, key: str, **params) -> ApiError:
    return ApiError(status, code, t(key, current_lang(), **params))


def register_error_handlers(bp) -> None:
    from app.services.rollover import RolloverError

    @bp.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return e.response()

    @bp.errorhandler(NotFound)
    def _not_found(_e: NotFound):
        return error(404, "not_found", "api.err.not_found").response()

    @bp.errorhandler(RolloverError)
    def _rollover(e: RolloverError):
        return error(e.status, e.code, f"rollover.err.{e.code}", **e.params).response()

    @bp.errorhandler(Conflict)
    def _conflict(e: Conflict):
        key = f"api.conflict.{e.code}"
        return error(409, e.code, key if t(key) != key else "api.conflict.integrity").response()
