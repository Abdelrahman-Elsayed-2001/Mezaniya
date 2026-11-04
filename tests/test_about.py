"""The About page, the release notes in the language files, and the generated docs/whats_new*.md."""
import subprocess
import sys

import pytest

from app import about, i18n
from app.config import BASE_DIR
from app.version import __version__
from tests.api_helpers import Api, make_app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def test_about_page_is_public_and_complete(app):
    page = app.test_client().get("/about").get_data(as_text=True)
    assert about.REPO_URL in page and about.LICENSE_URL in page and about.COPYRIGHT in page
    assert about.version_label() in page and i18n.t("about.current", "ar") in page
    assert "<code>languages.db</code>" in page                    # `name` in a note → <code>


def test_about_page_for_a_logged_in_user_links_back_to_the_budget(app):
    api = Api(app, "alice")
    page = api.get("/about").get_data(as_text=True)
    assert "alice" in page and 'href="/"' in page
    assert 'href="/about"' in api.get("/").get_data(as_text=True)


def test_the_current_version_is_the_newest_release():
    assert about.RELEASES[0][0] == __version__


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_every_release_has_notes_and_every_note_a_release(lang):
    keys = {k for k in i18n.catalog(lang) if k.startswith("whats_new.")}
    assert keys == {about.notes_key(v) for v, _ in about.RELEASES}
    assert all(r["notes"] for r in about.release_notes(lang))


def test_whats_new_files_are_up_to_date():
    r = subprocess.run([sys.executable, str(BASE_DIR / "docs" / "tools" / "whats_new.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
