"""What to do with a mezaniya.db or languages.db this version can't open, instead of stopping with
an error: delete it, set it aside under another name, or (when app.db.upgrades has fix scripts
for its generation) upgrade it. A fresh file is made on the next start in the first two cases.
The server must not be running.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.db import upgrades
from app.db.connection import IncompatibleDatabase, check_compatible, connect
from app.i18n import store as language_store

DATA, LANGUAGES = "data", "languages"
SIDE_FILES = ("-wal", "-shm", "-journal")


@dataclass
class Problem:
    kind: str                 # DATA or LANGUAGES
    path: Path
    found: int
    expected: int
    upgradable: bool


def _check(path: Path, check) -> IncompatibleDatabase | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(path)
    try:
        check(conn)
    except IncompatibleDatabase as e:
        return e
    finally:
        conn.close()
    return None


def problems(db_path: Path, languages_path: Path) -> list[Problem]:
    found = []
    e = _check(Path(db_path), check_compatible)
    if e:
        found.append(Problem(DATA, Path(db_path), e.generation, e.expected, upgrades.can_upgrade(e.generation)))
    e = _check(Path(languages_path), language_store.check_compatible)
    if e:                      # only ever newer than this version: nothing to upgrade
        found.append(Problem(LANGUAGES, Path(languages_path), e.generation, e.expected, False))
    return found


def _remove(path: Path) -> None:
    for side in SIDE_FILES:
        extra = path.with_name(path.name + side)
        if extra.exists():
            extra.unlink()
    path.unlink()


def delete(problem: Problem) -> None:
    _remove(problem.path)


def _keep_copy(problem: Problem, now: datetime | None) -> Path:
    """Copy to <name>-old-v<generation>-<stamp>.db next to it; the copy takes in anything still in
    the -wal file."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = problem.path.with_name(f"{problem.path.stem}-old-v{problem.found}-{stamp}{problem.path.suffix}")
    src = sqlite3.connect(problem.path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def set_aside(problem: Problem, now: datetime | None = None) -> Path:
    """Rename (as a copy, then remove the original). Returns the new path."""
    target = _keep_copy(problem, now)
    _remove(problem.path)
    return target


def upgrade(problem: Problem, now: datetime | None = None) -> Path:
    """Keep a copy as set_aside names it, then run the fix scripts on the file. Returns the copy."""
    if not problem.upgradable:
        raise ValueError(f"{problem.path} can't be upgraded")
    copy = _keep_copy(problem, now)
    conn = connect(problem.path)
    try:
        upgrades.upgrade(conn)
        check_compatible(conn)
    finally:
        conn.close()
    return copy
