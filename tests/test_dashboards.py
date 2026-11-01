"""Customizable dashboards: the 'dashboards' setting (app/dashboard.py)."""
import json

import pytest

from app import dashboard
from app.db.repo import Conflict, settings
from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def layout(*boards):
    return {"boards": [{"id": bid, "name": name, "rows": rows} for bid, name, rows in boards]}


def test_stored_default_matches_the_code(conn):
    stored = conn.execute("SELECT default_value FROM default_settings WHERE key = 'dashboards'").fetchone()[0]
    assert json.loads(stored) == dashboard.default_dashboards()


def test_new_user_gets_the_default(conn, alice):
    assert settings.get_settings(conn, alice)["dashboards"] == dashboard.default_dashboards()


def test_boards_can_repeat_and_hide_widgets(conn, alice):
    mine = layout(("main", None, [["summary", "expenses"], ["summary"]]), ("charts", "الرسوم", [["daily"]]))
    s = settings.update_settings(conn, alice, {"dashboards": mine})
    boards = s["dashboards"]["boards"]
    assert boards[0]["rows"] == [["summary", "expenses"], ["summary"]] and boards[1]["name"] == "الرسوم"
    assert "goals" not in json.dumps(boards)            # hidden widgets stay hidden


@pytest.mark.parametrize("bad", [
    {"boards": []},
    layout(("main", None, [["nope"]])),
    layout(("main", None, [["summary", "goals", "daily", "details"]])),     # 4 in a row
    layout(("main", None, [[]])),
    layout(("main", None, []), ("main", None, [])),                         # duplicate id
    layout(("Main!", None, [])),
    layout(("main", "x" * 41, [])),
    layout(("main", "  ", [])),
    {"boards": [{"id": f"b{i}", "name": None, "rows": []} for i in range(11)]},
    layout(("main", None, [["summary"]] * 41)),
    "summary",
])
def test_bad_layouts_are_refused(conn, alice, bad):
    with pytest.raises(Conflict):
        settings.update_settings(conn, alice, {"dashboards": bad})


def test_stored_layout_is_cleaned_on_read():
    stored = {"boards": [{"id": "main", "name": None, "rows": [["summary", "gone"], ["gone"]]},
                         {"id": "BAD", "rows": []}],
              "seen": [w for w in dashboard.WIDGETS if w != "daily"]}
    out = dashboard.normalize(stored)
    assert [b["id"] for b in out["boards"]] == ["main"]
    assert out["boards"][0]["rows"] == [["summary"], ["daily"]]       # unknown dropped, new widget added
    assert dashboard.normalize("garbage") == dashboard.default_dashboards()


def test_api_and_page(app):
    api = Api(app, "alice")
    page = api.get("/").get_data(as_text=True)
    assert 'id="dashboard-layout"' in page and 'id="widget-expenses"' in page
    mine = layout(("main", None, [["expenses"]]), ("b2", "Charts", [["daily", "by_type"]]))
    r = api.patch("/api/settings", {"dashboards": mine})
    assert r.status_code == 200 and r.get_json()["dashboards"]["boards"][1]["rows"] == [["daily", "by_type"]]
    assert '"Charts"' in api.get("/").get_data(as_text=True)
    assert api.patch("/api/settings", {"dashboards": layout(("main", None, [["x"]]))}).status_code == 422
