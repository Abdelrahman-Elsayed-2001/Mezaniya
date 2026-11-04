"""What the About page (web) and the About window (server manager) show.

The release notes are texts like any other: whats_new.v<major>_<minor>_<patch> in the language
files, one line per point, so they're edited and translated from the server manager too.
docs/whats_new.md and docs/whats_new.en.md are generated from them (docs/tools/whats_new.py).
"""
from __future__ import annotations

from .version import STAGE, __version__

APP_NAME = "Mezaniya"
REPO_URL = "https://github.com/Abdelrahman-Elsayed-2001/Mezaniya"
LICENSE_NAME = "MIT License"
LICENSE_URL = f"{REPO_URL}/blob/main/LICENSE"
COPYRIGHT = "Copyright (c) 2026 Abdelrahman Elsayed"

# newest first; add the new version here (and its whats_new key) on every release
RELEASES: tuple[tuple[str, str], ...] = (
    ("0.4.1", "beta"),
    ("0.4.0", "beta"),
    ("0.3.0", "beta"),
    ("0.2.0", "beta"),
    ("0.1.0", "beta"),
)


def notes_key(version: str) -> str:
    return "whats_new.v" + version.replace(".", "_")


def version_label(version: str = __version__, stage: str = STAGE) -> str:
    return f"{version} ({stage})" if stage else version


def release_notes(lang: str | None = None) -> list[dict]:
    """[{version, label, current, notes: [line, …]}, …], newest first, in the given language."""
    from . import i18n
    return [{"version": v, "label": version_label(v, stage), "current": v == __version__,
             "notes": [line for line in i18n.t(notes_key(v), lang).splitlines() if line.strip()]}
            for v, stage in RELEASES]
