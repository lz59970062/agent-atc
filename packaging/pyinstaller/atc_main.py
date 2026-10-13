"""Frozen entry point: one executable serves every console script.

pyproject.toml declares ``atc`` and ``atc-desktop-core``. A frozen build ships a single binary, so the name it was
invoked under selects the entry point.
"""
from pathlib import Path
import sys

if __name__ == "__main__":
    if Path(sys.argv[0]).stem == "atc-desktop-core":
        from agent_atc.desktop_core import main
    else:
        from agent_atc.cli import main

    raise SystemExit(main())
