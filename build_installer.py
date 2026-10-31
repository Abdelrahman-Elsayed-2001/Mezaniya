"""Build the Windows installer: PyInstaller, then Inno Setup.

    python build_installer.py                  # dist\\Mezaniya + Output\\Mezaniya-Setup-v<version>.exe
    python build_installer.py --skip-pyinstaller   # recompile installer.iss only
    python build_installer.py --skip-installer     # PyInstaller only (no Inno Setup needed)

Needs: the app's requirements and PyInstaller in this Python (pip install -r requirements-build.txt),
and Inno Setup 6 (ISCC.exe on PATH, in its default folder, or set INNO_SETUP to its path).
The version comes from app/version.py: 0.1.0 + STAGE "beta" → Mezaniya-Setup-v0.1.0-beta.exe.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "packaging"))
from version_info import numeric_version, read_version  # noqa: E402  (packaging/version_info.py)

SPEC = ROOT / "packaging" / "mezaniya.spec"
ISS = ROOT / "installer.iss"
DIST = ROOT / "dist" / "Mezaniya"
OUTPUT = ROOT / "Output"


def find_iscc() -> Path:
    candidates = [os.environ.get("INNO_SETUP"), shutil.which("ISCC")]
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(base):
            sub = "Programs/Inno Setup 6" if base == "LOCALAPPDATA" else "Inno Setup 6"
            candidates.append(str(Path(os.environ[base]) / sub / "ISCC.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    sys.exit("Inno Setup 6 not found: install it from https://jrsoftware.org/isdl.php "
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
    numbers = ".".join(map(str, numeric_version(version)))
    run([str(find_iscc()), f"/DAppVersion={numbers}", f"/DAppVersionFull={full}", str(ISS)])
    setup = OUTPUT / f"Mezaniya-Setup-v{full}.exe"
    if not setup.is_file():
        sys.exit(f"Inno Setup did not produce {setup}")
    return setup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Mezaniya Windows installer.")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse the existing dist\\Mezaniya")
    parser.add_argument("--skip-installer", action="store_true", help="stop after PyInstaller")
    args = parser.parse_args(argv)

    version, stage = read_version()
    full = f"{version}-{stage}" if stage else version
    print(f"Mezaniya {full}")
    if not args.skip_pyinstaller:
        build_exe()
    if args.skip_installer:
        print(f"Done: {DIST}")
        return 0
    setup = build_installer(version, full)
    print(f"Done: {setup} ({setup.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
