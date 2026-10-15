#!/usr/bin/env bash
# Freeze atc with PyInstaller. Runs inside a Python image and writes /out/dist/atc.
# The builder's glibc is the oldest glibc the binary will run on.
set -euo pipefail

work=$(mktemp -d)
cp -r /src/pyproject.toml /src/src "$work/"   # /src is mounted read-only
# A frozen build has no source files to digest, so it carries its build ID.
if [[ -n ${ATC_BUILD:-} ]]; then
  printf '%s\n' "$ATC_BUILD" > "$work/src/agent_atc/_build.txt"
fi
python -m venv "$work/venv"
. "$work/venv/bin/activate"
pip install --quiet --disable-pip-version-check pyinstaller==6.11.1 "$work"
pyinstaller --noconfirm --log-level WARN --distpath /out/dist --workpath "$work/build" \
  /src/packaging/pyinstaller/atc.spec

/out/dist/atc/atc --version
echo "built against $(ldd --version | head -1)"
du -sh /out/dist/atc
