"""Frozen entry point: one executable serves every console script.

pyproject.toml declares ``ltc`` and ``ltc-desktop-core``. A frozen build ships a single binary, so the name it was
invoked under selects the entry point.
"""
from pathlib import Path
import sys

if __name__ == "__main__":
    if Path(sys.argv[0]).stem == "ltc-desktop-core":
        from long_task_callback.desktop_core import main
    else:
        from long_task_callback.cli import main

    raise SystemExit(main())
