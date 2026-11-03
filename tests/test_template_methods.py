"""Every method a page's markup calls (@click, x-text, …) is defined in that page's scripts.
A method lost in an edit otherwise only shows up as a dead button in the browser."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app"
PAGES = {
    "settings": (["templates/settings.html"], ["static/js/settings.js", "static/js/api.js"]),
    "dashboard": (["templates/app.html", "templates/_foreign_balances.html", "templates/widgets"],
                  ["static/js/dashboard.js", "static/js/api.js"]),
}
ATTR = re.compile(r'''(?:@[\w.-]+|x-[\w:.-]+|:[\w-]+)="([^"]*)"''')
CALL = re.compile(r"(?<![\w.$'])([a-zA-Z_]\w*)\(")
BUILTIN = {"t", "tr", "Number", "String", "Math", "confirm", "parseInt", "parseFloat", "Boolean", "Object",
           "Array", "Date", "isNaN", "encodeURIComponent", "setTimeout", "if", "for", "switch", "function"}


def _texts(paths):
    for rel in paths:
        p = ROOT / rel
        for f in (sorted(p.glob("*.html")) if p.is_dir() else [p]):
            yield f.read_text(encoding="utf-8")


def test_called_methods_exist():
    missing = {}
    for page, (markup, scripts) in PAGES.items():
        js = "\n".join(_texts(scripts))
        defined = set(re.findall(r"^\s*(?:async\s+)?([a-zA-Z_]\w*)\s*\([^)]*\)\s*\{", js, re.M))
        defined |= set(re.findall(r"\b(?:function|const|let)\s+([a-zA-Z_]\w*)", js))
        called = {name for html in _texts(markup) for attr in ATTR.findall(html) for name in CALL.findall(attr)}
        gone = sorted(called - defined - BUILTIN)
        if gone:
            missing[page] = gone
    assert missing == {}
