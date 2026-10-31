"""Windows version resource (Properties → Details) for the PyInstaller exes.

The version comes from app/version.py, so there is nothing to edit here on a release:
0.1.0 + STAGE "beta" → file version 0.1.0.0, product version "0.1.0-beta".
Used by mezaniya.spec; `python packaging/version_info.py` prints the resource for a check.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTHOR = "Abdelrahman Elsayed"
COPYRIGHT = f"Copyright (c) 2026 {AUTHOR}"
PRODUCT = "Mezaniya"


def read_version() -> tuple[str, str]:
    """(__version__, STAGE) from app/version.py, without importing the app (and Flask)."""
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    values = dict(re.findall(r'^(__version__|STAGE)\s*=\s*"([^"]*)"', text, re.MULTILINE))
    return values["__version__"], values.get("STAGE", "")


def numeric_version(version: str) -> tuple[int, int, int, int]:
    """"0.1.0" → (0, 1, 0, 0): Windows wants exactly four numbers."""
    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    return tuple(parts + [0] * (4 - len(parts)))


def version_info(exe_name: str, description: str):
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    version, stage = read_version()
    numbers = numeric_version(version)
    dotted = ".".join(map(str, numbers))
    strings = [
        StringStruct("CompanyName", AUTHOR),
        StringStruct("FileDescription", description),
        StringStruct("FileVersion", dotted),
        StringStruct("InternalName", exe_name.removesuffix(".exe")),
        StringStruct("LegalCopyright", COPYRIGHT),
        StringStruct("OriginalFilename", exe_name),
        StringStruct("ProductName", PRODUCT),
        StringStruct("ProductVersion", f"{version}-{stage}" if stage else version),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", strings)]),     # US English, Unicode
              VarFileInfo([VarStruct("Translation", [0x0409, 1200])])])


if __name__ == "__main__":
    print(version_info("Mezaniya.exe", "Mezaniya budget manager"))
