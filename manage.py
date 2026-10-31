"""Admin commands. Run from the project folder (messages follow MEZANIYA_LANGUAGE, or --lang):

    python manage.py list-users
    python manage.py reset-password <username> [--password "#temp123#"]
    python manage.py logout-all
    python manage.py delete-user <username> --yes   (backs up first)
    python manage.py demo [--replace]                (user "test" / "test123" with three months of data)
    python manage.py backup
    python manage.py list-backups
    python manage.py restore <backup-file> --yes      (stop the server first)
    python manage.py migrate
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from app import i18n
from app.auth.passwords import is_manual
from app.config import Config
from app.db.connection import connect, migrate
from app.db.repo import users
from app.services import admin, backup, demo

LANG = i18n.default_language()


def t(key: str, **params) -> str:
    return i18n.t(key, LANG, **params)


def say(key: str, error: bool = False, **params) -> None:
    print(t(key, **params), file=sys.stderr if error else sys.stdout)


def _conn():
    conn = connect(Config.DB_PATH)
    migrate(conn)
    return conn


def cmd_list_users(_args) -> int:
    conn = _conn()
    rows = conn.execute("SELECT id, username, datetime(created_at, 'localtime') AS created_at,"
                        " datetime(last_login_at, 'localtime') AS last_login_at, password_hash FROM users ORDER BY id")
    for r in rows:
        flag = t("cli.must_change_flag") if is_manual(r["password_hash"]) else ""
        say("cli.user_line", id=f"{r['id']:>4}", username=f"{r['username']:<24}", created=r["created_at"],
            last_login=r["last_login_at"] or t("common.dash"), flag=flag)
    return 0


def cmd_reset_password(args) -> int:
    """Same as editing users.password_hash by hand: sets a #temporary# password that
    must be typed with its # marks and has to be changed right after logging in."""
    conn = _conn()
    user = users.find_by_username(conn, args.username)
    if user is None:
        say("cli.no_user", error=True, name=args.username)
        return 1
    temp = args.password or secrets.token_urlsafe(6)
    if not is_manual(temp):
        temp = f"#{temp.strip('#')}#"
    users.set_password_hash(conn, user["id"], temp)
    say("cli.temp_password", name=user["username"], password=temp)
    say("cli.temp_password_note")
    return 0


def cmd_logout_all(_args) -> int:
    n = admin.logout_all(_conn())
    say("cli.logged_out", users=i18n.plural("count.users", n, LANG))
    return 0


def cmd_delete_user(args) -> int:
    conn = _conn()
    user = users.find_by_username(conn, args.username)
    if user is None:
        say("cli.no_user", error=True, name=args.username)
        return 1
    if not args.yes:
        say("cli.delete_needs_yes", error=True, name=user["username"])
        return 2
    safety = admin.delete_user(conn, user["id"], Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP)
    say("cli.deleted", name=user["username"], path=safety)
    return 0


def cmd_demo(args) -> int:
    try:
        demo.create_demo_user(_conn(), LANG, replace=args.replace)
    except admin.AdminError:
        say("cli.demo_exists", error=True, name=demo.DEMO_USERNAME)
        return 1
    say("manager.demo_created", name=demo.DEMO_USERNAME, password=demo.DEMO_PASSWORD,
        months=i18n.plural("count.months", demo.DEMO_MONTHS, LANG))
    return 0


def cmd_backup(_args) -> int:
    path = backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP)
    say("cli.backup_written", path=path)
    return 0


def cmd_list_backups(_args) -> int:
    files = backup.list_backups(Config.BACKUP_DIR)
    if not files:
        say("cli.no_backups")
    for f in files:
        say("cli.backup_line", path=f, size=f"{f.stat().st_size / 1024:.0f}")
    return 0


def cmd_restore(args) -> int:
    source = Path(args.file)
    if not source.is_file():
        say("cli.file_missing", error=True, path=source)
        return 1
    if not args.yes:
        say("cli.restore_needs_yes", error=True)
        return 2
    safety = backup.restore(Config.DB_PATH, source, Config.BACKUP_DIR, Config.BACKUP_KEEP)
    say("cli.restored", db=Config.DB_PATH, file=source)
    if safety:
        say("cli.previous_saved", path=safety)
    return 0


def cmd_migrate(_args) -> int:
    conn = connect(Config.DB_PATH)
    applied = migrate(conn)
    if applied:
        say("cli.migrations_applied", versions=", ".join(map(str, applied)))
    else:
        say("cli.db_up_to_date")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manage.py", description=t("cli.description"))
    parser.add_argument("--lang", choices=i18n.languages(), help=t("cli.help_lang"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help=t("cli.help_list_users")).set_defaults(func=cmd_list_users)
    p = sub.add_parser("reset-password", help=t("cli.help_reset_password"))
    p.add_argument("username")
    p.add_argument("--password", help=t("cli.help_password"))
    p.set_defaults(func=cmd_reset_password)
    sub.add_parser("logout-all", help=t("cli.help_logout_all")).set_defaults(func=cmd_logout_all)
    p = sub.add_parser("delete-user", help=t("cli.help_delete_user"))
    p.add_argument("username")
    p.add_argument("--yes", action="store_true", help=t("cli.help_yes"))
    p.set_defaults(func=cmd_delete_user)
    p = sub.add_parser("demo", help=t("cli.help_demo"))
    p.add_argument("--replace", action="store_true", help=t("cli.help_replace"))
    p.set_defaults(func=cmd_demo)
    sub.add_parser("backup", help=t("cli.help_backup")).set_defaults(func=cmd_backup)
    sub.add_parser("list-backups", help=t("cli.help_list_backups")).set_defaults(func=cmd_list_backups)
    p = sub.add_parser("restore", help=t("cli.help_restore"))
    p.add_argument("file")
    p.add_argument("--yes", action="store_true", help=t("cli.help_yes"))
    p.set_defaults(func=cmd_restore)
    sub.add_parser("migrate", help=t("cli.help_migrate")).set_defaults(func=cmd_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    global LANG
    args = build_parser().parse_args(argv)
    LANG = i18n.normalize(args.lang) if args.lang else i18n.default_language()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
