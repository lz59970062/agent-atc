#!/usr/bin/env node
// npm launcher: find the frozen atc for this platform and run it.
//
// The binary is installed by one of the optionalDependencies below; npm picks
// the one whose "os"/"cpu" match. Long-lived owners (systemd, launchd, Task
// Scheduler) are registered against the binary itself, never this script, so
// they do not depend on Node.
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

const PLATFORM_PACKAGES = {
  "linux-x64": "@agent-atc/linux-x64",
  "linux-arm64": "@agent-atc/linux-arm64",
  "darwin-x64": "@agent-atc/darwin-x64",
  "darwin-arm64": "@agent-atc/darwin-arm64",
  "win32-x64": "@agent-atc/win32-x64",
};

function fail(message) {
  process.stderr.write(`atc: ${message}\n`);
  process.exit(1);
}

const PIP_INSTALL = 'python3 -m pip install "git+https://github.com/lz59970062/agent-atc.git"';

// The Linux binaries link against glibc; musl systems such as Alpine cannot load them.
function isMusl() {
  if (process.platform !== "linux") return false;
  try {
    const report = process.report && process.report.getReport();
    return !(report && report.header && report.header.glibcVersionRuntime);
  } catch {
    return false;
  }
}

function binaryPath() {
  if (process.env.ATC_BINARY) return process.env.ATC_BINARY;
  if (isMusl()) fail(`no prebuilt binary for musl-based Linux (such as Alpine); install from source: ${PIP_INSTALL}`);
  const key = `${process.platform}-${process.arch}`;
  const name = PLATFORM_PACKAGES[key];
  if (!name) fail(`no prebuilt binary for ${key}; install from source: ${PIP_INSTALL}`);
  let root;
  try {
    root = path.dirname(require.resolve(`${name}/package.json`));
  } catch {
    fail(`${name} is not installed; reinstall without --omit=optional / --no-optional`);
  }
  return path.join(root, "dist", "atc", process.platform === "win32" ? "atc.exe" : "atc");
}

const result = spawnSync(binaryPath(), process.argv.slice(2), { stdio: "inherit" });
if (result.error) fail(result.error.message);
if (result.signal) process.kill(process.pid, result.signal);
process.exit(result.status === null ? 1 : result.status);
