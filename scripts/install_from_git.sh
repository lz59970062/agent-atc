#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <github-https-url> [subdirectory] [-- <reins setup options>...]" >&2
  echo "Example: $0 https://github.com/lz59970062/harness-reins.git" >&2
  echo "Set REINS_PYTHON to select an existing virtual environment's Python." >&2
  exit 2
fi

repo_url="$1"
shift
subdirectory=""
if [[ $# -gt 0 && "$1" != -- ]]; then
  subdirectory="$1"
  shift
fi
if [[ $# -gt 0 ]]; then
  if [[ "$1" != -- ]]; then
    echo "Pass reins setup options after --" >&2
    exit 2
  fi
  shift
fi
reins_python="${REINS_PYTHON:-python3}"

if [[ "$repo_url" == git+* ]]; then
  spec="$repo_url"
else
  spec="git+${repo_url}"
fi

if [[ -n "$subdirectory" ]]; then
  spec="${spec}#subdirectory=${subdirectory}"
fi

"$reins_python" -m pip install "$spec"
# Use the same interpreter for installation and setup, even if the installed
# console scripts (for example pip --user) are not on the caller's PATH.
"$reins_python" -m harness_reins setup --force --enable --now "$@"
