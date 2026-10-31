"""Start the app with Waitress: `python run.py` (settings: mezaniya.env / MEZANIYA_* variables)."""
import logging
import socket

from waitress import serve

from app import create_app
from app.config import Config
from app.i18n import t
from app.logging_setup import setup_logging
from app.services.backup import BackupScheduler
from app.services.server_control import remove_pid_file, write_pid_file

log = logging.getLogger("mezaniya")


def lan_addresses() -> list[str]:
    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    # skip loopback and link-local (169.254.x.x: no DHCP lease, nobody can reach it)
    return sorted({a for a in addresses if not a.startswith(("127.", "169.254."))})


def main() -> None:
    Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging(Config.LOG_DIR)
    app = create_app()                      # applies pending DB migrations
    scheduler = None
    if Config.BACKUP_INTERVAL_HOURS > 0:
        scheduler = BackupScheduler(Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP,
                                    Config.BACKUP_INTERVAL_HOURS).start()

    log.info(t("log.started", port=Config.PORT))
    if Config.HOST in ("0.0.0.0", "::"):
        for ip in lan_addresses():
            log.info(t("log.network", ip=ip, port=Config.PORT))
    log.info(t("log.config", db=Config.DB_PATH, backups=Config.BACKUP_DIR, keep=Config.BACKUP_KEEP,
               hours=f"{Config.BACKUP_INTERVAL_HOURS:g}",
               registration=t("log.registration_open" if Config.ALLOW_REGISTRATION else "log.registration_closed")))
    write_pid_file(Config.PID_FILE, Config.PORT)
    try:
        serve(app, host=Config.HOST, port=Config.PORT, threads=Config.THREADS, ident="mezaniya")
    except Exception:
        log.exception(t("log.server_error", port=Config.PORT))
        raise
    finally:
        remove_pid_file(Config.PID_FILE)
        if scheduler:
            scheduler.stop()


if __name__ == "__main__":
    main()
