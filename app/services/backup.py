"""Database backups: consistent copies via SQLite's online backup API (safe while the
server is running, WAL included), kept as data/backups/mezaniya-YYYYMMDD-HHMMSS.db."""
from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from app.i18n import t

log = logging.getLogger(__name__)
PREFIX = "mezaniya-"


def list_backups(backup_dir: Path) -> list[Path]:
    """Oldest first."""
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{PREFIX}*.db"))


def backup_now(db_path: Path, backup_dir: Path, keep: int, now: datetime | None = None) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{PREFIX}{stamp}.db"
    n = 1
    while target.exists():
        target = backup_dir / f"{PREFIX}{stamp}-{n}.db"
        n += 1
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    prune(backup_dir, keep)
    log.info(t("log.backup_written", path=target))
    return target


def prune(backup_dir: Path, keep: int) -> list[Path]:
    removed = []
    if keep <= 0:
        return removed
    for old in list_backups(backup_dir)[:-keep]:
        old.unlink()
        removed.append(old)
    return removed


def backup_due(backup_dir: Path, interval_hours: float, now: float | None = None) -> bool:
    existing = list_backups(backup_dir)
    if not existing:
        return True
    age = (now or time.time()) - max(p.stat().st_mtime for p in existing)
    return age >= interval_hours * 3600


def restore(db_path: Path, backup_file: Path, backup_dir: Path, keep: int) -> Path | None:
    """Replace the live DB with a backup. The current DB is backed up first.
    The server must be stopped. Returns the safety backup's path."""
    check = sqlite3.connect(backup_file)
    try:
        healthy = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        healthy = False
    finally:
        check.close()
    if not healthy:
        raise ValueError(f"{backup_file} is not a healthy database")
    safety = backup_now(db_path, backup_dir, keep=0) if db_path.exists() else None
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()
    shutil.copyfile(backup_file, db_path)
    log.info(t("log.restored", db=db_path, file=backup_file, safety=safety))
    return safety


class BackupScheduler:
    """Background thread: makes a backup whenever the newest one is older than the interval."""

    def __init__(self, db_path: Path, backup_dir: Path, keep: int, interval_hours: float,
                 check_every_seconds: float = 3600):
        self.db_path, self.backup_dir, self.keep = db_path, backup_dir, keep
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
            return backup_now(self.db_path, self.backup_dir, self.keep)
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:   # never let a failed backup kill the thread
                log.exception(t("log.backup_failed"))
            self._stop.wait(self.check_every)
