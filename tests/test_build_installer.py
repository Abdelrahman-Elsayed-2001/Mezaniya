"""build_installer.py: finding the Inno Setup compiler."""
import pytest

import build_installer


@pytest.fixture
def clean(tmp_path, monkeypatch):
    """No Inno Setup anywhere; every default location points inside tmp_path."""
    monkeypatch.setattr(build_installer, "registry_inno_dirs", lambda: [])
    monkeypatch.setattr(build_installer.shutil, "which", lambda _name: None)
    monkeypatch.delenv("INNO_SETUP", raising=False)
    for base in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(base, str(tmp_path / base))
    return tmp_path


def iscc(folder):
    folder.mkdir(parents=True)
    (folder / "ISCC.exe").touch()
    return folder / "ISCC.exe"


def test_finds_version_7_in_program_files(clean):
    exe = iscc(clean / "ProgramFiles" / "Inno Setup 7")
    assert build_installer.find_iscc() == exe


def test_prefers_the_newest_version(clean):
    iscc(clean / "LOCALAPPDATA" / "Programs" / "Inno Setup 6")
    exe = iscc(clean / "ProgramFiles(x86)" / "Inno Setup 7")
    assert build_installer.find_iscc() == exe


def test_finds_a_custom_folder_through_the_registry(clean, monkeypatch):
    exe = iscc(clean / "Tools" / "Inno Setup 7")
    monkeypatch.setattr(build_installer, "registry_inno_dirs", lambda: [exe.parent])
    assert build_installer.find_iscc() == exe


def test_env_variable_wins(clean, monkeypatch):
    iscc(clean / "ProgramFiles" / "Inno Setup 7")
    exe = iscc(clean / "Other")
    monkeypatch.setenv("INNO_SETUP", str(exe))
    assert build_installer.find_iscc() == exe


def test_not_found_says_how_to_fix_it(clean):
    with pytest.raises(SystemExit, match="INNO_SETUP"):
        build_installer.find_iscc()


def test_build_guide_writes_the_pdf_into_the_program_folder(tmp_path, monkeypatch):
    dist = tmp_path / "dist" / "Mezaniya"
    dist.mkdir(parents=True)
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        out = cmd[cmd.index("--out-dir") + 1]
        (build_installer.Path(out)).mkdir(parents=True, exist_ok=True)
        (build_installer.Path(out) / "user_guide.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(build_installer, "run", fake_run)
    pdf = build_installer.build_guide(dist)
    assert pdf == dist / "docs" / "user_guide.pdf"
    assert calls[0][1:4] == [str(build_installer.CONVERTER), str(build_installer.GUIDE), "--format"]


def test_build_guide_stops_when_no_pdf_came_out(tmp_path, monkeypatch):
    dist = tmp_path / "Mezaniya"
    dist.mkdir()
    monkeypatch.setattr(build_installer, "run", lambda cmd: None)
    with pytest.raises(SystemExit, match="--skip-guide"):
        build_installer.build_guide(dist)
