"""Colour themes: five base colours in, every CSS variable app.css uses out.

A theme is {text, background, primary, secondary, accent}. The rest (surfaces, borders, muted
text, hover, chart colours) is derived here, so a custom theme needs five picks, not twenty.
Meaning colours (danger, positive, warning) don't follow the theme: red must stay red. They
come in a light and a dark set, chosen by the background's brightness.

Everything is computed on the server and printed into the page (base.html), so the page never
flashes the default colours first. Colours are only ever #rrggbb (checked by parse_colors), which
is what makes printing them into a <style> block safe.
"""
from __future__ import annotations

import re

COLOR_KEYS = ("text", "background", "primary", "secondary", "accent")
SPECIAL = ("auto", "custom")           # theme setting values besides theme ids
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

_MEANING = {
    "light": {"--danger": "#b42318", "--danger-bg": "#fef3f2", "--warn-bg": "#fffaeb", "--warn-text": "#93370d",
              "--pos": "#067647", "--pos-bg": "#ecfdf3", "--orange": "#b54708", "--orange-bg": "#fff4ed"},
    "dark": {"--danger": "#fda29b", "--danger-bg": "#3a1714", "--warn-bg": "#3a2a0b", "--warn-text": "#fedf89",
             "--pos": "#75e0a7", "--pos-bg": "#0b2b1c", "--orange": "#fdb022", "--orange-bg": "#3a220b"},
}


def parse_colors(value) -> dict[str, str]:
    """The five colours as lower-case #rrggbb, or ValueError. Nothing else gets through."""
    if not isinstance(value, dict) or set(value) != set(COLOR_KEYS):
        raise ValueError(f"colours must be exactly {', '.join(COLOR_KEYS)}")
    out = {}
    for k in COLOR_KEYS:
        v = value[k]
        if not isinstance(v, str) or not _HEX.match(v):
            raise ValueError(f"{k} must be #rrggbb")
        out[k] = v.lower()
    return out


def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    """`amount` of b into a (0 = a, 1 = b)."""
    return _hex(x + (y - x) * amount for x, y in zip(_rgb(a), _rgb(b)))


def luminance(h: str) -> float:
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in _rgb(h)]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mode_of(colors: dict) -> str:
    return "dark" if luminance(colors["background"]) < 0.18 else "light"


def _readable(color: str, on: str, toward: str, target: float) -> str:
    """`color`, moved toward `toward` just enough to reach `target` contrast on `on`."""
    for step in range(21):
        c = mix(color, toward, step / 20)
        if contrast(c, on) >= target:
            return c
    return toward


def css_vars(colors: dict, mode: str | None = None) -> dict[str, str]:
    mode = mode or mode_of(colors)
    text, bg = colors["text"], colors["background"]
    primary, secondary, accent = colors["primary"], colors["secondary"], colors["accent"]
    surface = mix(bg, "#ffffff", 0.6 if mode == "light" else 0.06)
    on_primary = max((text, bg, "#ffffff"), key=lambda c: contrast(c, primary))
    series = _readable(accent, surface, text, 3)
    out = {
        "--bg": bg,
        "--surface": surface,
        "--text": text,
        "--muted": _readable(mix(text, bg, 0.4), surface, text, 4.5),
        "--border": mix(text, bg, 0.82),
        "--primary": primary,
        "--primary-hover": mix(primary, text, 0.18),
        "--primary-text": on_primary,
        # primary as a text colour (links, active tab): darkened/lightened until readable
        "--primary-ink": _readable(primary, surface, text, 4.5),
        "--series-1": series,
        "--series-1-soft": mix(series, surface, 0.5),
        "--series-track": mix(secondary, surface, 0.55),
        "--grid": mix(text, surface, 0.88),
        "--axis": mix(text, surface, 0.75),
        "--axis-ink": _readable(mix(text, bg, 0.45), surface, text, 3),
        "--focus": accent,
        **_MEANING[mode],
    }
    return out


def css(colors: dict, mode: str | None = None) -> str:
    """A :root rule for a <style> block. Safe to print: every value is a computed #rrggbb."""
    mode = mode or mode_of(colors)
    body = "".join(f"{k}:{v};" for k, v in css_vars(colors, mode).items())
    return f":root{{{body}color-scheme:{mode};}}"


def warnings(colors: dict) -> list[str]:
    """What to tell the user about a custom palette (they may still save it)."""
    out = []
    if contrast(colors["text"], colors["background"]) < 4.5:
        out.append("low_text_contrast")
    return out
