"""Launch the same installed runtime, independent of PATH and the caller's cwd.

All private workers use this entry point. A future frozen distribution can use
its executable directly without pretending it is a Python interpreter.
"""
import functools
import hashlib
from pathlib import Path
import sys
import os
import shutil
from .system import shell


BUILD_FILE = "_build.txt"
_BUILD_SUFFIXES = (".py", ".js", ".ps1", ".md", ".yaml")


@functools.lru_cache(maxsize=1)
def build_id() -> str:
    """Identify this exact build, not just its version number.

    Release builds embed ``_build.txt``. Otherwise the ID is a digest of the
    package's own files, so two installations that report the same version but
    hold different code are told apart.
    """
    package = Path(__file__).parent
    embedded = package / BUILD_FILE
    if embedded.is_file():
        return embedded.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.suffix in _BUILD_SUFFIXES and path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def worker_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).with_name("_entry.py")), *arguments]


def console_script_path() -> str | None:
    return shutil.which("reins")


def control_command(*arguments: str) -> str:
    """A command an agent can paste; runnable even when ``reins`` is not on PATH.

    OS-owned workers (launchd, systemd) usually lack the installation's bin
    directory on PATH, so fall back to the interpreter plus private entry point
    rather than a script path that is not executable on its own.
    """
    script = None if os.name == "nt" else console_script_path()
    command = [script, *arguments] if script else worker_command(*arguments)
    return shell.format_command(command)
