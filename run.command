#!/bin/bash
# Double-clickable launcher for macOS (and usable on Linux).
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/"
  read -r -p "Press Return to close."
  exit 1
fi

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  echo "This Python has no tkinter module."
  echo "On macOS:  brew install python-tk    (or use the python.org installer)"
  echo "On Debian/Ubuntu:  sudo apt install python3-tk"
  read -r -p "Press Return to close."
  exit 1
fi

exec "$PY" app.py
