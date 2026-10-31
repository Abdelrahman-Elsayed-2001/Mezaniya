"""Request body parsing: each entity declares its writable fields; anything else is rejected.
Field labels in messages come from strings.json ("field.<name>")."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from flask import request

from .errors import error, invalid

MAX_MONEY = 1_000_000_000_000


class FieldError(Exception):
    """Raised by a parser; parse() turns it into a 422 naming the field."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params


@dataclass(frozen=True)
class Field:
    parse: Callable[[Any, "Field"], Any]
    required: bool = False
    nullable: bool = False
    max_len: int = 200
    choices: tuple = ()
    max_value: float | None = None


def _number(value, f: Field) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    x = float(value)
    if not math.isfinite(x):
        raise ValueError
    return x


def money(value, f: Field) -> float:
    x = _number(value, f)
    if x < 0:
        raise FieldError("api.err.non_negative")
    if x > MAX_MONEY:
        raise FieldError("api.err.too_large")
    return round(x, 2)


def ratio(value, f: Field) -> float:
    """A fraction (0.25 = 25%)."""
    x = _number(value, f)
    limit = 1 if f.max_value is None else f.max_value
    if not 0 <= x <= limit:
        raise FieldError("api.err.ratio_range", max=f"{limit * 100:g}")
    return round(x, 6)


def count(value, f: Field) -> int:
    x = _number(value, f)
    if x != int(x) or x < 0:
        raise FieldError("api.err.integer")
    if f.max_value is not None and x > f.max_value:
        raise FieldError("api.err.max_value", max=f"{f.max_value:g}")
    return int(x)


def number(value, f: Field) -> float:
    x = _number(value, f)
    if x < 0 or (f.max_value is not None and x > f.max_value):
        raise FieldError("api.err.number_range", max=f"{f.max_value:g}")
    return x


def text(value, f: Field) -> str:
    if not isinstance(value, str):
        raise ValueError
    value = value.strip()
    if f.required and not value:
        raise FieldError("api.err.required")
    if len(value) > f.max_len:
        raise FieldError("api.err.too_long", max=f.max_len)
    return value


def optional_text(value, f: Field) -> str | None:
    return text(value, f) or None


def boolean(value, f: Field) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def choice(value, f: Field) -> str:
    if value not in f.choices:
        raise FieldError("api.err.choice", choices=", ".join(f.choices))
    return value


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso_date(value, f: Field) -> str:
    if not isinstance(value, str):
        raise ValueError
    try:
        if not _DATE_RE.match(value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise FieldError("api.err.date") from None


def ref_id(value, f: Field) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError
    return value


def id_list(value, f: Field) -> list[int]:
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError
    return [ref_id(v, f) for v in value]


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise error(400, "bad_request", "auth.err.bad_request")
    return data


def parse(spec: dict[str, Field], data: dict, partial: bool) -> dict:
    """Validate `data` against `spec`. partial=True (PATCH) skips required checks for absent fields."""
    unknown = sorted(set(data) - set(spec))
    if unknown:
        raise invalid("api.err.unknown_fields", None, fields=", ".join(unknown)).with_field(unknown[0])
    out = {}
    for name, f in spec.items():
        if name not in data:
            if f.required and not partial:
                raise invalid("api.err.required", name)
            continue
        value = data[name]
        if value is None or (isinstance(value, str) and not value.strip() and f.parse is not text):
            if not f.nullable:
                raise invalid("api.err.required", name)
            out[name] = None
            continue
        try:
            out[name] = f.parse(value, f)
        except FieldError as e:
            raise invalid(e.key, name, **e.params) from None
        except (ValueError, TypeError):
            raise invalid("api.err.invalid_value", name) from None
    return out
