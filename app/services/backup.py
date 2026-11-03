"""Database backups: consistent copies via SQLite's online backup API (safe while the
server is running, WAL included), kept as data/backups/mezaniya-YYYYMMDD-HHMMSS.db.

languages.db (next to the database by default) is copied alongside as languages-YYYYMMDD-HHMMSS.db
with the same stamp; restoring it is a separate choice, since the languages are shared by everyone."""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from app.db.connection import check_compatible
from app.i18n import store as language_store
from app.i18n import t

log = logging.getLogger(__name__)
PREFIX = "mezaniya-"
LANGUAGES_PREFIX = "languages-"


def default_languages_path(db_path: Path) -> Path:
    return Path(db_path).parent / "languages.db"


def languages_backup(backup_file: Path) -> Path | None:
    """The languages copy made with this backup, if there is one."""
    name = backup_file.name
    if not name.startswith(PREFIX):
        return None
    companion = backup_file.with_name(LANGUAGES_PREFIX + name[len(PREFIX):])
    return companion if companion.is_file() else None


def _copy_db(source: Path, target: Path) -> None:
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _healthy(path: Path) -> bool:
    check = sqlite3.connect(path)
    try:
        return check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        check.close()


def _replace(live: Path, source: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = live.with_name(live.name + suffix)
        if side.exists():
            side.unlink()
    shutil.copyfile(source, live)


def list_backups(backup_dir: Path) -> list[Path]:
    """Oldest first."""
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{PREFIX}*.db"))


def backup_now(db_path: Path, backup_dir: Path, keep: int, now: datetime | None = None,
               languages_path: Path | None = None) -> Path:
    """Back up the database (and languages.db, when it exists). Returns the database copy's path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{PREFIX}{stamp}.db"
    n = 1
    while target.exists():
        target = backup_dir / f"{PREFIX}{stamp}-{n}.db"
        n += 1
    _copy_db(db_path, target)
    languages = languages_path or default_languages_path(db_path)
    if Path(languages).is_file():
        _copy_db(Path(languages), target.with_name(LANGUAGES_PREFIX + target.name[len(PREFIX):]))
    prune(backup_dir, keep)
    log.info(t("log.backup_written", path=target))
    return target


def prune(backup_dir: Path, keep: int) -> list[Path]:
    removed = []
    if keep <= 0:
        return removed
    for old in list_backups(backup_dir)[:-keep]:
        companion = languages_backup(old)
        old.unlink()
        removed.append(old)
        if companion:
            companion.unlink()
    return removed


def backup_due(backup_dir: Path, interval_hours: float, now: float | None = None) -> bool:
    existing = list_backups(backup_dir)
    if not existing:
        return True
    age = (now or time.time()) - max(p.stat().st_mtime for p in existing)
    return age >= interval_hours * 3600


def restore(db_path: Path, backup_file: Path, backup_dir: Path, keep: int, with_languages: bool = False,
            languages_path: Path | None = None) -> Path | None:
    """Replace the live DB with a backup, and languages.db with the backup's languages copy when
    `with_languages`. The current files are backed up first. The server must be stopped. Returns the
    safety backup's path. Raises ValueError for a damaged file and IncompatibleDatabase for one from
    another version, before anything is changed."""
    languages_copy = languages_backup(backup_file) if with_languages else None
    if with_languages and languages_copy is None:
        raise ValueError(f"{backup_file} has no languages copy")
    for path in filter(None, (backup_file, languages_copy)):
        if not _healthy(path):
            raise ValueError(f"{path} is not a healthy database")
    for path, check in ((backup_file, check_compatible), (languages_copy, language_store.check_compatible)):
        if path is None:
            continue
        conn = sqlite3.connect(path)
        try:
            check(conn)                  # a file this version can't read would leave an app that can't start
        finally:
            conn.close()
    languages = Path(languages_path or default_languages_path(db_path))
    safety = backup_now(db_path, backup_dir, keep=0, languages_path=languages) if db_path.exists() else None
    _replace(db_path, backup_file)
    if languages_copy:
        _replace(languages, languages_copy)
    log.info(t("log.restored", db=db_path, file=backup_file, safety=safety))
    return safety


class BackupScheduler:
    """Background thread: makes a backup whenever the newest one is older than the interval."""

    def __init__(self, db_path: Path, backup_dir: Path, keep: int, interval_hours: float,
                 check_every_seconds: float = 3600, languages_path: Path | None = None):
        self.db_path, self.backup_dir, self.keep = db_path, backup_dir, keep
        self.languages_path = languages_path
        self.interval_hours, self.check_every = interval_hours, check_every_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mezaniya-backup", daemon=True)

    def start(self) -> "BackupScheduler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def tick(self) -> Path | None:
        if self.db_path.exists() and backup_due(self.backup_dir, self.interval_hours):
            return backup_now(self.db_path, self.backup_dir, self.keep, languages_path=self.languages_path)
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:   # never let a failed backup kill the thread
                log.exception(t("log.backup_failed"))
            self._stop.wait(self.check_every)
