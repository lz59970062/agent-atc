#!/usr/bin/env bash
# Build atc as a self-contained Linux x64 binary and pack it as npm packages.
# Every step runs in a throwaway container; the host needs only Docker.
#
#   packaging/build-linux.sh            -> packaging/out/{dist,npm}
#   ATC_PIP_INDEX_URL=...               optional PyPI mirror for the builder
#   ATC_APT_MIRROR=...                  optional Debian mirror host for the builder
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out=$root/packaging/out
# bullseye's glibc 2.31 covers Ubuntu 20.04+ and Debian 11+.
base=${ATC_BUILDER_BASE:-python:3.12-slim-bullseye}
builder=atc-freeze-builder:local
node_image=${ATC_NODE_IMAGE:-node:20-bookworm-slim}

docker build -q -t "$builder" --build-arg "BASE_IMAGE=$base" --build-arg "APT_MIRROR=${ATC_APT_MIRROR:-}" \
  - <"$root/packaging/Dockerfile.freeze" >/dev/null

python_version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/pyproject.toml")
npm_version=$(sed -E 's/a([0-9]+)$/-alpha.\1/; s/b([0-9]+)$/-beta.\1/; s/rc([0-9]+)$/-rc.\1/' <<<"$python_version")

rm -rf "$out"
mkdir -p "$out"
as_user=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
pip_env=()
[[ -n ${ATC_PIP_INDEX_URL:-} ]] && pip_env=(-e "PIP_INDEX_URL=$ATC_PIP_INDEX_URL")

docker run --rm "${as_user[@]}" "${pip_env[@]}" -v "$root:/src:ro" -v "$out:/out" \
  "$builder" bash /src/packaging/freeze.sh
docker run --rm "${as_user[@]}" -e npm_config_cache=/tmp/.npm -v "$root:/src:ro" -v "$out:/out" \
  "$node_image" bash /src/packaging/npm/pack.sh "$npm_version" linux-x64

ls -lh "$out/npm"
