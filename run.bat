@echo off
setlocal
cd /d "%~dp0"
pythonw app.py
if errorlevel 1 (
  echo.
  echo The app exited with an error. Running again with console output:
  python app.py
  pause
)
