"""Start / stop the server process from outside it (used by the server manager GUI).

run.py writes data/server.pid ({"pid", "port"}) while it runs, so a server started from
start.bat is controllable too. Before stopping a PID we check it is still a Python process
(or the installed MezaniyaServer.exe), so a stale file can't make us kill an unrelated
program that reused the PID.

Installed build: there is no Python on the machine, so the manager starts
MezaniyaServer.exe (run.py frozen by PyInstaller) from its own folder instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)
SERVER_EXE = "MezaniyaServer.exe"


@dataclass(frozen=True)
class ServerState:
    running: bool
    pid: int | None = None
    port: int | None = None
    healthy: bool = False


def write_pid_file(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")


def remove_pid_file(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def read_pid_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def process_name(pid: int) -> str | None:
    """Executable name of a live process, or None if it isn't running."""
    if IS_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        line = out.stdout.strip()
        if not line.startswith('"'):
            return None                     # "INFO: No tasks are running..."
        return line.split('","')[0].strip('"')
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return "python"


def parse_netstat_listener(output: str, port: int) -> int | None:
    """PID listening on TCP `port` in `netstat -ano -p TCP` output."""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].rsplit(":", 1)[-1] == str(port) \
                and parts[3].upper() == "LISTENING" and parts[4].isdigit():
            return int(parts[4])
    return None


def pid_listening_on(port: int) -> int | None:
    if not IS_WINDOWS:
        return None
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    return parse_netstat_listener(out.stdout, port)


def is_our_server(pid: int) -> bool:
    name = process_name(pid)
    return bool(name and name.lower().startswith(("python", SERVER_EXE.lower())))


def health(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


class ServerControl:
    def __init__(self, project_dir: Path, pid_file: Path, python: Path | None = None):
        self.project_dir = project_dir
        self.pid_file = pid_file
        self.python = python or console_python()
        self._proc: subprocess.Popen | None = None

    def command(self) -> tuple[list[str], Path]:
        """Server command line and working folder."""
        if FROZEN:
            exe = Path(sys.executable).with_name(SERVER_EXE)
            return [str(exe)], exe.parent
        return [str(self.python), "run.py"], self.project_dir

    def state(self, port: int | None = None) -> ServerState:
        """`port`: also look for a server answering there without a pid file
        (started by an older run.py, or from an editor)."""
        info = read_pid_file(self.pid_file)
        if info and is_our_server(info["pid"]):
            p = info.get("port")
            return ServerState(True, info["pid"], p, bool(p) and health(p))
        if self._proc and self._proc.poll() is None:      # started, pid file not written yet
            return ServerState(True, self._proc.pid, port, False)
        if port and health(port):
            pid = pid_listening_on(port)
            if pid and is_our_server(pid):
                return ServerState(True, pid, port, True)
        return ServerState(False)

    def start(self, port: int) -> subprocess.Popen:
        env = {**os.environ, "MEZANIYA_PORT": str(port)}
        if FROZEN:
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"    # MezaniyaServer.exe is its own frozen app
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WINDOWS else 0
        args, cwd = self.command()
        self._proc = subprocess.Popen(
            args, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, close_fds=True)
        return self._proc

    def wait_until_healthy(self, port: int, timeout: float = 15) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if health(port, timeout=0.5):
                return True
            if self._proc and self._proc.poll() is not None:
                return False                                  # exited (e.g. port already in use)
            time.sleep(0.25)
        return False

    def stop(self, timeout: float = 10, port: int | None = None) -> bool:
        st = self.state(port)
        if not st.running:
            return True
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(st.pid), "/T", "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.kill(st.pid, 15)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process_name(st.pid) is None:
                break
            time.sleep(0.2)
        try:
            self.pid_file.unlink()        # a killed process can't clean up after itself
        except OSError:
            pass
        self._proc = None
        return process_name(st.pid) is None


def console_python() -> Path:
    """python.exe next to the running interpreter (pythonw.exe has no console streams)."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe" if IS_WINDOWS else "python")
    return candidate if candidate.exists() else exe
