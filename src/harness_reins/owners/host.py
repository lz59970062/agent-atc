"""The current host's platform module and its machine and boot identity."""
from __future__ import annotations

import sys
from . import linux as linux_platform
from . import macos as macos_platform
from . import windows as windows_platform


def current_boot_id() -> str | None:
    return host_platform().current_boot_id()


def host_platform():
    if sys.platform == "win32":
        return windows_platform
    return macos_platform if sys.platform == "darwin" else linux_platform


def current_machine_id() -> str:
    return host_platform().current_machine_id()
