#!/usr/bin/env bash
# Smoke-test the npm packages from packaging/build-linux.sh in a disposable,
# Python-free Node container. Nothing on the host is installed or changed.
#
#   NPM_UPSTREAM=...   registry used to fetch verdaccio (default registry.npmjs.org)
#   LTC_APT_MIRROR=... optional Debian mirror host
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
docker run --rm --init \
  -e "NPM_UPSTREAM=${NPM_UPSTREAM:-https://registry.npmjs.org}" -e "APT_MIRROR=${LTC_APT_MIRROR:-}" \
  -v "$root/packaging/out:/out:ro" -v "$root/packaging/npm/smoke-test.sh:/smoke-test.sh:ro" \
  "${LTC_NODE_IMAGE:-node:20-bookworm-slim}" bash ${LTC_TRACE:+-x} /smoke-test.sh
