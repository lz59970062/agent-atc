"""One canonical ATC per user: ~/.atc/bin/atc and the install record beside it.

Several environments can each hold an ``atc``. Only the one that last ran
``atc setup`` is canonical: services, skills and pasted ACK commands use its
launcher, and other installations forward their commands to it. A frozen
build copies itself under ~/.atc/versions/ so it survives the package
manager that delivered it (for example an nvm Node version being removed); a
Python installation is referenced in place through its own interpreter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .. import __version__
from .. import runtime as atc_runtime
from ..store import install_record, layout
from ..store import records as queue_store

FORWARDED_ENV = "ATC_FORWARDED"


def _frozen_copy() -> list[str]:
    """Copy this frozen build under ~/.atc/versions and return its executable."""
    source = Path(sys.executable).resolve().parent
    target = layout.atc_home() / "versions" / f"{__version__}-{atc_runtime.build_id()}"
    if not target.is_dir():
        staging = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        shutil.copytree(source, staging, symlinks=True)
        os.replace(staging, target)
    return [str(target / Path(sys.executable).name)]


def _launcher_text(command: list[str]) -> str:
    if os.name == "nt":
        quoted = " ".join(f'"{part}"' for part in command)
        return f"@echo off\r\n{quoted} %*\r\n"
    import shlex
    return f"#!/bin/sh\n# ATC launcher written by atc setup; rerun atc setup to change it.\nexec {shlex.join(command)} \"$@\"\n"


def ensure() -> dict[str, object]:
    """Make this installation the canonical ATC and return its install record."""
    command = _frozen_copy() if getattr(sys, "frozen", False) else atc_runtime.worker_command()
    record = {"schema": install_record.INSTALL_SCHEMA, "version": __version__, "build": atc_runtime.build_id(),
              "command": command, "installed_at": time.time()}
    queue_store.private_directory(install_record.bin_dir())
    launcher = install_record.launcher_path()
    temporary = launcher.with_name(f".{launcher.name}.{os.getpid()}.tmp")
    temporary.write_text(_launcher_text(command), encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o755)
    os.replace(temporary, launcher)
    queue_store.write_request(install_record.record_path(), record)
    return record


def is_current(record: dict[str, object] | None = None) -> bool:
    """Whether this process runs the canonical build; identical builds are the same code."""
    record = install_record.read() if record is None else record
    return record is None or record.get("build") == atc_runtime.build_id()


def forward(argv: list[str]) -> int | None:
    """Run ``argv`` with the canonical installation instead of this one.

    Returns the exit code, or None when this installation should handle the
    command itself (it is canonical, nothing is installed, or it was forwarded).
    """
    if os.environ.get(FORWARDED_ENV) == "1":
        return None
    record = install_record.read()
    if record is None or is_current(record):
        return None
    launcher = install_record.launcher_path()
    if not launcher.is_file():
        return None
    print(f"atc: using the installed ATC {record.get('version')} (build {record.get('build')}) at {launcher}; "
          f"run `atc setup` from this installation to make it the installed one", file=sys.stderr)
    environment = dict(os.environ, **{FORWARDED_ENV: "1"})
    command = [str(launcher)] if os.name != "nt" else ["cmd", "/c", str(launcher)]
    return subprocess.run([*command, *argv], env=environment).returncode
