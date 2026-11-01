"""App configuration.

Values come from environment variables (MEZANIYA_*), or from a `mezaniya.env` file in
the project folder (KEY=VALUE lines, # comments). Real environment variables win.

Installed build (PyInstaller, sys.frozen): the program folder under Program Files is
read-only, so the env file and the data (database, secret key, backups, logs) live in
%LOCALAPPDATA%/Mezaniya instead.

Portable build: the same programs with a `portable.txt` file next to them. Then everything
lives in a `data` folder beside the programs, so the whole folder can be carried around.
"""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(__file__).resolve().parent.parent


PORTABLE_MARKER = "portable.txt"


def program_dir() -> Path:
    """The folder holding Mezaniya.exe and MezaniyaServer.exe (frozen builds)."""
    return Path(sys.executable).resolve().parent


def is_portable() -> bool:
    return FROZEN and (program_dir() / PORTABLE_MARKER).is_file()


def user_data_dir() -> Path:
    """data/ next to the programs when portable, %LOCALAPPDATA%/Mezaniya when installed,
    the project's data/ folder when run from the source."""
    if FROZEN:
        if is_portable():
            return program_dir() / "data"
        local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(local) / "Mezaniya"
    return BASE_DIR / "data"


def data_dir_problem(path: Path) -> str | None:
    """Why the data folder can't be used (e.g. a portable copy on a read-only drive or under
    Program Files), or None. Checked at startup so the user gets a clear message instead of
    a server that dies on its first write."""
    probe = Path(path) / ".write-test"
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return str(e)
    return None


ENV_FILE = Path(os.environ.get("MEZANIYA_ENV_FILE",
                               user_data_dir() / "mezaniya.env" if FROZEN else BASE_DIR / "mezaniya.env"))


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


PORTABLE = is_portable()


class Config:
    HOST = _env("HOST", "0.0.0.0")
    PORT = int(_env("PORT", 8080))
    THREADS = int(_env("THREADS", 8))
    DATA_DIR = Path(_env("DATA_DIR", user_data_dir()))
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
