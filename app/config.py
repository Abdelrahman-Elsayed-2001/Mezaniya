"""App configuration.

Values come from environment variables (MEZANIYA_*), or from a `mezaniya.env` file in
the project folder (KEY=VALUE lines, # comments). Real environment variables win.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("MEZANIYA_ENV_FILE", BASE_DIR / "mezaniya.env"))


def load_env_file(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines into os.environ without overriding variables already set."""
    loaded = {}
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


load_env_file(ENV_FILE)


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default) -> str:
    return os.environ.get(f"MEZANIYA_{name}", str(default))


class Config:
    HOST = _env("HOST", "0.0.0.0")
    PORT = int(_env("PORT", 8080))
    THREADS = int(_env("THREADS", 8))
    DATA_DIR = Path(_env("DATA_DIR", BASE_DIR / "data"))
    DB_PATH = Path(_env("DB_PATH", DATA_DIR / "mezaniya.db"))
    ALLOW_REGISTRATION = _bool(_env("ALLOW_REGISTRATION", "true"))
    # empty → generated once and stored in DATA_DIR/secret_key
    SECRET_KEY = os.environ.get("MEZANIYA_SECRET_KEY") or None
    SESSION_DAYS = int(_env("SESSION_DAYS", 30))
    LOGIN_MAX_ATTEMPTS = int(_env("LOGIN_MAX_ATTEMPTS", 5))
    LOGIN_LOCK_SECONDS = int(_env("LOGIN_LOCK_SECONDS", 300))
    BACKUP_DIR = Path(_env("BACKUP_DIR", DATA_DIR / "backups"))
    BACKUP_KEEP = int(_env("BACKUP_KEEP", 14))                 # how many backup files to keep
    BACKUP_INTERVAL_HOURS = float(_env("BACKUP_INTERVAL_HOURS", 24))
    LOG_DIR = Path(_env("LOG_DIR", DATA_DIR / "logs"))
    PID_FILE = DATA_DIR / "server.pid"          # lets the server manager find a running server
    # default language (ar / en) for the web app before a visitor picks one, logs, manager and CLI
    LANGUAGE = _env("LANGUAGE", "ar")
