"""Portable build: data next to the programs when portable.txt is there, and the zip builder."""
import importlib.util
import sys
import zipfile

import pytest

from app import config
from app.config import BASE_DIR


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """Pretend to be the frozen Mezaniya.exe in tmp_path/prog."""
    prog = tmp_path / "prog"
    prog.mkdir()
    monkeypatch.setattr(config, "FROZEN", True)
    monkeypatch.setattr(sys, "executable", str(prog / "Mezaniya.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return prog


def test_installed_build_uses_localappdata(frozen, tmp_path):
    assert not config.is_portable()
    assert config.user_data_dir() == tmp_path / "local" / "Mezaniya"


def test_portable_build_uses_data_next_to_the_programs(frozen):
    (frozen / "portable.txt").write_text("x", encoding="utf-8")
    assert config.is_portable()
    assert config.user_data_dir() == frozen.resolve() / "data"


def test_running_from_source_is_never_portable(monkeypatch):
    monkeypatch.setattr(config, "FROZEN", False)
    assert not config.is_portable() and config.user_data_dir() == BASE_DIR / "data"


def test_data_dir_problem(tmp_path):
    assert config.data_dir_problem(tmp_path / "new" / "data") is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert config.data_dir_problem(blocker / "data")         # a file where a folder should be


def load_builder():
    spec = importlib.util.spec_from_file_location("build_installer", BASE_DIR / "build_installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portable_zip(tmp_path):
    dist = tmp_path / "dist" / "Mezaniya"
    (dist / "_internal").mkdir(parents=True)
    (dist / "Mezaniya.exe").write_bytes(b"MZ")
    (dist / "MezaniyaServer.exe").write_bytes(b"MZ")
    (dist / "_internal" / "lib.dll").write_bytes(b"x")
    out = load_builder().build_portable("9.9.9-beta", dist, tmp_path / "Output")
    assert out.name == "Mezaniya-Portable-v9.9.9-beta.zip"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        top = "Mezaniya-Portable-v9.9.9-beta/"
        assert {top + n for n in ("Mezaniya.exe", "MezaniyaServer.exe", "_internal/lib.dll", "portable.txt")} == names
        note = z.read(top + "portable.txt").decode("utf-8-sig")
        assert "data" in note and "\r\n" in note
