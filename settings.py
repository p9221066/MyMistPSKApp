"""Local settings for MyMistPSKApp.

The API token is stored in cleartext JSON under %LOCALAPPDATA% by design
(chosen at setup time for convenience). Anything that can read your user
profile can read the token, so treat the file as a secret: see README.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "MyMistPSKApp"


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULTS = {
    "cloud_label": "Global 01 (api.mist.com)",
    "custom_host": "",
    "token": "",
    "org_id": "",
    "remember_token": True,
}


def load() -> dict:
    data = dict(DEFAULTS)
    path = config_path()
    try:
        if path.is_file():
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass  # unreadable or corrupt config: fall back to defaults
    return data


def save(data: dict) -> None:
    out = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
    if not out.get("remember_token"):
        out["token"] = ""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
