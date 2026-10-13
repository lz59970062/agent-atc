#!/usr/bin/env bash
# Freeze ltc with PyInstaller. Runs inside a Python image and writes /out/dist/ltc.
# The builder's glibc is the oldest glibc the binary will run on.
set -euo pipefail

work=$(mktemp -d)
cp -r /src/pyproject.toml /src/src "$work/"   # /src is mounted read-only
python -m venv "$work/venv"
. "$work/venv/bin/activate"
pip install --quiet --disable-pip-version-check pyinstaller==6.11.1 "$work"
pyinstaller --noconfirm --log-level WARN --distpath /out/dist --workpath "$work/build" \
  /src/packaging/pyinstaller/ltc.spec

/out/dist/ltc/ltc --version
echo "built against $(ldd --version | head -1)"
du -sh /out/dist/ltc
