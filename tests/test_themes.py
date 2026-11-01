"""Colour themes: built-in list, the user's choice, custom colours and the page's <style>."""
import pytest

from app import themes
from app.db.repo import Conflict, settings
from tests.api_helpers import Api, make_app

MINE = {"text": "#111111", "background": "#fafafa", "primary": "#e1572d", "secondary": "#a6ed7d", "accent": "#55e766"}


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def test_every_builtin_theme_derives_readable_colours(conn):
    rows = settings.list_themes(conn)
    assert len(rows) == 9 and rows[0]["id"] == "ember"
    for th in rows:
        v = themes.css_vars(th["colors"], th["mode"])
        assert themes.contrast(v["--text"], v["--surface"]) >= 4.5, th["id"]
        assert themes.contrast(v["--primary-ink"], v["--surface"]) >= 4.5, th["id"]
        assert themes.contrast(v["--muted"], v["--surface"]) >= 4.5, th["id"]
        assert themes.contrast(v["--primary-text"], v["--primary"]) >= 3, th["id"]
        assert themes.mode_of(th["colors"]) == th["mode"], th["id"]


@pytest.mark.parametrize("bad", [
    {**MINE, "text": "red"}, {**MINE, "text": "#12345"}, {**MINE, "text": "#111111;}body{"},
    {k: v for k, v in MINE.items() if k != "accent"}, {**MINE, "extra": "#000000"}, "#111111", None,
])
def test_only_exact_hex_colours_get_through(bad):
    with pytest.raises(ValueError):
        themes.parse_colors(bad)


def test_default_is_auto_with_no_style(conn, alice):
    s = settings.get_settings(conn, alice)
    assert (s["theme"], s["theme_colors"]) == ("auto", None)
    assert settings.theme_css(s, conn) is None


def test_repo_refuses_unknown_theme_and_bad_colours(conn, alice):
    with pytest.raises(Conflict):
        settings.update_settings(conn, alice, {"theme": "neon"})
    with pytest.raises(Conflict):
        settings.update_settings(conn, alice, {"theme_colors": {**MINE, "text": "url(x)"}})


def test_custom_colours_are_kept_when_switching_back_and_forth(conn, alice):
    s = settings.update_settings(conn, alice, {"theme": "custom", "theme_colors": MINE})
    assert s["theme"] == "custom" and s["theme_colors"] == MINE
    assert "--bg:#fafafa" in settings.theme_css(s, conn)
    s = settings.update_settings(conn, alice, {"theme": "midnight"})
    assert s["theme_colors"] == MINE and "--bg:#080b12" in settings.theme_css(s, conn)
    s = settings.reset_settings(conn, alice, ["theme"])
    assert s["theme"] == "auto" and s["theme_colors"] is None


def test_api_theme_flow_and_page_style(app):
    api = Api(app, "alice")
    listed = api.get("/api/themes").get_json()
    assert listed[0]["name"] == "جمر" and listed[0]["css"].startswith(":root{")
    assert '<style id="theme-style"></style>' in api.get("/").get_data(as_text=True)

    r = api.patch("/api/settings", {"theme": "ember"}).get_json()
    assert "--bg:#090401" in r["theme_css"]
    assert "--bg:#090401" in api.get("/settings").get_data(as_text=True)

    assert api.patch("/api/settings", {"theme": "neon"}).status_code == 422
    assert api.patch("/api/settings", {"theme": "custom"}).status_code == 422       # no colours yet
    assert api.patch("/api/settings", {"theme": "custom", "theme_colors": {**MINE, "text": "red"}}).status_code == 422
    r = api.patch("/api/settings", {"theme": "custom", "theme_colors": MINE}).get_json()
    assert r["theme"] == "custom" and "--primary:#e1572d" in r["theme_css"]


def test_preview_saves_nothing_and_warns_on_low_contrast(app):
    api = Api(app, "alice")
    r = api.post("/api/themes/preview", {"theme_colors": {**MINE, "text": "#eeeeee"}}).get_json()
    assert r["mode"] == "light" and r["css"].startswith(":root{") and len(r["warnings"]) == 1
    assert api.post("/api/themes/preview", {"theme_colors": MINE}).get_json()["warnings"] == []
    assert api.get("/api/settings").get_json()["theme"] == "auto"
    assert api.post("/api/themes/preview", {}).status_code == 422


def test_login_page_has_no_theme(app):
    assert '<style id="theme-style"></style>' in Api(app).client.get("/login").get_data(as_text=True)
