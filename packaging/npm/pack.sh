#!/usr/bin/env bash
# Stage and pack the npm packages around a frozen build. Runs inside a Node image.
# Usage: pack.sh <npm-version> <platform>, e.g. pack.sh 0.7.1-alpha.2 linux-x64
set -euo pipefail

version=$1
platform=$2
stage=$(mktemp -d)
cp -r /src/packaging/npm/ltc "$stage/ltc"
mkdir -p "$stage/$platform/dist"
cp -a /out/dist/ltc "$stage/$platform/dist/ltc"

node - "$stage" "$version" "$platform" <<'EOF'
const fs = require("fs");
const path = require("path");
const [stage, version, platform] = process.argv.slice(2);
const [os, cpu] = platform.split("-");

const mainFile = path.join(stage, "ltc", "package.json");
const main = JSON.parse(fs.readFileSync(mainFile, "utf8"));
main.version = version;
for (const name of Object.keys(main.optionalDependencies)) main.optionalDependencies[name] = version;
fs.writeFileSync(mainFile, JSON.stringify(main, null, 2) + "\n");

fs.writeFileSync(path.join(stage, platform, "package.json"), JSON.stringify({
  name: `@ltc-proto/${platform}`,
  version,
  description: `Prebuilt ltc binary for ${platform}`,
  license: "MIT",
  os: [os],
  cpu: [cpu],
  files: ["dist"],
  preferUnplugged: true,
}, null, 2) + "\n");
EOF

mkdir -p /out/npm
(cd "$stage/ltc" && npm pack --silent --pack-destination /out/npm)
(cd "$stage/$platform" && npm pack --silent --pack-destination /out/npm)
