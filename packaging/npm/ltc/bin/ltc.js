#!/usr/bin/env node
// npm launcher: find the frozen ltc for this platform and run it.
//
// The binary is installed by one of the optionalDependencies below; npm picks
// the one whose "os"/"cpu" match. Long-lived owners (systemd, launchd, Task
// Scheduler) are registered against the binary itself, never this script, so
// they do not depend on Node.
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

const PLATFORM_PACKAGES = {
  "linux-x64": "@ltc-proto/linux-x64",
  "linux-arm64": "@ltc-proto/linux-arm64",
  "darwin-x64": "@ltc-proto/darwin-x64",
  "darwin-arm64": "@ltc-proto/darwin-arm64",
  "win32-x64": "@ltc-proto/win32-x64",
};

function fail(message) {
  process.stderr.write(`ltc: ${message}\n`);
  process.exit(1);
}

function binaryPath() {
  if (process.env.LTC_BINARY) return process.env.LTC_BINARY;
  const key = `${process.platform}-${process.arch}`;
  const name = PLATFORM_PACKAGES[key];
  if (!name) fail(`no prebuilt binary for ${key}; install with pip instead`);
  let root;
  try {
    root = path.dirname(require.resolve(`${name}/package.json`));
  } catch {
    fail(`${name} is not installed; reinstall without --omit=optional / --no-optional`);
  }
  return path.join(root, "dist", "ltc", process.platform === "win32" ? "ltc.exe" : "ltc");
}

const result = spawnSync(binaryPath(), process.argv.slice(2), { stdio: "inherit" });
if (result.error) fail(result.error.message);
if (result.signal) process.kill(process.pid, result.signal);
process.exit(result.status === null ? 1 : result.status);
