"""Build the Windows releases: PyInstaller, then the setup (Inno Setup) and the portable zip.

    python build_installer.py                  # dist\\Mezaniya, then in Output\\:
                                               #   Mezaniya-Setup-v<version>.exe     (installer)
                                               #   Mezaniya-Portable-v<version>.zip  (unzip and run)
    python build_installer.py --skip-pyinstaller   # reuse dist\\Mezaniya
    python build_installer.py --skip-installer     # no setup (no Inno Setup needed)
    python build_installer.py --skip-portable      # no portable zip

The portable zip is the same dist\\Mezaniya plus packaging\\portable.txt: that file makes the
programs keep their data in a data\\ folder beside them (app/config.py) instead of %LOCALAPPDATA%.

Needs: the app's requirements and PyInstaller in this Python (pip install -r requirements-build.txt),
and Inno Setup 6 or newer (found on PATH, in its usual folders or the registry; or set INNO_SETUP to ISCC.exe).
The version comes from app/version.py: 0.4.0 + STAGE "beta" → Mezaniya-Setup-v0.4.0-beta.exe.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_version_info():
    """packaging/version_info.py, loaded by path: packaging/ can't be a Python package,
    since that name would shadow the real `packaging` library PyInstaller depends on."""
    spec = importlib.util.spec_from_file_location("version_info", ROOT / "packaging" / "version_info.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version_info = load_version_info()
SPEC = ROOT / "packaging" / "mezaniya.spec"
ISS = ROOT / "installer.iss"
PORTABLE_NOTE = ROOT / "packaging" / "portable.txt"     # also the marker app/config.py looks for
DIST = ROOT / "dist" / "Mezaniya"
OUTPUT = ROOT / "Output"


def registry_inno_dirs() -> list[Path]:
    """Install folders of every Inno Setup version in the Windows uninstall list (any location)."""
    if os.name != "nt":
        return []
    import winreg
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    found = []
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, uninstall),
                       (winreg.HKEY_LOCAL_MACHINE, uninstall.replace("SOFTWARE", r"SOFTWARE\WOW6432Node")),
                       (winreg.HKEY_CURRENT_USER, uninstall)):
        try:
            with winreg.OpenKey(hive, path) as key:
                names = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
                for name in names:
                    if re.fullmatch(r"Inno Setup \d+_is1", name):
                        with winreg.OpenKey(key, name) as app:
                            found.append(Path(winreg.QueryValueEx(app, "InstallLocation")[0]))
        except OSError:
            continue
    return found


def inno_version(folder: Path) -> int:
    match = re.search(r"Inno Setup (\d+)", folder.name)
    return int(match.group(1)) if match else 0


def find_iscc() -> Path:
    """INNO_SETUP, then PATH, then the newest version in the default folders or the registry."""
    folders = registry_inno_dirs()
    for base in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(base):
            parent = Path(os.environ[base]) / ("Programs" if base == "LOCALAPPDATA" else "")
            folders += parent.glob("Inno Setup*")
    newest_first = sorted(folders, key=inno_version, reverse=True)
    candidates = [os.environ.get("INNO_SETUP"), shutil.which("ISCC"), *(f / "ISCC.exe" for f in newest_first)]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    sys.exit("Inno Setup not found: install it from https://jrsoftware.org/isdl.php "
             "or set INNO_SETUP to the full path of ISCC.exe.")


def run(cmd: list[str]) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_exe() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(f"PyInstaller is not installed in {sys.executable}: pip install -r requirements-build.txt")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), str(SPEC)])
    for exe in ("Mezaniya.exe", "MezaniyaServer.exe"):
        if not (DIST / exe).is_file():
            sys.exit(f"PyInstaller did not produce {DIST / exe}")


def build_installer(version: str, full: str) -> Path:
    if not (DIST / "Mezaniya.exe").is_file():
        sys.exit(f"{DIST} is missing: run without --skip-pyinstaller first.")
    numbers = ".".join(map(str, version_info.numeric_version(version)))
    run([str(find_iscc()), f"/DAppVersion={numbers}", f"/DAppVersionFull={full}", str(ISS)])
    setup = OUTPUT / f"Mezaniya-Setup-v{full}.exe"
    if not setup.is_file():
        sys.exit(f"Inno Setup did not produce {setup}")
    return setup


def build_portable(full: str, dist: Path = DIST, output: Path = OUTPUT) -> Path:
    """Output/Mezaniya-Portable-v<full>.zip: one top folder with the programs and portable.txt."""
    if not (dist / "Mezaniya.exe").is_file():
        sys.exit(f"{dist} is missing: run without --skip-pyinstaller first.")
    from app.config import PORTABLE_MARKER
    output.mkdir(parents=True, exist_ok=True)
    top = f"Mezaniya-Portable-v{full}"
    target = output / f"{top}.zip"
    tmp = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                z.write(path, f"{top}/{path.relative_to(dist).as_posix()}")
        note = PORTABLE_NOTE.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        z.writestr(f"{top}/{PORTABLE_MARKER}", "\ufeff" + note)      # BOM + CRLF: reads well in Notepad
    tmp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Mezaniya Windows setup and portable zip.")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse the existing dist\\Mezaniya")
    parser.add_argument("--skip-installer", action="store_true", help="no setup exe (Inno Setup not needed)")
    parser.add_argument("--skip-portable", action="store_true", help="no portable zip")
    args = parser.parse_args(argv)

    version, stage = version_info.read_version()
    full = f"{version}-{stage}" if stage else version
    print(f"Mezaniya {full}")
    if not args.skip_pyinstaller:
        build_exe()
    built = []
    if not args.skip_installer:
        built.append(build_installer(version, full))
    if not args.skip_portable:
        built.append(build_portable(full))
    for path in built:
        print(f"Done: {path} ({path.stat().st_size / 1_048_576:.1f} MB)")
    if not built:
        print(f"Done: {DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
