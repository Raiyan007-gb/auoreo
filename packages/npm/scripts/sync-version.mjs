#!/usr/bin/env node
// Keep the npm launcher + platform packages at the Python package's `__version__`.
//   node packages/npm/scripts/sync-version.mjs          rewrite package.json files
//   node packages/npm/scripts/sync-version.mjs --check  exit 1 if anything is out of sync
// PEP 440 pre-releases map to semver: 1.2.0a1 -> 1.2.0-alpha.1, b -> beta, rc -> rc,
// .devN -> -dev.N (npm dist-tags then follow the `auoreo upgrade` channels).
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { PLATFORMS, scopedName } from "./platforms.mjs";

const pkgRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(pkgRoot, "..", "..");

export function pythonVersion(initPy = join(repoRoot, "src", "auoreo", "__init__.py")) {
  const m = /^__version__\s*=\s*["']([^"']+)["']/m.exec(readFileSync(initPy, "utf8"));
  if (!m) throw new Error(`no __version__ in ${initPy}`);
  return m[1];
}

export function toSemver(pep440) {
  const m = /^(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?(?:\.dev(\d+))?$/.exec(pep440);
  if (!m) throw new Error(`unsupported version ${pep440}`);
  const [, base, pre, preN, dev] = m;
  const label = { a: "alpha", b: "beta", rc: "rc" }[pre];
  let v = base;
  if (label) v += `-${label}.${preN}`;
  if (dev !== undefined) v += label ? `.dev.${dev}` : `-dev.${dev}`;
  return v;
}

function update(file, mutate, check) {
  const before = readFileSync(file, "utf8");
  const json = JSON.parse(before);
  mutate(json);
  const after = `${JSON.stringify(json, null, 2)}\n`;
  const changed = JSON.stringify(JSON.parse(before)) !== JSON.stringify(json);
  if (changed && !check) writeFileSync(file, after);
  return changed;
}

function main() {
  const check = process.argv.includes("--check");
  const version = toSemver(pythonVersion());
  const stale = [];
  const main = join(pkgRoot, "package.json");
  const mainChanged = update(
    main,
    (j) => {
      j.version = version;
      for (const { id } of PLATFORMS) j.optionalDependencies[scopedName(id)] = version;
    },
    check,
  );
  if (mainChanged) stale.push(main);
  for (const { id } of PLATFORMS) {
    const file = join(pkgRoot, "platforms", id, "package.json");
    if (update(file, (j) => void (j.version = version), check)) stale.push(file);
  }
  if (check && stale.length) {
    console.error(`npm versions out of sync with __version__ (${version}):\n  ${stale.join("\n  ")}`);
    process.exit(1);
  }
  console.log(check ? `npm versions in sync (${version})` : `npm packages at ${version} (${stale.length} updated)`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
