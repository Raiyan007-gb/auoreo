#!/usr/bin/env node
// Stage publishable platform packages from release artifacts.
//   node packages/npm/scripts/pack-platforms.mjs --artifacts <dir> [--out <dir>] [--strict]
// For every platform in scripts/platforms.mjs, copies platforms/<id>/package.json and
// <artifacts>/<artifact> -> <out>/<id>/bin/auoreo[.exe] (mode 0755). Default out:
// packages/npm/dist. Missing artifacts are skipped with a warning (an error with --strict).
// Then: `npm publish <out>/<id>` per platform, followed by `npm publish packages/npm`.
import { chmodSync, copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import { PLATFORMS } from "./platforms.mjs";

const pkgRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function packPlatforms({ artifacts, out = join(pkgRoot, "dist"), strict = false }) {
  const staged = [];
  const missing = [];
  for (const { id, artifact, exe } of PLATFORMS) {
    const src = join(artifacts, artifact);
    if (!existsSync(src)) {
      missing.push(artifact);
      continue;
    }
    const dest = join(out, id);
    rmSync(dest, { recursive: true, force: true });
    mkdirSync(join(dest, "bin"), { recursive: true });
    copyFileSync(join(pkgRoot, "platforms", id, "package.json"), join(dest, "package.json"));
    copyFileSync(src, join(dest, "bin", exe));
    chmodSync(join(dest, "bin", exe), 0o755);
    staged.push(dest);
  }
  if (missing.length && strict) throw new Error(`missing release artifacts: ${missing.join(", ")}`);
  return { staged, missing };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: { artifacts: { type: "string" }, out: { type: "string" }, strict: { type: "boolean" } },
  });
  if (!values.artifacts) {
    console.error("usage: pack-platforms.mjs --artifacts <dir> [--out <dir>] [--strict]");
    process.exit(2);
  }
  try {
    const { staged, missing } = packPlatforms({
      artifacts: resolve(values.artifacts),
      out: values.out ? resolve(values.out) : undefined,
      strict: values.strict,
    });
    for (const m of missing) console.warn(`warning: artifact not found, skipped: ${m}`);
    for (const s of staged) console.log(`staged ${s}`);
  } catch (e) {
    console.error(String(e instanceof Error ? e.message : e));
    process.exit(1);
  }
}
