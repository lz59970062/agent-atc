"""Launch the same installed runtime, independent of PATH and the caller's cwd.

All private workers use this entry point. A future frozen distribution can use
its executable directly without pretending it is a Python interpreter.
"""
from pathlib import Path
import sys
import os
import shutil
from .system import shell


def worker_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).with_name("_entry.py")), *arguments]


def console_script_path() -> str | None:
    return shutil.which("atc")


def control_command(*arguments: str) -> str:
    """A command an agent can paste; runnable even when ``atc`` is not on PATH.

    OS-owned workers (launchd, systemd) usually lack the installation's bin
    directory on PATH, so fall back to the interpreter plus private entry point
    rather than a script path that is not executable on its own.
    """
    script = None if os.name == "nt" else console_script_path()
    command = [script, *arguments] if script else worker_command(*arguments)
    return shell.format_command(command)
