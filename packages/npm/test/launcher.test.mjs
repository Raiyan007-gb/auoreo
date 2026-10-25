// node --test packages/npm/test : launcher resolution order, recursion guard, uvx fallback,
// error message, exit-code/signal forwarding, version sync and platform packing.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { packPlatforms } from "../scripts/pack-platforms.mjs";
import { PLATFORMS, scopedName } from "../scripts/platforms.mjs";
import { pythonVersion, toSemver } from "../scripts/sync-version.mjs";

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = resolve(here, "..");
const LAUNCHER = join(pkgRoot, "bin", "auoreo.cjs");
const L = require(LAUNCHER);

const posixOnly = { skip: process.platform === "win32" ? "fake binaries are sh scripts" : false };
const roots = [];
after(() => roots.forEach((r) => rmSync(r, { recursive: true, force: true })));

function tmp() {
  const d = mkdtempSync(join(tmpdir(), "auoreo-npm-"));
  roots.push(d);
  return d;
}

/** A fake executable that prints its tag + args and exits with $FAKE_EXIT. */
function fakeBin(dir, name, tag) {
  mkdirSync(dir, { recursive: true });
  const p = join(dir, name);
  writeFileSync(p, `#!/bin/sh\necho "${tag} $*"\nexit \${FAKE_EXIT:-0}\n`);
  chmodSync(p, 0o755);
  return p;
}

const base = { platform: "linux", arch: "x64", musl: false, selfPath: LAUNCHER };

describe("resolveCommand order", posixOnly, () => {
  test("AUOREO_BINARY beats package, PATH and uvx", () => {
    const d = tmp();
    const explicit = fakeBin(d, "explicit", "env");
    const pathDir = join(d, "path");
    fakeBin(pathDir, "auoreo", "path");
    fakeBin(pathDir, "uvx", "uvx");
    const pkgDir = join(d, "pkg");
    fakeBin(join(pkgDir, "bin"), "auoreo", "pkg");
    const r = L.resolveCommand(["--version"], {
      ...base,
      env: { AUOREO_BINARY: explicit, PATH: pathDir },
      resolvePackage: () => pkgDir,
    });
    assert.deepEqual(r, { command: explicit, args: ["--version"], source: "env" });
  });

  test("a missing AUOREO_BINARY is an error, not a silent fallback", () => {
    const r = L.resolveCommand([], { ...base, env: { AUOREO_BINARY: "/nope/auoreo" } });
    assert.match(r.error, /AUOREO_BINARY points at a missing file/);
  });

  test("platform package beats PATH", () => {
    const d = tmp();
    const pathDir = join(d, "path");
    fakeBin(pathDir, "auoreo", "path");
    const pkgDir = join(d, "pkg");
    const bin = fakeBin(join(pkgDir, "bin"), "auoreo", "pkg");
    const seen = [];
    const r = L.resolveCommand(["x"], {
      ...base,
      env: { PATH: pathDir },
      resolvePackage: (name) => (seen.push(name), pkgDir),
    });
    assert.equal(r.source, "package");
    assert.equal(r.command, bin);
    assert.deepEqual(seen, ["@auoreo/cli-linux-x64"]);
  });

  test("musl hosts ask for the -musl package; windows for auoreo.exe", () => {
    const d = tmp();
    const seen = [];
    L.resolveCommand([], { ...base, musl: true, env: { PATH: d }, resolvePackage: (n) => (seen.push(n), null) });
    assert.deepEqual(seen, ["@auoreo/cli-linux-x64-musl"]);
    const pkgDir = join(d, "win");
    fakeBin(join(pkgDir, "bin"), "auoreo.exe", "win");
    const r = L.resolveCommand([], { ...base, platform: "win32", env: { PATH: "" }, resolvePackage: () => pkgDir });
    assert.equal(r.command, join(pkgDir, "bin", "auoreo.exe"));
  });

  test("system auoreo on PATH when no package is installed", () => {
    const d = tmp();
    const bin = fakeBin(join(d, "a"), "auoreo", "path");
    fakeBin(join(d, "a"), "uvx", "uvx");
    const r = L.resolveCommand(["serve"], { ...base, env: { PATH: join(d, "a") }, resolvePackage: () => null });
    assert.deepEqual(r, { command: bin, args: ["serve"], source: "path" });
  });

  test("PATH guard skips the launcher symlink and npm shims, finds the real one later", () => {
    const d = tmp();
    const linkDir = join(d, "npm-global-bin");
    mkdirSync(linkDir);
    symlinkSync(LAUNCHER, join(linkDir, "auoreo"));
    const shimDir = join(d, "shim");
    mkdirSync(shimDir);
    writeFileSync(join(shimDir, "auoreo"), '#!/bin/sh\nexec node "$basedir/node_modules/auoreo/bin/auoreo.cjs" "$@"\n');
    chmodSync(join(shimDir, "auoreo"), 0o755);
    const real = fakeBin(join(d, "real"), "auoreo", "real");
    const r = L.resolveCommand([], {
      ...base,
      env: { PATH: [linkDir, shimDir, join(d, "real")].join(":") },
      resolvePackage: () => null,
    });
    assert.equal(r.command, real);
    assert.ok(L.isSelf(join(linkDir, "auoreo"), LAUNCHER));
  });

  test("re-entry (AUOREO_NPM_LAUNCHER set) never searches PATH again", () => {
    const d = tmp();
    fakeBin(d, "auoreo", "path");
    const uvx = fakeBin(d, "uvx", "uvx");
    const r = L.resolveCommand([], {
      ...base,
      env: { PATH: d, [L.GUARD_ENV]: "1" },
      resolvePackage: () => null,
    });
    assert.equal(r.command, uvx);
  });

  test("uvx fallback prefixes the package name", () => {
    const d = tmp();
    const uvx = fakeBin(d, "uvx", "uvx");
    const r = L.resolveCommand(["run", "hi"], { ...base, env: { PATH: d }, resolvePackage: () => null });
    assert.deepEqual(r, { command: uvx, args: ["auoreo", "run", "hi"], source: "uvx" });
  });

  test("nothing found: error lists every install option", () => {
    const r = L.resolveCommand([], { ...base, env: { PATH: tmp() }, resolvePackage: () => null });
    assert.match(r.error, /no executable found for linux-x64/);
    for (const hint of ["@auoreo/cli-linux-x64", "npm install -g auoreo", "uv tool install auoreo", "uvx", "AUOREO_BINARY"]) {
      assert.ok(r.error.includes(hint), hint);
    }
    const other = L.resolveCommand([], { ...base, platform: "freebsd", env: { PATH: tmp() }, resolvePackage: () => null });
    assert.match(other.error, /No prebuilt binary is published for this platform/);
  });
});

test("detectMusl reads glibcVersionRuntime from the process report", () => {
  assert.equal(L.detectMusl("linux", () => ({ header: { glibcVersionRuntime: "2.39" } })), false);
  assert.equal(L.detectMusl("linux", () => ({ header: {} })), true);
  assert.equal(L.detectMusl("darwin", () => ({ header: {} })), false);
  assert.equal(L.detectMusl("linux", () => { throw new Error("no report"); }), false);
});

test("launcher package table matches scripts/platforms.mjs and package.json", () => {
  const ids = PLATFORMS.map((p) => p.id).sort();
  assert.deepEqual(Object.keys(L.PLATFORM_PACKAGES).sort(), ids);
  for (const id of ids) assert.equal(L.PLATFORM_PACKAGES[id], scopedName(id));
  const pkg = JSON.parse(readFileSync(join(pkgRoot, "package.json"), "utf8"));
  assert.deepEqual(Object.keys(pkg.optionalDependencies).sort(), ids.map(scopedName).sort());
  assert.equal(pkg.bin.auoreo, "bin/auoreo.cjs");
  for (const id of ids) {
    const p = JSON.parse(readFileSync(join(pkgRoot, "platforms", id, "package.json"), "utf8"));
    const [os, cpu] = id.split("-");
    assert.equal(p.name, scopedName(id));
    assert.deepEqual(p.os, [os]);
    assert.deepEqual(p.cpu, [cpu]);
    if (os === "linux") assert.deepEqual(p.libc, [id.endsWith("-musl") ? "musl" : "glibc"]);
  }
});

describe("end to end (real spawn)", posixOnly, () => {
  /** Lay out <root>/node_modules/{auoreo,@auoreo/cli-<key>} like an npm install. */
  function install(exitCode) {
    const root = tmp();
    const launcherDir = join(root, "node_modules", "auoreo", "bin");
    mkdirSync(launcherDir, { recursive: true });
    copyFileSync(LAUNCHER, join(launcherDir, "auoreo.cjs"));
    const key = L.platformKey(process.platform, process.arch, L.detectMusl());
    const pkgDir = join(root, "node_modules", "@auoreo", `cli-${key}`);
    mkdirSync(pkgDir, { recursive: true });
    writeFileSync(join(pkgDir, "package.json"), JSON.stringify({ name: `@auoreo/cli-${key}`, version: "0.0.0" }));
    fakeBin(join(pkgDir, "bin"), "auoreo", "platform-pkg");
    return { launcher: join(launcherDir, "auoreo.cjs"), exitCode };
  }

  const run = (launcher, args, env) =>
    spawnSync(process.execPath, [launcher, ...args], { encoding: "utf8", env: { PATH: tmp(), ...env } });

  test("installed platform package runs with args and stdio inherited", (t) => {
    if (!L.PLATFORM_PACKAGES[L.platformKey(process.platform, process.arch, L.detectMusl())]) {
      t.skip("no platform package for this host");
      return;
    }
    const { launcher } = install();
    const r = run(launcher, ["--version"], {});
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout.trim(), "platform-pkg --version");
  });

  test("exit code is forwarded", () => {
    const d = tmp();
    const bin = fakeBin(d, "auoreo-fake", "fake");
    const r = run(LAUNCHER, ["a"], { AUOREO_BINARY: bin, FAKE_EXIT: "7" });
    assert.equal(r.status, 7);
    assert.equal(r.stdout.trim(), "fake a");
  });

  test("a child killed by a signal kills the launcher with the same signal", () => {
    const d = tmp();
    const p = join(d, "suicidal");
    writeFileSync(p, "#!/bin/sh\nkill -TERM $$\nsleep 5\n");
    chmodSync(p, 0o755);
    const r = run(LAUNCHER, [], { AUOREO_BINARY: p });
    assert.equal(r.signal, "SIGTERM");
  });

  test("child sees the re-entry guard env", () => {
    const d = tmp();
    const p = join(d, "envdump");
    writeFileSync(p, `#!/bin/sh\necho "guard=$${L.GUARD_ENV}"\n`);
    chmodSync(p, 0o755);
    const r = run(LAUNCHER, [], { AUOREO_BINARY: p });
    assert.equal(r.stdout.trim(), "guard=1");
  });

  test("no binary anywhere: exit 1 with the install options on stderr", () => {
    const root = tmp();
    const launcher = join(root, "auoreo.cjs");
    copyFileSync(LAUNCHER, launcher); // no node_modules next to it
    const r = run(launcher, [], {});
    assert.equal(r.status, 1);
    assert.match(r.stderr, /Install one of:/);
  });
});

test("sync-version: PEP 440 -> semver and the committed files are in sync", () => {
  assert.equal(toSemver("0.1.0"), "0.1.0");
  assert.equal(toSemver("1.2.0a1"), "1.2.0-alpha.1");
  assert.equal(toSemver("1.2.0b3"), "1.2.0-beta.3");
  assert.equal(toSemver("1.2.0rc2"), "1.2.0-rc.2");
  assert.equal(toSemver("1.2.0.dev4"), "1.2.0-dev.4");
  assert.throws(() => toSemver("1.2"));
  const r = spawnSync(process.execPath, [join(pkgRoot, "scripts", "sync-version.mjs"), "--check"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const pkg = JSON.parse(readFileSync(join(pkgRoot, "package.json"), "utf8"));
  assert.equal(pkg.version, toSemver(pythonVersion()));
});

test("pack-platforms stages artifacts into publishable packages", posixOnly, () => {
  const art = tmp();
  writeFileSync(join(art, "auoreo-linux-x64"), "ELF");
  writeFileSync(join(art, "auoreo-windows-x64.exe"), "MZ");
  const out = tmp();
  const { staged, missing } = packPlatforms({ artifacts: art, out });
  assert.deepEqual(staged.sort(), [join(out, "linux-x64"), join(out, "win32-x64")].sort());
  assert.equal(missing.length, 3);
  assert.ok(existsSync(join(out, "win32-x64", "bin", "auoreo.exe")));
  assert.equal(statSync(join(out, "linux-x64", "bin", "auoreo")).mode & 0o111, 0o111);
  assert.equal(JSON.parse(readFileSync(join(out, "linux-x64", "package.json"), "utf8")).name, "@auoreo/cli-linux-x64");
  assert.throws(() => packPlatforms({ artifacts: art, out, strict: true }), /missing release artifacts/);
});
