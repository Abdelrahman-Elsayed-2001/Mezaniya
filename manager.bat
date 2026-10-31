@echo off
rem Open the Mezaniya server manager (no console window).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run start.bat once first to set up the environment.
    pause
    goto :eof
)
start "" ".venv\Scripts\pythonw.exe" server_manager.py
