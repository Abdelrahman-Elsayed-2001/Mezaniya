"""Dashboard widgets: one template per widget, and the default layout uses each exactly once."""
import re

from app import i18n
from app.config import BASE_DIR
from app.dashboard import DEFAULT_LAYOUT, WIDGETS

WIDGET_DIR = BASE_DIR / "app" / "templates" / "widgets"


def test_every_widget_has_a_template_and_a_title():
    assert {p.stem for p in WIDGET_DIR.glob("*.html")} == set(WIDGETS)
    assert all(i18n.has(key) for key in WIDGETS.values())


def test_default_layout_uses_each_widget_once():
    used = [w for row in DEFAULT_LAYOUT for w in row]
    assert sorted(used) == sorted(WIDGETS)


def test_widgets_can_be_repeated_on_a_page():
    # a widget may appear more than once (step 5), so none may carry a fixed element id
    for path in WIDGET_DIR.glob("*.html"):
        assert not re.search(r"""\sid=["']""", path.read_text(encoding="utf-8")), path.name
