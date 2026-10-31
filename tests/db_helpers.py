from app.db.repo import base, budgets


def type_id(conn, user_id, name):
    return next(t["id"] for t in base.list_rows(conn, user_id, "expense_types") if t["name"] == name)


def current_budget(conn, user_id):
    return budgets.list_budgets(conn, user_id)[0]


def add_expense(conn, user_id, budget_id, name="انترنت", type_name="أساسي", **values):
    return budgets.create_item(conn, user_id, "expenses", budget_id,
                               {"name": name, "type_id": type_id(conn, user_id, type_name), **values})
