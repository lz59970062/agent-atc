"""Environment flags and command lines in the platform's own conventions."""
from __future__ import annotations

import os
import shlex
from . import windows_process


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def format_command(arguments: list[str]) -> str:
    """Render commands for the platform's documented interactive shell."""
    if os.name == "nt":
        return windows_process.format_command(arguments)
    return shlex.join(arguments)


def process_command(arguments: list[str], environment: dict[str, str] | None = None) -> list[str]:
    return windows_process.prepare_command(arguments, env=environment) if os.name == "nt" else arguments
