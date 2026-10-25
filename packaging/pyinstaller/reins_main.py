"""Frozen entry point: one executable serves every console script.

pyproject.toml declares ``reins`` and ``reins-desktop-core``. A frozen build ships a single binary, so the name it was
invoked under selects the entry point.
"""
from pathlib import Path
import sys

if __name__ == "__main__":
    if Path(sys.argv[0]).stem == "reins-desktop-core":
        from harness_reins.harness.codex.desktop.core import main
    else:
        from harness_reins.cli import main

    raise SystemExit(main())
