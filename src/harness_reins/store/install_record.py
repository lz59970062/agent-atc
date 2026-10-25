"""Where the canonical Reins launcher and its install record live (read side).

``reins setup`` writes them (install.canonical); everything else only reads
them, to run the canonical installation instead of whichever ``reins`` is first
on PATH.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import layout

INSTALL_SCHEMA = "reins.install/1"


def bin_dir() -> Path:
    return layout.reins_home() / "bin"


def launcher_path() -> Path:
    return bin_dir() / ("reins.cmd" if os.name == "nt" else "reins")


def record_path() -> Path:
    return layout.reins_home() / "install.json"


def read() -> dict[str, object] | None:
    try:
        record = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != INSTALL_SCHEMA:
        return None
    command = record.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        return None
    return record


def launcher() -> str | None:
    """The canonical launcher, when ``reins setup`` has installed one."""
    path = launcher_path()
    return str(path) if read() is not None and path.is_file() else None
