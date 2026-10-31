"""Generic CRUD that is always scoped to one user.

Every function takes `user_id` and every statement filters on it, so a row of
another user is indistinguishable from a missing row (NotFound). Table and column
names come from the whitelist below only; values are always bound parameters.
Referenced ids (budget_id, type_id, ...) are checked to belong to the same user
before writing; the composite foreign keys in the schema are the safety net.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


class NotFound(LookupError):
    def __init__(self, entity: str, id_):
        super().__init__(f"{entity} {id_} not found")
        self.entity, self.id = entity, id_


class Conflict(Exception):
    """Write rejected by a uniqueness / integrity rule. `code` is stable for callers to map."""

    def __init__(self, message: str, code: str = "integrity"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Table:
    name: str
    columns: frozenset[str]                      # writable columns (user_id/id excluded)
    refs: dict[str, str] = field(default_factory=dict)   # column -> referenced table
    bools: frozenset[str] = frozenset()
    scope: str | None = None                     # column grouping sort_order (e.g. budget_id)
    order_by: str = "sort_order, id"


def _t(name, columns, refs=None, bools=(), scope=None, order_by="sort_order, id") -> Table:
    return Table(name, frozenset(columns.split()), refs or {}, frozenset(bools), scope, order_by)


TABLES: dict[str, Table] = {t.name: t for t in (
    _t("expense_types", "name role is_system sort_order", bools=("is_system",)),
    _t("goal_categories", "name sort_order"),
    _t("recurring_templates",
       "kind name type_id amount is_savings_withdrawal active notes sort_order",
       refs={"type_id": "expense_types"}, bools=("is_savings_withdrawal", "active")),
    _t("budgets", "year month emergency_current emergency_min savings_current source_budget_id",
       refs={"source_budget_id": "budgets"}, order_by="year, month"),
    _t("incomes",
       "budget_id source amount is_savings_withdrawal template_id needs_amount notes sort_order",
       refs={"budget_id": "budgets", "template_id": "recurring_templates"},
       bools=("is_savings_withdrawal", "needs_amount"), scope="budget_id"),
    _t("expenses",
       "budget_id name type_id cash_amount inst_balance inst_count inst_original_count carried_in "
       "status deferred_amount paid_at notes template_id prev_expense_id needs_amount sort_order",
       refs={"budget_id": "budgets", "type_id": "expense_types",
             "template_id": "recurring_templates", "prev_expense_id": "expenses"},
       bools=("needs_amount",), scope="budget_id"),
    _t("goals",
       "budget_id name category_id target_amount priority manual_allocation notes sort_order",
       refs={"budget_id": "budgets", "category_id": "goal_categories"}, scope="budget_id"),
)}


def _table(name: str) -> Table:
    try:
        return TABLES[name]
    except KeyError:
        raise ValueError(f"unknown table {name!r}") from None


def _check_columns(t: Table, values: dict) -> None:
    unknown = set(values) - t.columns
    if unknown:
        raise ValueError(f"unknown columns for {t.name}: {sorted(unknown)}")


def row_to_dict(t: Table, row: sqlite3.Row) -> dict:
    d = dict(row)
    for b in t.bools:
        d[b] = bool(d[b])
    return d


def exists(conn: sqlite3.Connection, user_id: int, table: str, id_) -> bool:
    t = _table(table)
    return conn.execute(f"SELECT 1 FROM {t.name} WHERE id = ? AND user_id = ?", (id_, user_id)).fetchone() is not None


def _check_refs(conn, user_id: int, t: Table, values: dict) -> None:
    for col, ref in t.refs.items():
        v = values.get(col)
        if v is not None and not exists(conn, user_id, ref, v):
            raise NotFound(ref, v)


def get(conn: sqlite3.Connection, user_id: int, table: str, id_) -> dict:
    t = _table(table)
    row = conn.execute(f"SELECT * FROM {t.name} WHERE id = ? AND user_id = ?", (id_, user_id)).fetchone()
    if row is None:
        raise NotFound(t.name, id_)
    return row_to_dict(t, row)


def list_rows(conn: sqlite3.Connection, user_id: int, table: str, **where) -> list[dict]:
    t = _table(table)
    _check_columns(t, where)
    clauses = ["user_id = ?"] + [f"{c} = ?" for c in where]
    sql = f"SELECT * FROM {t.name} WHERE {' AND '.join(clauses)} ORDER BY {t.order_by}"
    return [row_to_dict(t, r) for r in conn.execute(sql, (user_id, *where.values()))]


def _integrity(t: Table, exc: sqlite3.IntegrityError) -> Conflict:
    code = "duplicate" if "UNIQUE constraint failed" in str(exc) else "integrity"
    return Conflict(f"{t.name}: {exc}", code)


def insert(conn: sqlite3.Connection, user_id: int, table: str, values: dict) -> dict:
    t = _table(table)
    values = dict(values)
    _check_columns(t, values)
    _check_refs(conn, user_id, t, values)
    if "sort_order" in t.columns and "sort_order" not in values:
        scope_sql, scope_args = "", ()
        if t.scope:
            scope_sql, scope_args = f" AND {t.scope} = ?", (values.get(t.scope),)
        row = conn.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {t.name} WHERE user_id = ?{scope_sql}",
                           (user_id, *scope_args)).fetchone()
        values["sort_order"] = row[0]
    cols = ["user_id", *values]
    sql = f"INSERT INTO {t.name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    try:
        cur = conn.execute(sql, (user_id, *values.values()))
    except sqlite3.IntegrityError as e:
        raise _integrity(t, e) from e
    return get(conn, user_id, table, cur.lastrowid)


def update(conn: sqlite3.Connection, user_id: int, table: str, id_, values: dict) -> dict:
    t = _table(table)
    _check_columns(t, values)
    if not exists(conn, user_id, table, id_):
        raise NotFound(t.name, id_)
    if not values:
        return get(conn, user_id, table, id_)
    _check_refs(conn, user_id, t, values)
    sets = ", ".join(f"{c} = ?" for c in values)
    try:
        conn.execute(f"UPDATE {t.name} SET {sets} WHERE id = ? AND user_id = ?", (*values.values(), id_, user_id))
    except sqlite3.IntegrityError as e:
        raise _integrity(t, e) from e
    return get(conn, user_id, table, id_)


def delete(conn: sqlite3.Connection, user_id: int, table: str, id_) -> None:
    t = _table(table)
    try:
        cur = conn.execute(f"DELETE FROM {t.name} WHERE id = ? AND user_id = ?", (id_, user_id))
    except sqlite3.IntegrityError as e:
        raise _integrity(t, e) from e
    if cur.rowcount == 0:
        raise NotFound(t.name, id_)
