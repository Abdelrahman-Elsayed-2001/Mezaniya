"""Mezaniya server manager (tkinter). Run with manager.bat, or:

    .venv\\Scripts\\pythonw.exe server_manager.py

Start / stop / restart the server, see who is connected and log them out, create users
(including the demo user), change passwords, delete a user with all their data, back up,
and change the port. Texts come from app/i18n/strings.json; the layout mirrors for Arabic.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from app import i18n
from app.config import BASE_DIR, ENV_FILE, Config
from app.db.connection import connect, migrate
from app.services import admin, backup, demo
from app.services.server_control import ServerControl, ServerState

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
STATE_POLL_MS = 2000
USERS_POLL_MS = 5000
LOG_POLL_MS = 2000
LOG_TAIL_BYTES = 48_000
FIREWALL_CMD = ('New-NetFirewallRule -DisplayName "Mezaniya {port}" -Direction Inbound -Protocol TCP '
                '-LocalPort {port} -Action Allow -Profile Private')


def configured_port() -> int:
    value = admin.read_env_value(ENV_FILE, "MEZANIYA_PORT") or os.environ.get("MEZANIYA_PORT") or Config.PORT
    try:
        return admin.validate_port(value)
    except admin.AdminError:
        return Config.PORT


class Layout:
    """Side / anchor helpers so one layout code serves right-to-left and left-to-right."""

    def __init__(self, lang: str):
        self.rtl = i18n.direction(lang) == "rtl"
        self.start = "right" if self.rtl else "left"
        self.end = "left" if self.rtl else "right"
        self.anchor = "e" if self.rtl else "w"
        self.justify = "right" if self.rtl else "left"


class Dialog(tk.Toplevel):
    """Small modal form; subclasses add fields in body() and return a value from apply()."""

    def __init__(self, parent, title_key: str):
        super().__init__(parent)
        self.lang, self.ui = parent.lang, parent.ui
        self.title(self.t(title_key))
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        self.body(frame)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text=self.t("common.save"), command=self._ok).pack(side=self.ui.start)
        ttk.Button(buttons, text=self.t("common.cancel"), command=self.destroy).pack(side=self.ui.start, padx=8)
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.wait_visibility()
        self.grab_set()
        self.focus_first()

    def t(self, key: str, **params) -> str:
        return i18n.t(key, self.lang, **params)

    def field(self, parent, label_key: str, show: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=self.t(label_key), anchor=self.ui.anchor, font=FONT).pack(fill="x")
        entry = ttk.Entry(parent, width=32, justify=self.ui.justify, show=show or "", font=FONT)
        entry.pack(fill="x", pady=(2, 10))
        return entry

    def _ok(self):
        try:
            self.result = self.apply()
        except admin.AdminError as e:
            messagebox.showerror(self.t("manager.cannot"), e.message(self.lang), parent=self)
            return
        self.destroy()

    def body(self, frame): ...
    def apply(self): ...
    def focus_first(self): ...


class NewUserDialog(Dialog):
    def __init__(self, parent, conn):
        self.conn = conn
        super().__init__(parent, "manager.new_user")

    def body(self, frame):
        self.username = self.field(frame, "auth.username")
        self.password = self.field(frame, "manager.new_password_hint", show="•")
        self.confirm = self.field(frame, "auth.password_confirm", show="•")

    def focus_first(self):
        self.username.focus_set()

    def apply(self):
        if self.password.get() != self.confirm.get():
            raise admin.AdminError("auth.err.confirm_mismatch")
        admin.create_user(self.conn, self.username.get(), self.password.get(), self.lang)
        return self.username.get().strip()


class PasswordDialog(Dialog):
    def __init__(self, parent, conn, user: dict):
        self.conn, self.user = conn, user
        super().__init__(parent, "manager.change_password")

    def body(self, frame):
        ttk.Label(frame, text=self.user["username"], font=("Segoe UI", 12, "bold"),
                  anchor=self.ui.anchor).pack(fill="x", pady=(0, 10))
        self.password = self.field(frame, "auth.new_password", show="•")
        self.confirm = self.field(frame, "auth.password_confirm", show="•")
        self.temporary = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=self.t("manager.temporary_check"), variable=self.temporary).pack(anchor=self.ui.anchor)

    def focus_first(self):
        self.password.focus_set()

    def apply(self):
        if self.password.get() != self.confirm.get():
            raise admin.AdminError("auth.err.confirm_mismatch")
        return admin.set_password(self.conn, self.user["id"], self.password.get(), self.temporary.get())


class ManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.lang = i18n.default_language()
        self.ui = Layout(self.lang)
        self.geometry("1000x720")
        self.minsize(840, 560)
        self.option_add("*Font", FONT)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=26, font=FONT)
        style.configure("Treeview.Heading", font=FONT_BOLD)

        self.control = ServerControl(BASE_DIR, Config.PID_FILE)
        self.state = ServerState(False)
        self.busy = False
        self.tasks: queue.Queue = queue.Queue()
        self.users: list[dict] = []
        self._log_size = -1
        self.root_frame: ttk.Frame | None = None
        self.conn = connect(Config.DB_PATH)
        migrate(self.conn)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._drain_tasks)
        self._poll_state()
        self._poll_users()
        self._poll_log()

    def t(self, key: str, **params) -> str:
        return i18n.t(key, self.lang, **params)

    # ------------------------------------------------------------------ layout

    def switch_language(self):
        self.lang = next(code for code in i18n.languages() if code != self.lang)
        os.environ["MEZANIYA_LANGUAGE"] = self.lang
        admin.write_env_value(ENV_FILE, "MEZANIYA_LANGUAGE", self.lang)
        self.ui = Layout(self.lang)
        self._log_size = -1
        self._build()
        self._show_state(self.state)
        self.refresh_users()

    def _build(self):
        ui, t = self.ui, self.t
        if self.root_frame is not None:
            self.root_frame.destroy()
        self.title(t("manager.title"))
        root = self.root_frame = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # server
        srv = ttk.LabelFrame(root, text=f" {t('manager.server')} ", padding=10, labelanchor="n" + ui.anchor)
        srv.pack(fill="x")
        top = ttk.Frame(srv)
        top.pack(fill="x")
        self.status_dot = tk.Label(top, text="●", font=("Segoe UI", 16), fg="#9aa5b1")
        self.status_dot.pack(side=ui.start)
        self.status_text = ttk.Label(top, text="…", font=FONT_BOLD)
        self.status_text.pack(side=ui.start, padx=6)
        self.address = ttk.Label(top, text="", foreground="#0f766e", cursor="hand2")
        self.address.pack(side=ui.start, padx=16)
        self.address.bind("<Button-1>", lambda _e: self.open_browser())

        self.btn_lang = ttk.Button(top, text=t("lang.switch_to"), command=self.switch_language)
        self.btn_start = ttk.Button(top, text=t("manager.start"), command=self.start_server)
        self.btn_stop = ttk.Button(top, text=t("manager.stop"), command=self.stop_server)
        self.btn_restart = ttk.Button(top, text=t("manager.restart"), command=self.restart_server)
        self.btn_open = ttk.Button(top, text=t("manager.open"), command=self.open_browser)
        for b in (self.btn_lang, self.btn_open, self.btn_restart, self.btn_stop, self.btn_start):
            b.pack(side=ui.end, padx=3)

        port_row = ttk.Frame(srv)
        port_row.pack(fill="x", pady=(10, 0))
        ttk.Label(port_row, text=t("manager.port")).pack(side=ui.start)
        self.port_var = tk.StringVar(value=str(configured_port()))
        ttk.Entry(port_row, textvariable=self.port_var, width=8, justify="center").pack(side=ui.start, padx=6)
        ttk.Button(port_row, text=t("manager.save_port"), command=self.save_port).pack(side=ui.start)
        self.port_note = ttk.Label(port_row, text="", foreground="#b54708")
        self.port_note.pack(side=ui.start, padx=12)

        # users
        usr = ttk.LabelFrame(root, text=f" {t('manager.users')} ", padding=10, labelanchor="n" + ui.anchor)
        usr.pack(fill="both", expand=True, pady=10)
        bar = ttk.Frame(usr)
        bar.pack(fill="x")
        for key, command in (("manager.new_user", self.new_user), ("manager.change_password", self.change_password),
                             ("manager.logout_user", self.logout_selected), ("manager.delete_user", self.delete_selected)):
            ttk.Button(bar, text=t(key), command=command).pack(side=ui.start, padx=3)
        ttk.Button(bar, text=t("manager.logout_all"), command=self.logout_everyone).pack(side=ui.end, padx=3)
        ttk.Button(bar, text=t("manager.demo", name=demo.DEMO_USERNAME), command=self.create_demo).pack(side=ui.end, padx=3)

        info = ttk.Frame(usr)
        info.pack(fill="x", pady=(6, 0))
        self.online_label = ttk.Label(info, text="")
        self.online_label.pack(side=ui.start)
        ttk.Label(info, text=t("manager.online_hint", duration=i18n.plural("count.minutes", admin.ONLINE_MINUTES, self.lang)),
                  foreground="#7b8794").pack(side=ui.start, padx=10)

        cols = ["username", "state", "last_seen", "last_login", "created", "months"]
        if ui.rtl:
            cols.reverse()                  # Treeview lays columns out left→right
        widths = {"username": 180, "state": 190, "last_seen": 140, "last_login": 140, "created": 140, "months": 90}
        table = ttk.Frame(usr)
        table.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=t(f"manager.col_{c}"), anchor=ui.anchor)
            self.tree.column(c, width=widths[c], anchor=ui.anchor, stretch=c == "username")
        self.tree.tag_configure("online", foreground="#067647")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=ui.end, fill="y")
        self.tree.pack(side=ui.start, fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.change_password())
        self.columns = cols

        # backup
        bk = ttk.LabelFrame(root, text=f" {t('manager.backup')} ", padding=10, labelanchor="n" + ui.anchor)
        bk.pack(fill="x")
        ttk.Button(bk, text=t("manager.backup_now"), command=self.backup_now).pack(side=ui.start, padx=3)
        ttk.Button(bk, text=t("manager.open_backups"), command=self.open_backups).pack(side=ui.start, padx=3)
        # caption and the (Latin) file name in separate labels: Tk lays mixed text out left-to-right
        ttk.Label(bk, text=t("manager.last_backup")).pack(side=ui.start, padx=(16, 6))
        self.backup_label = ttk.Label(bk, text="", foreground="#52606d")
        self.backup_label.pack(side=ui.start)

        # log
        lg = ttk.LabelFrame(root, text=f" {t('manager.log')} ", padding=6, labelanchor="n" + ui.anchor)
        lg.pack(fill="both", expand=False, pady=(10, 0))
        self.log = tk.Text(lg, height=8, wrap="none", font=("Consolas", 9), state="disabled",
                           background="#0f1419", foreground="#d6dde4", relief="flat")
        self.log.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ background work

    def _drain_tasks(self):
        try:
            while True:
                fn, args = self.tasks.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._drain_tasks)

    def run_bg(self, work, done=None):
        """Run blocking work off the UI thread; `done(result)` runs back on it."""
        def target():
            try:
                result, error = work(), None
            except Exception as e:      # surfaced to the operator instead of dying silently
                result, error = None, e
            self.tasks.put((self._finish_bg, (done, result, error)))
        self._set_busy(True)
        threading.Thread(target=target, daemon=True).start()

    def _finish_bg(self, done, result, error):
        self._set_busy(False)
        if error is not None:
            message = error.message(self.lang) if isinstance(error, admin.AdminError) else str(error)
            messagebox.showerror(self.t("manager.error"), message, parent=self)
        elif done:
            done(result)
        self._poll_state(once=True)

    def _set_busy(self, busy: bool):
        self.busy = busy
        self._refresh_buttons()

    # ------------------------------------------------------------------ polling

    def _poll_state(self, once: bool = False):
        def work():
            self.tasks.put((self._show_state, (self.control.state(configured_port()),)))
        threading.Thread(target=work, daemon=True).start()
        if not once:
            self.after(STATE_POLL_MS, self._poll_state)

    def _show_state(self, st: ServerState):
        self.state = st
        if st.running and st.healthy:
            self.status_dot.configure(fg="#12b76a")
            self.status_text.configure(text=self.t("manager.state_running"))
            self.address.configure(text=f"http://localhost:{st.port}")
        elif st.running:
            self.status_dot.configure(fg="#f79009")
            self.status_text.configure(text=self.t("manager.state_starting"))
            self.address.configure(text="")
        else:
            self.status_dot.configure(fg="#9aa5b1")
            self.status_text.configure(text=self.t("manager.state_stopped"))
            self.address.configure(text="")
        configured = configured_port()
        if st.running and st.port and st.port != configured:
            self.port_note.configure(text=self.t("manager.port_pending", running=st.port, configured=configured))
        else:
            self.port_note.configure(text="")
        self._refresh_buttons()

    def _refresh_buttons(self):
        running = self.state.running
        self.btn_start.configure(state="disabled" if self.busy or running else "normal")
        self.btn_stop.configure(state="normal" if running and not self.busy else "disabled")
        self.btn_restart.configure(state="normal" if running and not self.busy else "disabled")
        self.btn_open.configure(state="normal" if self.state.healthy else "disabled")

    def _poll_users(self):
        self.refresh_users()
        self.after(USERS_POLL_MS, self._poll_users)

    def refresh_users(self):
        selected = self.selected_user()
        self.users = admin.list_users(self.conn)
        self.tree.delete(*self.tree.get_children())
        dash = self.t("common.dash")
        for u in self.users:
            state = self.t("manager.online") if u["online"] else dash
            if u["must_change_password"]:
                state += " · " + self.t("manager.temp_password")
            row = {"username": u["username"], "state": state, "last_seen": u["last_seen_at"] or dash,
                   "last_login": u["last_login_at"] or dash, "created": u["created_at"], "months": u["months"]}
            self.tree.insert("", "end", iid=str(u["id"]), tags=("online",) if u["online"] else (),
                             values=[row[c] for c in self.columns])
        if selected and self.tree.exists(str(selected["id"])):
            self.tree.selection_set(str(selected["id"]))
        online = sum(u["online"] for u in self.users)
        self.online_label.configure(text=self.t("manager.online_count", online=online, total=len(self.users)))
        files = backup.list_backups(Config.BACKUP_DIR)
        self.backup_label.configure(text=files[-1].name if files else dash)

    def _poll_log(self):
        path = Config.LOG_DIR / "mezaniya.log"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size != self._log_size:
            self._log_size = size
            text = ""
            if size:
                with open(path, "rb") as f:
                    f.seek(max(0, size - LOG_TAIL_BYTES))
                    text = f.read().decode("utf-8", errors="replace")
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(LOG_POLL_MS, self._poll_log)

    # ------------------------------------------------------------------ server

    def start_server(self):
        port = configured_port()

        def work():
            self.control.start(port)
            return self.control.wait_until_healthy(port)

        def done(ok):
            if not ok:
                messagebox.showerror(self.t("manager.start_failed_title"), self.t("manager.start_failed", port=port),
                                     parent=self)
        self.run_bg(work, done)

    def stop_server(self, then=None):
        def done(ok):
            if not ok:
                messagebox.showerror(self.t("manager.error"), self.t("manager.stop_failed"), parent=self)
            elif then:
                then()
        port = self.state.port or configured_port()
        self.run_bg(lambda: self.control.stop(port=port), done)

    def restart_server(self):
        self.stop_server(then=self.start_server)

    def open_browser(self):
        if self.state.healthy:
            webbrowser.open(f"http://localhost:{self.state.port}")

    def save_port(self):
        try:
            port = admin.validate_port(self.port_var.get())
        except admin.AdminError as e:
            messagebox.showerror(self.t("manager.port_invalid"), e.message(self.lang), parent=self)
            return
        admin.write_env_value(ENV_FILE, "MEZANIYA_PORT", str(port))
        command = FIREWALL_CMD.format(port=port)
        self.clipboard_clear()
        self.clipboard_append(command)
        msg = self.t("manager.port_saved", port=port, command=command)
        if self.state.running and self.state.port != port:
            if messagebox.askyesno(self.t("manager.port_saved_title"), msg + "\n\n" + self.t("manager.port_restart_now"),
                                   parent=self):
                self.restart_server()
        else:
            messagebox.showinfo(self.t("manager.port_saved_title"), msg, parent=self)
        self._poll_state(once=True)

    # ------------------------------------------------------------------ users

    def selected_user(self) -> dict | None:
        sel = self.tree.selection() if hasattr(self, "tree") else ()
        if not sel:
            return None
        return next((u for u in self.users if str(u["id"]) == sel[0]), None)

    def require_user(self) -> dict | None:
        user = self.selected_user()
        if user is None:
            messagebox.showinfo(self.t("manager.pick_user_title"), self.t("manager.pick_user"), parent=self)
        return user

    def new_user(self):
        dlg = NewUserDialog(self, self.conn)
        self.wait_window(dlg)
        if dlg.result:
            self.refresh_users()
            messagebox.showinfo(self.t("manager.done"), self.t("manager.user_created", name=dlg.result), parent=self)

    def create_demo(self):
        name = demo.DEMO_USERNAME
        replace = False
        if any(u["username"].casefold() == name for u in self.users):
            if not messagebox.askyesno(self.t("manager.demo", name=name), self.t("manager.demo_replace", name=name),
                                       icon="warning", parent=self):
                return
            replace = True

        def done(_user_id):
            self.refresh_users()
            messagebox.showinfo(self.t("manager.done"), self.t(
                "manager.demo_created", name=name, password=demo.DEMO_PASSWORD,
                months=i18n.plural("count.months", demo.DEMO_MONTHS, self.lang)), parent=self)
        lang = self.lang

        def work():
            conn = connect(Config.DB_PATH)          # its own connection: this runs on a worker thread
            try:
                return demo.create_demo_user(conn, lang, replace=replace)
            finally:
                conn.close()
        self.run_bg(work, done)

    def change_password(self):
        user = self.require_user()
        if not user:
            return
        dlg = PasswordDialog(self, self.conn, user)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self.refresh_users()
        text = self.t("manager.password_changed", name=user["username"])
        if dlg.result.startswith("#"):
            text += "\n\n" + self.t("manager.password_temp_note", password=dlg.result)
        messagebox.showinfo(self.t("manager.done"), text, parent=self)

    def logout_selected(self):
        user = self.require_user()
        if user and messagebox.askyesno(self.t("manager.logout_user_title"),
                                        self.t("manager.logout_user_confirm", name=user["username"]), parent=self):
            admin.logout_user(self.conn, user["id"])
            messagebox.showinfo(self.t("manager.done"), self.t("manager.logout_user_done", name=user["username"]),
                                parent=self)

    def logout_everyone(self):
        if messagebox.askyesno(self.t("manager.logout_all_title"), self.t("manager.logout_all_confirm"), parent=self):
            n = admin.logout_all(self.conn)
            messagebox.showinfo(self.t("manager.done"), self.t(
                "manager.logout_all_done", users=i18n.plural("count.users", n, self.lang)), parent=self)

    def delete_selected(self):
        user = self.require_user()
        if not user:
            return
        months = i18n.plural("count.months", user["months"], self.lang)
        if not messagebox.askyesno(self.t("manager.delete_title"),
                                   self.t("manager.delete_confirm", name=user["username"], months=months),
                                   icon="warning", parent=self):
            return
        typed = simpledialog.askstring(self.t("manager.delete_confirm_title"),
                                       self.t("manager.delete_type_name", name=user["username"]), parent=self)
        if typed is None:
            return
        if typed.strip().casefold() != user["username"].casefold():
            messagebox.showerror(self.t("manager.delete_mismatch_title"), self.t("manager.delete_mismatch"), parent=self)
            return
        safety = admin.delete_user(self.conn, user["id"], Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP)
        self.refresh_users()
        messagebox.showinfo(self.t("manager.deleted_title"),
                            self.t("manager.deleted", name=user["username"], file=safety.name), parent=self)

    # ------------------------------------------------------------------ backup

    def backup_now(self):
        def done(path: Path):
            self.refresh_users()
            messagebox.showinfo(self.t("manager.done"), self.t("manager.backup_done", path=path), parent=self)
        self.run_bg(lambda: backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP), done)

    def open_backups(self):
        Config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(Config.BACKUP_DIR)

    # ------------------------------------------------------------------ closing

    def on_close(self):
        if self.state.running:
            answer = messagebox.askyesnocancel(self.t("manager.close_title"), self.t("manager.close_body"), parent=self)
            if answer is None:
                return
            if answer:
                self.control.stop(port=self.state.port or configured_port())
        self.conn.close()
        self.destroy()


def main():
    ManagerApp().mainloop()


if __name__ == "__main__":
    main()
