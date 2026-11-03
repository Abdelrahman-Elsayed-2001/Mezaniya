"""Mezaniya server manager (tkinter). Run with manager.bat, or:

    .venv\\Scripts\\pythonw.exe server_manager.py

Installed from the setup, this file is Mezaniya.exe and starts MezaniyaServer.exe (see
packaging/mezaniya.spec); data then lives in %LOCALAPPDATA%/Mezaniya, or in data/ next to
the programs for the portable build.

Start / stop / restart the server, see who is connected and log them out, create users
(including the demo user), change passwords, delete a user with all their data, back up,
and change the port. The second tab adds languages and edits any text (in languages.db). Texts come from app/i18n/languages/*.json; the layout mirrors for Arabic.
"""
from __future__ import annotations

import os
import queue
import re
import sqlite3
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from app import i18n
from app.version import STAGE, __version__
from app.config import BASE_DIR, ENV_FILE, Config, data_dir_problem
from app.db.connection import connect, migrate
from app.i18n import store as language_store
from app.services import admin, backup, data_files, demo
from app.services import languages as langs
from app.services.server_control import ServerControl, ServerState

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
STATE_POLL_MS = 2000
USERS_POLL_MS = 5000
LOG_POLL_MS = 2000
LOG_TAIL_BYTES = 48_000
FIREWALL_CMD = ('New-NetFirewallRule -DisplayName "Mezaniya {port}" -Direction Inbound -Protocol TCP '
                '-LocalPort {port} -Action Allow -Profile Private')


# Windows virtual-key codes of C, V, X, A: with an Arabic keyboard layout Ctrl+V arrives as Ctrl+<Arabic
# letter>, which Tk's own copy/paste bindings don't recognise
CLIPBOARD_KEYS = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}
EDIT_CLASSES = ("Entry", "TEntry", "Text", "TCombobox")


def _select_all(widget) -> None:
    if isinstance(widget, tk.Text):
        widget.tag_add("sel", "1.0", "end-1c")
    else:
        widget.select_range(0, "end")
        widget.icursor("end")


def _edit_event(widget, event: str) -> None:
    if event == "<<SelectAll>>":
        _select_all(widget)
        return
    widget.event_generate(event)
    if isinstance(widget, tk.Text) and "dir" in widget.tag_names():
        widget.tag_add("dir", "1.0", "end")


def install_edit_shortcuts(root: tk.Misc, labels: dict[str, str]) -> None:
    """Ctrl+C/V/X/A whatever the keyboard layout, and a right-click menu, in every text field."""
    def shortcut(event):
        if event.keysym.lower() in ("c", "v", "x", "a") or event.keycode not in CLIPBOARD_KEYS:
            if event.keysym.lower() == "a":
                _select_all(event.widget)
                return "break"
            return None
        _edit_event(event.widget, CLIPBOARD_KEYS[event.keycode])
        return "break"

    def menu(event):
        widget = event.widget
        widget.focus_set()
        popup = tk.Menu(widget, tearoff=False)
        for event_name, label in (("<<Cut>>", labels["cut"]), ("<<Copy>>", labels["copy"]),
                                  ("<<Paste>>", labels["paste"]), ("<<SelectAll>>", labels["select_all"])):
            popup.add_command(label=label, command=lambda e=event_name: _edit_event(widget, e))
        popup.tk_popup(event.x_root, event.y_root)

    for cls in EDIT_CLASSES:
        root.bind_class(cls, "<Control-KeyPress>", shortcut, add="+")
        root.bind_class(cls, "<Button-3>", menu, add="+")


# Tk draws every line left-to-right, so in Arabic a line with Latin text or {placeholders} comes out
# in the wrong order. Wrapping each line in RIGHT-TO-LEFT EMBEDDING … POP DIRECTIONAL FORMATTING makes
# Windows lay it out right-to-left. The marks are display-only: plain() takes them off what is typed.
RLE, PDF = "\u202b", "\u202c"
BIDI_MARKS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def directed(text: str, rtl: bool) -> str:
    if not rtl or not text:
        return text
    return "\n".join(f"{RLE}{line}{PDF}" if line else line for line in plain(text).split("\n"))


def plain(text: str) -> str:
    return BIDI_MARKS.sub("", text)


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

    def text(self, value: str) -> str:
        return directed(value, self.rtl)


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
        return self.ui.text(i18n.t(key, self.lang, **params))

    def field(self, parent, label_key: str, show: str | None = None) -> ttk.Entry:
        ttk.Label(parent, text=self.t(label_key), anchor=self.ui.anchor, font=FONT).pack(fill="x")
        entry = ttk.Entry(parent, width=32, justify=self.ui.justify, show=show or "", font=FONT)
        entry.pack(fill="x", pady=(2, 10))
        return entry

    def _ok(self):
        try:
            self.result = self.apply()
        except admin.AdminError as e:
            messagebox.showerror(self.t("manager.cannot"), self.ui.text(e.message(self.lang)), parent=self)
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


def lang_label(lang: dict) -> str:
    return directed(f"{lang['name']} ({lang['code']})", lang["dir"] == "rtl")


class LanguageDialog(Dialog):
    """Add a language, or change one's details (a built-in one: only its reference language)."""

    def __init__(self, parent, conn, lang: dict | None = None):
        self.conn, self.lang_row = conn, lang
        self.others = [x for x in langs.languages(conn) if not lang or x["code"] != lang["code"]]
        super().__init__(parent, "manager.edit_language" if lang else "manager.add_language")

    def combo(self, parent, label_key: str, labels: list[str], current: int) -> ttk.Combobox:
        ttk.Label(parent, text=self.t(label_key), anchor=self.ui.anchor, font=FONT).pack(fill="x")
        box = ttk.Combobox(parent, values=labels, state="readonly", width=44, font=FONT)
        box.current(current)
        box.pack(fill="x", pady=(2, 10))
        return box

    def body(self, frame):
        codes = [x["code"] for x in self.others]
        lang = self.lang_row or {"code": "", "name": "", "dir": "ltr", "plural": "en", "system": False,
                                 "reference": "en" if "en" in codes else i18n.DEFAULT_LANGUAGE}
        self.code = self.field(frame, "manager.lang_code")
        self.code.insert(0, lang["code"])
        self.name = self.field(frame, "manager.lang_name")
        self.name.insert(0, lang["name"])
        self.dirs = ["rtl", "ltr"]
        self.dir = self.combo(frame, "manager.lang_dir", [self.t(f"manager.dir_{d}") for d in self.dirs],
                              self.dirs.index(lang["dir"]))
        self.rules = list(langs.PLURAL_FORMS)
        self.plural = self.combo(frame, "manager.lang_plural", [self.t(f"manager.plural_{r}") for r in self.rules],
                                 self.rules.index(lang["plural"]))
        self.refs = [None] + [x["code"] for x in self.others]
        labels = [self.t("manager.no_reference")] + [lang_label(x) for x in self.others]
        self.reference = self.combo(frame, "manager.lang_reference", labels,
                                    self.refs.index(lang["reference"]) if lang["reference"] in self.refs else 0)
        if self.lang_row:
            self.code.configure(state="disabled")
        if lang["system"]:
            for widget in (self.name, self.dir, self.plural):
                widget.configure(state="disabled")
            ttk.Label(frame, text=self.t("manager.lang_from_file"), foreground="#52606d", wraplength=380,
                      justify=self.ui.justify, anchor=self.ui.anchor).pack(fill="x")

    def focus_first(self):
        (self.name if self.lang_row else self.code).focus_set()

    def apply(self):
        args = (plain(self.name.get()), self.dirs[self.dir.current()], self.rules[self.plural.current()],
                self.refs[self.reference.current()])
        if self.lang_row:
            langs.update_language(self.conn, self.lang_row["code"], *args)
            return self.lang_row["code"]
        return langs.add_language(self.conn, plain(self.code.get()), *args)


class TextDialog(Dialog):
    """Edit one text of one language: the reference text above, the translation (or plural forms) below."""

    def __init__(self, parent, conn, target: dict, reference: dict | None, row: dict):
        self.conn, self.target, self.reference, self.row = conn, target, reference, row
        super().__init__(parent, "manager.edit_text")

    def text_box(self, parent, value: str, height: int, read_only: bool, rtl: bool) -> tk.Text:
        box = tk.Text(parent, width=60, height=height, wrap="word", font=FONT, relief="solid", borderwidth=1,
                      background="#f3f4f6" if read_only else "white")
        box.tag_configure("dir", justify="right" if rtl else "left")
        box.insert("1.0", directed(value, rtl), "dir")
        box.bind("<KeyRelease>", lambda _e: box.tag_add("dir", "1.0", "end"))
        if read_only:
            box.configure(state="disabled")
        else:
            box.bind("<Return>", lambda _e: (self._ok(), "break")[1])
            box.bind("<Shift-Return>", lambda _e: (box.insert("insert", "\n"), "break")[1])
        box.pack(fill="x", pady=(2, 10))
        return box

    def caption(self, parent, text: str, **kw):
        ttk.Label(parent, text=text, anchor=self.ui.anchor, justify=self.ui.justify, wraplength=520,
                  font=kw.pop("font", FONT), **kw).pack(fill="x")

    def body(self, frame):
        row, t = self.row, self.t
        ttk.Label(frame, text=row["key"], font=("Consolas", 10), foreground="#52606d").pack(anchor=self.ui.anchor)
        if self.reference:
            self.caption(frame, t("manager.reference_text", name=self.reference["name"]), font=FONT_BOLD)
            self.text_box(frame, langs.as_line(row["reference"]) if row["plural"] else (row["reference"] or ""),
                          3, True, self.reference["dir"] == "rtl")
        if row["edited"] and row["original"] is not None:
            self.caption(frame, t("manager.original_text"), font=FONT_BOLD)
            self.text_box(frame, langs.as_line(row["original"]) if row["plural"] else row["original"],
                          2, True, self.target["dir"] == "rtl")
        self.caption(frame, t("manager.translation", name=self.target["name"]), font=FONT_BOLD)
        rtl = self.target["dir"] == "rtl"
        if row["plural"]:
            self.caption(frame, t("manager.plural_hint"), foreground="#52606d")
            value = row["value"] or {}
            self.forms = {}
            grid = ttk.Frame(frame)
            grid.pack(fill="x", pady=(4, 10))
            for i, form in enumerate(langs.PLURAL_FORMS[self.target["plural"]]):
                label = ttk.Label(grid, text=t(f"manager.plural_form_{form}"), font=FONT)
                entry = ttk.Entry(grid, width=44, justify="right" if rtl else "left", font=FONT)
                entry.insert(0, directed(value.get(form, ""), rtl))
                label.grid(row=i, column=1 if self.ui.rtl else 0, sticky=self.ui.anchor, padx=6, pady=2)
                entry.grid(row=i, column=0 if self.ui.rtl else 1, sticky="we", pady=2)
                self.forms[form] = entry
            grid.columnconfigure(0 if self.ui.rtl else 1, weight=1)
        else:
            self.box = self.text_box(frame, row["value"] or "", 4, False, rtl)
            names = langs.placeholders(row["reference"])
            if names:
                self.caption(frame, t("manager.placeholders_hint",
                                      names="  ".join("{" + n + "}" for n in sorted(names))), foreground="#b54708")
        self.caption(frame, t("manager.empty_hint"), foreground="#7b8794")
        if row["edited"] and row["original"] is not None:
            ttk.Button(frame, text=t("manager.restore_original"), command=self.restore).pack(anchor=self.ui.anchor,
                                                                                            pady=(8, 0))

    def focus_first(self):
        (next(iter(self.forms.values())) if self.row["plural"] else self.box).focus_set()

    def value(self):
        if self.row["plural"]:
            return {form: plain(entry.get()) for form, entry in self.forms.items()}
        return plain(self.box.get("1.0", "end-1c"))

    def apply(self):
        langs.save_text(self.conn, self.target["code"], self.row["key"], self.value())
        return True

    def restore(self):
        langs.save_text(self.conn, self.target["code"], self.row["key"], None)
        self.result = True
        self.destroy()


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
        self.lconn = language_store.open_db(Config.languages_db_path())
        self.text_target: str | None = None
        self.text_reference: str | None = None

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        install_edit_shortcuts(self, self.edit_labels)        # labels filled (per language) by _build
        self.after(100, self._drain_tasks)
        self._poll_state()
        self._poll_users()
        self._poll_log()

    def t(self, key: str, **params) -> str:
        return self.ui.text(i18n.t(key, self.lang, **params))

    # ------------------------------------------------------------------ layout

    def switch_language(self, code: str):
        if code == self.lang:
            return
        self.lang = code
        os.environ["MEZANIYA_LANGUAGE"] = self.lang
        admin.write_env_value(ENV_FILE, "MEZANIYA_LANGUAGE", self.lang)
        self.rebuild()

    def rebuild(self, tab: int = 0):
        self.ui = Layout(self.lang)
        self._log_size = -1
        self._build(tab)
        self._show_state(self.state)
        self.refresh_users()

    def _language_switch(self, parent):
        """Two languages: a button to the other one. More: a list of them all."""
        codes = i18n.languages()
        others = [code for code in codes if code != self.lang]
        if len(others) == 1:
            return ttk.Button(parent, text=i18n.language_name(others[0]),
                              command=lambda: self.switch_language(others[0]))
        if not others:
            return None
        names = [i18n.language_name(code) for code in codes]
        box = ttk.Combobox(parent, values=names, state="readonly", width=max(len(n) for n in names) + 2)
        box.set(i18n.language_name(self.lang))
        box.bind("<<ComboboxSelected>>", lambda _e: self.switch_language(codes[box.current()]))
        return box

    def _build(self, tab: int = 0):
        ui, t = self.ui, self.t
        if self.root_frame is not None:
            self.root_frame.destroy()
        self.title(f"{t('manager.title')} · v{__version__}{' ' + STAGE if STAGE else ''}")
        self.edit_labels = getattr(self, "edit_labels", {})
        self.edit_labels.update({k: t(f"manager.{k}") for k in ("cut", "copy", "paste", "select_all")})
        self.root_frame = ttk.Frame(self, padding=(12, 8, 12, 12))
        self.root_frame.pack(fill="both", expand=True)
        notebook = ttk.Notebook(self.root_frame)
        notebook.pack(fill="both", expand=True)
        root = ttk.Frame(notebook, padding=(0, 10, 0, 0))
        languages_tab = ttk.Frame(notebook, padding=(0, 10, 0, 0))
        notebook.add(root, text=f"  {t('manager.tab_server')}  ")
        notebook.add(languages_tab, text=f"  {t('manager.tab_languages')}  ")
        self._build_languages(languages_tab)
        notebook.select(tab)

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

        self.btn_lang = self._language_switch(top)
        self.btn_start = ttk.Button(top, text=t("manager.start"), command=self.start_server)
        self.btn_stop = ttk.Button(top, text=t("manager.stop"), command=self.stop_server)
        self.btn_restart = ttk.Button(top, text=t("manager.restart"), command=self.restart_server)
        self.btn_open = ttk.Button(top, text=t("manager.open"), command=self.open_browser)
        for b in (self.btn_lang, self.btn_open, self.btn_restart, self.btn_stop, self.btn_start):
            if b is not None:
                b.pack(side=ui.end, padx=3)

        port_row = ttk.Frame(srv)
        port_row.pack(fill="x", pady=(10, 0))
        ttk.Label(port_row, text=t("manager.port")).pack(side=ui.start)
        # the version on the far side; caption and (Latin) number in separate labels, as for backups
        ttk.Label(port_row, text=f"{__version__}{' ' + STAGE if STAGE else ''}", foreground="#52606d").pack(side=ui.end)
        ttk.Label(port_row, text=t("manager.version")).pack(side=ui.end, padx=6)
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

    def _tree(self, parent, cols: list[str], widths: dict[str, int], stretch: str, height: int) -> ttk.Treeview:
        """A Treeview whose columns run from the reading side, with a scrollbar."""
        ui = self.ui
        if ui.rtl:
            cols = list(reversed(cols))
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse", height=height)
        for c in cols:
            tree.heading(c, text=self.t(f"manager.col_{c}"), anchor=ui.anchor)
            tree.column(c, width=widths[c], anchor=ui.anchor, stretch=c == stretch)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=ui.end, fill="y")
        tree.pack(side=ui.start, fill="both", expand=True)
        tree.columns_order = cols
        return tree

    def _build_languages(self, tab):
        ui, t = self.ui, self.t
        box = ttk.LabelFrame(tab, text=f" {t('manager.languages')} ", padding=10, labelanchor="n" + ui.anchor)
        box.pack(fill="x")
        bar = ttk.Frame(box)
        bar.pack(fill="x")
        for key, command in (("manager.add_language", self.add_language),
                             ("manager.edit_language", self.edit_language),
                             ("manager.enable_language", lambda: self.toggle_language(True)),
                             ("manager.disable_language", lambda: self.toggle_language(False)),
                             ("manager.delete_language", self.delete_language)):
            ttk.Button(bar, text=t(key), command=command).pack(side=ui.start, padx=3)
        self.lang_tree = self._tree(
            box, ["code", "name", "dir", "lang_state", "kind", "progress", "reference"],
            {"code": 70, "name": 150, "dir": 160, "lang_state": 90, "kind": 90, "progress": 190, "reference": 150},
            "name", 4)
        self.lang_tree.bind("<Double-1>", lambda _e: self.edit_language())

        texts = ttk.LabelFrame(tab, text=f" {t('manager.texts')} ", padding=10, labelanchor="n" + ui.anchor)
        texts.pack(fill="both", expand=True, pady=(10, 0))
        filters = ttk.Frame(texts)
        filters.pack(fill="x")
        ttk.Label(filters, text=t("manager.target_label")).pack(side=ui.start)
        self.target_box = ttk.Combobox(filters, state="readonly", width=16)
        self.target_box.pack(side=ui.start, padx=(4, 14))
        ttk.Label(filters, text=t("manager.reference_label")).pack(side=ui.start)
        self.reference_box = ttk.Combobox(filters, state="readonly", width=16)
        self.reference_box.pack(side=ui.start, padx=(4, 14))
        ttk.Label(filters, text=t("manager.search_label")).pack(side=ui.start)
        self.search_var = tk.StringVar()
        ttk.Entry(filters, textvariable=self.search_var, width=18).pack(side=ui.start, padx=4)
        self.untranslated_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text=t("manager.untranslated_only"), variable=self.untranslated_var,
                        command=self.refresh_texts).pack(side=ui.start, padx=10)
        self.target_box.bind("<<ComboboxSelected>>", lambda _e: self._pick_target())
        self.reference_box.bind("<<ComboboxSelected>>", lambda _e: self._pick_reference())
        self._search_job = None
        self.search_var.trace_add("write", lambda *_: self._search_later())

        info = ttk.Frame(texts)
        info.pack(fill="x", pady=(6, 0))
        self.texts_count = ttk.Label(info, text="")
        self.texts_count.pack(side=ui.start)
        ttk.Button(info, text=t("manager.import_csv"), command=self.import_csv).pack(side=ui.end, padx=3)
        ttk.Button(info, text=t("manager.export_csv"), command=self.export_csv).pack(side=ui.end, padx=3)
        ttk.Label(info, text=t("manager.texts_hint"), foreground="#7b8794").pack(side=ui.start, padx=10)
        self.text_tree = self._tree(texts, ["key", "reference", "value"],
                                    {"key": 230, "reference": 330, "value": 330}, "value", 10)
        self.text_tree.heading("value", text=t("manager.translation", name="…"))
        self.text_tree.tag_configure("missing", foreground="#b54708")
        self.text_tree.tag_configure("edited", foreground="#1d4ed8")
        self.text_tree.bind("<Double-1>", lambda _e: self.edit_text())
        self.refresh_languages()

    # ------------------------------------------------------------------ languages

    def _language_rows(self) -> list[dict]:
        self.language_rows = langs.languages(self.lconn)
        return self.language_rows

    def _lang(self, code: str | None) -> dict | None:
        return next((x for x in self.language_rows if x["code"] == code), None)

    def refresh_languages(self):
        t, dash = self.t, self.t("common.dash")
        rows = self._language_rows()
        selected = self.lang_tree.selection()
        self.lang_tree.delete(*self.lang_tree.get_children())
        for x in rows:
            ref = self._lang(x["reference"])
            progress = t("manager.lang_progress", done=x["translated"], total=x["total"])
            if x["edited"] and x["system"]:
                progress += " · " + t("manager.lang_edited", n=x["edited"])
            values = {"code": x["code"], "name": x["name"], "dir": t(f"manager.dir_{x['dir']}"),
                      "lang_state": t("manager.lang_enabled" if x["enabled"] else "manager.lang_disabled"),
                      "kind": t("manager.lang_system" if x["system"] else "manager.lang_added"),
                      "progress": progress, "reference": ref["name"] if ref else dash}
            self.lang_tree.insert("", "end", iid=x["code"], values=[values[c] for c in self.lang_tree.columns_order],
                                  tags=() if x["enabled"] else ("missing",))
        self.lang_tree.tag_configure("missing", foreground="#7b8794")
        if selected and self.lang_tree.exists(selected[0]):
            self.lang_tree.selection_set(selected[0])
        codes = [x["code"] for x in rows]
        if self.text_target not in codes:
            self.text_target = next((c for c in codes if c != i18n.DEFAULT_LANGUAGE), codes[0])
        if self.text_reference not in codes or self.text_reference == self.text_target:
            target = self._lang(self.text_target)
            self.text_reference = target["reference"] if target["reference"] in codes else \
                next((c for c in codes if c != self.text_target), None)
        labels = [lang_label(x) for x in rows]
        self.target_box.configure(values=labels)
        self.target_box.current(codes.index(self.text_target))
        self.reference_box.configure(values=labels)
        if self.text_reference in codes:
            self.reference_box.current(codes.index(self.text_reference))
        self.refresh_texts()

    def _pick_target(self):
        self.text_target = self.language_rows[self.target_box.current()]["code"]
        target = self._lang(self.text_target)
        if target["reference"]:
            self.text_reference = target["reference"]
        self.refresh_languages()

    def _pick_reference(self):
        self.text_reference = self.language_rows[self.reference_box.current()]["code"]
        self.refresh_texts()

    def _search_later(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(300, self.refresh_texts)

    def refresh_texts(self):
        self._search_job = None
        target, reference = self._lang(self.text_target), self._lang(self.text_reference)
        if target is None:
            return
        self.text_tree.heading("value", text=lang_label(target))
        self.text_tree.heading("reference", text=lang_label(reference) if reference else self.t("manager.col_reference"))
        rows = langs.rows(self.lconn, self.text_reference or i18n.DEFAULT_LANGUAGE, self.text_target,
                          self.search_var.get(), self.untranslated_var.get())
        self.text_rows = {r["key"]: r for r in rows}
        tree = self.text_tree
        tree.delete(*tree.get_children())
        for r in rows:
            values = {"key": r["key"],
                      "reference": directed(langs.as_line(r["reference"])[:300], bool(reference) and reference["dir"] == "rtl"),
                      "value": directed(langs.as_line(r["value"])[:300], target["dir"] == "rtl")}
            tag = "missing" if r["value"] is None else "edited" if r["edited"] else ""
            tree.insert("", "end", iid=r["key"], values=[values[c] for c in tree.columns_order], tags=(tag,))
        self.texts_count.configure(text=self.t("manager.texts_count", shown=len(rows), done=target["translated"],
                                               total=target["total"]))

    def selected_language(self) -> dict | None:
        selection = self.lang_tree.selection()
        if not selection:
            messagebox.showinfo(self.t("manager.languages"), self.t("manager.pick_language"), parent=self)
            return None
        return self._lang(selection[0])

    def languages_changed(self, target: str | None = None):
        """Reload the texts everywhere (this window, and the running server within seconds)."""
        i18n.reload()
        if target:
            self.text_target = target
        if self.lang not in i18n.languages():
            self.lang = i18n.default_language()
        self.rebuild(tab=1)

    def add_language(self):
        dlg = LanguageDialog(self, self.lconn)
        self.wait_window(dlg)
        if dlg.result:
            self.text_reference = None
            self.languages_changed(dlg.result)
            messagebox.showinfo(self.t("manager.done"),
                                self.t("manager.language_added", name=self._lang(dlg.result)["name"]), parent=self)

    def edit_language(self):
        lang = self.selected_language()
        if lang:
            dlg = LanguageDialog(self, self.lconn, lang)
            self.wait_window(dlg)
            if dlg.result:
                self.languages_changed()

    def toggle_language(self, enabled: bool):
        lang = self.selected_language()
        if lang:
            try:
                langs.set_enabled(self.lconn, lang["code"], enabled)
            except admin.AdminError as e:
                messagebox.showerror(self.t("manager.cannot"), self.ui.text(e.message(self.lang)), parent=self)
                return
            self.languages_changed()

    def delete_language(self):
        lang = self.selected_language()
        if not lang:
            return
        if lang["system"]:
            messagebox.showerror(self.t("manager.cannot"), self.t("manager.lang_err.system"), parent=self)
            return
        if not messagebox.askyesno(self.t("manager.delete_language"),
                                   self.t("manager.delete_language_confirm", name=lang["name"]),
                                   icon="warning", parent=self):
            return
        langs.delete_language(self.lconn, lang["code"])
        self.languages_changed()

    def export_csv(self):
        target = self._lang(self.text_target)
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".xlsx", initialfile=f"mezaniya-{target['code']}.xlsx",
                                            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not path:
            return
        try:
            rows = langs.export_texts(self.lconn, path, target["code"], self.text_reference)
        except OSError as e:
            messagebox.showerror(self.t("manager.cannot"), str(e), parent=self)
            return
        messagebox.showinfo(self.t("manager.done"),
                            self.t("manager.csv_exported", rows=rows, path=path, code=target["code"]), parent=self)

    def import_csv(self):
        target = self._lang(self.text_target)
        path = filedialog.askopenfilename(parent=self, filetypes=[("Excel / CSV", "*.xlsx *.csv"), ("*", "*")])
        if not path:
            return
        try:
            result = langs.import_texts(self.lconn, path, target["code"])
        except admin.AdminError as e:
            messagebox.showerror(self.t("manager.cannot"), self.ui.text(e.message(self.lang)), parent=self)
            return
        except OSError as e:
            messagebox.showerror(self.t("manager.cannot"), str(e), parent=self)
            return
        message = self.t("manager.csv_imported", saved=result["saved"], unchanged=result["unchanged"])
        errors = result["errors"]
        if errors:
            shown = [f"{key}: {e.message(self.lang)}" for key, e in errors[:12]]
            if len(errors) > 12:
                shown.append(i18n.t("manager.csv_more", self.lang, n=len(errors) - 12))
            message += "\n\n" + self.t("manager.csv_refused", n=len(errors)) + "\n" + "\n".join(shown)
        i18n.reload()
        self.refresh_languages()
        (messagebox.showwarning if errors else messagebox.showinfo)(self.t("manager.done"), message, parent=self)

    def edit_text(self):
        selection = self.text_tree.selection()
        if not selection:
            return
        row = self.text_rows[selection[0]]
        dlg = TextDialog(self, self.lconn, self._lang(self.text_target), self._lang(self.text_reference), row)
        self.wait_window(dlg)
        if dlg.result:
            i18n.reload()
            self.refresh_languages()
            if self.text_tree.exists(row["key"]):
                self.text_tree.selection_set(row["key"])
                self.text_tree.see(row["key"])

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
            messagebox.showerror(self.t("manager.port_invalid"), self.ui.text(e.message(self.lang)), parent=self)
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
        self.run_bg(lambda: backup.backup_now(Config.DB_PATH, Config.BACKUP_DIR, Config.BACKUP_KEEP,
                                                   languages_path=Config.languages_db_path()), done)

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
        self.lconn.close()
        self.destroy()


class IncompatibleWindow(tk.Toplevel):
    """A data file this version can't open: delete it, rename it, or (when app.db.upgrades has fix
    scripts for its format) upgrade it. `done` is False when the user closes without a choice."""

    def __init__(self, root, problem: data_files.Problem, lang: str):
        super().__init__(root)
        self.problem, self.lang, self.ui, self.done = problem, lang, Layout(lang), False
        self.title(self.t("manager.incompatible_title"))
        self.resizable(False, False)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        intro = "manager.incompatible_data" if problem.kind == data_files.DATA else "manager.incompatible_languages"
        lines = [self.t(intro, path=problem.path, found=problem.found, expected=problem.expected),
                 self.t("manager.incompatible_choose")]
        if problem.upgradable:
            lines.append(self.t("manager.incompatible_upgradable"))
        ttk.Label(frame, text="\n\n".join(lines), wraplength=460, justify=self.ui.justify, anchor=self.ui.anchor,
                  font=FONT).pack(fill="x", pady=(0, 14))
        actions = [("manager.incompatible_rename", self.rename), ("manager.incompatible_delete", self.delete)]
        if problem.upgradable:
            actions.insert(0, ("manager.incompatible_upgrade", self.upgrade))
        for key, command in actions:
            ttk.Button(frame, text=self.t(key), command=command).pack(fill="x", pady=3)
        ttk.Button(frame, text=self.t("manager.incompatible_quit"), command=self.destroy).pack(fill="x", pady=(12, 0))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.lift()
        self.focus_force()

    def t(self, key: str, **params) -> str:
        return self.ui.text(i18n.t(key, self.lang, **params))

    def _run(self, action, message_key: str | None = None):
        try:
            result = action(self.problem)
        except (OSError, sqlite3.Error, ValueError) as e:
            messagebox.showerror(self.t("manager.cannot"), str(e), parent=self)
            return
        if message_key:
            messagebox.showinfo(self.t("manager.done"), self.t(message_key, path=result), parent=self)
        self.done = True
        self.destroy()

    def delete(self):
        if messagebox.askyesno(self.t("manager.incompatible_title"),
                               self.t("manager.incompatible_confirm_delete", path=self.problem.path),
                               icon="warning", default="no", parent=self):
            self._run(data_files.delete)

    def rename(self):
        self._run(data_files.set_aside, "manager.incompatible_renamed")

    def upgrade(self):
        self._run(data_files.upgrade, "manager.incompatible_upgraded")


def resolve_incompatible() -> bool:
    """Before anything opens mezaniya.db or languages.db: ask about each one this version can't
    open. False = the user closed without a choice (the manager doesn't start)."""
    problems = data_files.problems(Config.DB_PATH, Config.languages_db_path())
    if not problems:
        return True
    root = tk.Tk()
    root.withdraw()
    try:
        for problem in problems:
            window = IncompatibleWindow(root, problem, i18n.default_language())
            root.wait_window(window)
            if not window.done:
                return False
        return True
    finally:
        root.destroy()


def main():
    problem = data_dir_problem(Config.DATA_DIR)
    if problem:
        root = tk.Tk()
        root.withdraw()
        rtl = i18n.direction(i18n.default_language()) == "rtl"
        messagebox.showerror(directed(i18n.t("manager.data_dir_title"), rtl),
                             directed(i18n.t("manager.data_dir_unwritable", path=Config.DATA_DIR, error=problem), rtl))
        root.destroy()
        return
    if not resolve_incompatible():
        return
    i18n.use_database(Config.languages_db_path())
    ManagerApp().mainloop()


if __name__ == "__main__":
    main()
