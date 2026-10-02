"""Local settings for MyMistPSKApp.

The API token is stored in cleartext JSON in the per-user config directory
(see config_dir) by design, chosen at setup time for convenience. Anything
that can read your user profile can read the token, so treat the file as a
secret: see README.md.

On macOS and Linux the file is chmod 0600, which genuinely restricts it to
your account. On Windows that call only toggles the read-only flag and does
not restrict ACLs, so the file is readable by any process running as you.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "MyMistPSKApp"


def config_dir() -> Path:
    """The per-user config directory, following each platform's convention."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
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
        # Effective on macOS/Linux; a near no-op on Windows (see module docstring).
        os.chmod(path, 0o600)
    except OSError:
        pass
