# PyInstaller spec: one folder (dist/Mezaniya) holding two programs that share one runtime.
#
#   Mezaniya.exe        server_manager.py, the dashboard (windowed, no console)
#   MezaniyaServer.exe  run.py, the web server the dashboard starts / stops / restarts
#
# Built by build_installer.py; by hand: python -m PyInstaller --noconfirm packaging/mezaniya.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, SPECPATH)
from version_info import version_info  # noqa: E402  (packaging/version_info.py)

ICON = str(ROOT / "packaging" / "mezaniya.ico")

# files the app opens from disk next to its modules (Path(__file__)…)
DATAS = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "app" / "i18n" / "strings.json"), "app/i18n"),
    (str(ROOT / "app" / "db" / "migrations"), "app/db/migrations"),
]
EXCLUDES = ["pytest", "_pytest", "IPython", "matplotlib", "numpy", "PIL"]


def analysis(script, extra_excludes=()):
    return Analysis([str(ROOT / script)], pathex=[str(ROOT)], datas=DATAS,
                    excludes=EXCLUDES + list(extra_excludes), noarchive=False)


manager = analysis("server_manager.py")
server = analysis("run.py", extra_excludes=["tkinter", "_tkinter"])

manager_exe = EXE(PYZ(manager.pure), manager.scripts, [], exclude_binaries=True, name="Mezaniya",
                  icon=ICON, console=False, upx=False,
                  version=version_info("Mezaniya.exe", "Mezaniya budget manager"))
# console build so its log also shows when run by hand; the dashboard starts it without a window
server_exe = EXE(PYZ(server.pure), server.scripts, [], exclude_binaries=True, name="MezaniyaServer",
                 icon=ICON, console=True, upx=False,
                 version=version_info("MezaniyaServer.exe", "Mezaniya budget server"))

COLLECT(manager_exe, manager.binaries, manager.datas,
        server_exe, server.binaries, server.datas,
        name="Mezaniya", upx=False)
