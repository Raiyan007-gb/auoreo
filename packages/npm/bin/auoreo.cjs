#!/usr/bin/env node
// auoreo npm launcher: find a real `auoreo` executable and run it with inherited stdio.
//
// Resolution order (spec L1095, plus an explicit override first):
//   1. $AUOREO_BINARY
//   2. the per-platform optional dependency `@auoreo/cli-<platform>-<arch>[-musl]`
//   3. a system `auoreo` on PATH (never this launcher or its npm shims: recursion guard)
//   4. `uvx auoreo` when uv is installed
//   5. otherwise a clear error listing the install options
//
// AVX2 baseline variants (spec L217) are N/A: the payload is a PyInstaller-frozen CPython,
// which targets the plain x86-64 baseline, so there is no AVX2/non-AVX2 split to pick from.
"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

/** Re-entry marker: a launcher spawned by a launcher must not look on PATH again. */
const GUARD_ENV = "AUOREO_NPM_LAUNCHER";
/** Text that every npm-generated shim for this package contains (sh, .cmd, .ps1). */
const SHIM_MARKER = "auoreo.cjs";

const PLATFORM_PACKAGES = {
  "linux-x64": "@auoreo/cli-linux-x64",
  "linux-arm64": "@auoreo/cli-linux-arm64",
  "linux-x64-musl": "@auoreo/cli-linux-x64-musl",
  "darwin-arm64": "@auoreo/cli-darwin-arm64",
  "win32-x64": "@auoreo/cli-win32-x64",
};

/** musl libc has no `glibcVersionRuntime` in the process report header. */
function detectMusl(platform = process.platform, report = () => process.report?.getReport?.()) {
  if (platform !== "linux") return false;
  try {
    const header = report()?.header;
    return !header || !header.glibcVersionRuntime;
  } catch {
    return false;
  }
}

function platformKey(platform, arch, musl) {
  return `${platform}-${arch}${platform === "linux" && musl ? "-musl" : ""}`;
}

function exeName(platform, base = "auoreo") {
  return platform === "win32" ? `${base}.exe` : base;
}

function isFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function realpath(p) {
  try {
    return fs.realpathSync(p);
  } catch {
    return path.resolve(p);
  }
}

/** True for this launcher itself (symlinked bin) or an npm-generated shim pointing at it. */
function isSelf(candidate, selfPath) {
  if (realpath(candidate) === realpath(selfPath)) return true;
  try {
    if (fs.statSync(candidate).size > 8192) return false; // real binaries are megabytes
    return fs.readFileSync(candidate, "utf8").includes(SHIM_MARKER);
  } catch {
    return false;
  }
}

/** All executables called `name` on PATH, in order (PATHEXT-aware on Windows). */
function which(name, { env = process.env, platform = process.platform } = {}) {
  const sep = platform === "win32" ? ";" : ":";
  const dirs = (env.PATH ?? env.Path ?? "").split(sep).filter(Boolean);
  const exts =
    platform === "win32" ? (env.PATHEXT ?? ".EXE;.CMD;.BAT;.COM").split(";").filter(Boolean) : [""];
  const hits = [];
  for (const dir of dirs) {
    for (const ext of exts) {
      const p = path.join(dir, name + ext.toLowerCase());
      if (isFile(p) && !hits.includes(p)) hits.push(p);
    }
  }
  return hits;
}

function defaultResolvePackage(pkg) {
  try {
    return path.dirname(require.resolve(`${pkg}/package.json`, { paths: [__dirname] }));
  } catch {
    return null;
  }
}

/**
 * Decide what to run. Pure apart from filesystem probes; every input is injectable.
 * Returns `{command, args, source}` or `{error}`.
 */
function resolveCommand(argv, opts = {}) {
  const env = opts.env ?? process.env;
  const platform = opts.platform ?? process.platform;
  const arch = opts.arch ?? process.arch;
  const musl = opts.musl ?? detectMusl(platform);
  const selfPath = opts.selfPath ?? __filename;
  const resolvePackage = opts.resolvePackage ?? defaultResolvePackage;

  if (env.AUOREO_BINARY) {
    if (!isFile(env.AUOREO_BINARY)) {
      return { error: `AUOREO_BINARY points at a missing file: ${env.AUOREO_BINARY}` };
    }
    return { command: env.AUOREO_BINARY, args: argv, source: "env" };
  }

  const key = platformKey(platform, arch, musl);
  const pkg = PLATFORM_PACKAGES[key];
  if (pkg) {
    const dir = resolvePackage(pkg);
    const bin = dir && path.join(dir, "bin", exeName(platform));
    if (bin && isFile(bin)) return { command: bin, args: argv, source: "package" };
  }

  if (!env[GUARD_ENV]) {
    const system = which("auoreo", { env, platform }).find((p) => !isSelf(p, selfPath));
    if (system) return { command: system, args: argv, source: "path" };
  }

  const uvx = which("uvx", { env, platform })[0];
  if (uvx) return { command: uvx, args: ["auoreo", ...argv], source: "uvx" };

  return { error: notFoundMessage(key, pkg) };
}

function notFoundMessage(key, pkg) {
  return [
    `auoreo: no executable found for ${key}.`,
    pkg
      ? `  The optional dependency ${pkg} is not installed (npm --omit=optional, or a failed download).`
      : "  No prebuilt binary is published for this platform.",
    "Install one of:",
    "  npm install -g auoreo            (reinstall with optional dependencies)",
    "  uv tool install auoreo           (or: pipx install auoreo)",
    "  curl -LsSf https://astral.sh/uv/install.sh | sh   (then `uvx auoreo` is used automatically)",
    "  download a binary from the GitHub release and set AUOREO_BINARY=/path/to/auoreo",
  ].join("\n");
}

const FORWARDED = ["SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"];

function run(argv = process.argv.slice(2)) {
  const resolved = resolveCommand(argv);
  if (resolved.error) {
    process.stderr.write(`${resolved.error}\n`);
    process.exit(1);
  }
  const shell = process.platform === "win32" && /\.(cmd|bat)$/i.test(resolved.command);
  const child = spawn(resolved.command, resolved.args, {
    stdio: "inherit",
    env: { ...process.env, [GUARD_ENV]: "1" },
    shell,
    windowsHide: false,
  });
  const handlers = {};
  for (const sig of FORWARDED) {
    handlers[sig] = () => {
      if (!child.killed) child.kill(sig);
    };
    try {
      process.on(sig, handlers[sig]);
    } catch {
      /* signal not supported on this platform */
    }
  }
  child.on("error", (err) => {
    process.stderr.write(`auoreo: failed to start ${resolved.command}: ${err.message}\n`);
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    for (const sig of FORWARDED) process.removeListener(sig, handlers[sig]);
    if (signal) {
      process.kill(process.pid, signal); // die the same way so shells see the signal
      return;
    }
    process.exit(code ?? 1);
  });
}

module.exports = { resolveCommand, detectMusl, platformKey, which, isSelf, notFoundMessage, PLATFORM_PACKAGES, GUARD_ENV };

if (require.main === module) run();
