import pytest

from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.mark.parametrize("path", ["/", "/settings"])
def test_pages_need_login(app, path):
    r = Api(app).get(path)
    assert r.status_code == 302 and r.headers["Location"].startswith("/login")


def test_pages_render_for_logged_in_user(app):
    api = Api(app, "alice")
    home = api.get("/").get_data(as_text=True)
    assert 'x-data="dashboard"' in home and "alice" in home and 'href="/settings"' in home
    settings = api.get("/settings").get_data(as_text=True)
    assert 'x-data="settingsPage"' in settings and "البنود المتكررة" in settings


@pytest.mark.parametrize("asset", ["js/api.js", "js/dashboard.js", "js/settings.js", "css/app.css",
                                   "vendor/alpine-3.14.9.min.js"])
def test_static_assets_are_served(app, asset):
    r = app.test_client().get(f"/static/{asset}")
    assert r.status_code == 200 and len(r.data) > 100


def test_stage_banner_shows_version(app):
    from app.version import STAGE, __version__
    page = Api(app, "alice").get("/").get_data(as_text=True)
    assert ('class="stage-banner"' in page) == bool(STAGE)
    if STAGE:
        assert __version__ in page
    assert app.test_client().get("/healthz").get_json()["version"] == __version__
