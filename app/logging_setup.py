"""File + console logging for the running server (not used by tests)."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "mezaniya.log", when="midnight", backupCount=30, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [file_handler, console]
