---
name: npm
description: Publish web/sdk and the binary launcher to npm — package.json bin, workspaces, provenance, dist-tags, and the bin/agent Node launcher that spawns the native binary. Use when building web/* release channel.
---

# npm — JS distribution channel (plan.md Part J)

npm is the channel that gives devs **one command** (`npm i -g agent-platform`) producing a real native binary — via a tiny `bin/agent` Node launcher that spawns the platform binary, exactly like opencode's `packages/opencode/bin/opencode`.

## package.json essentials

```json
{
  "name": "agent-platform",
  "version": "1.2.3",
  "type": "module",      # ⚠ ESM package — see bin/agent.cjs note below
  "bin": { "agent": "./bin/agent.cjs" },   # .cjs: launcher uses require(); ESM .js would break
  "files": ["bin", "dist"],
  "engines": { "node": ">=22.14" }
}
```

- `bin` maps the command name → an executable JS file with a shebang `#!/usr/bin/env node`; npm creates the global/linkable binary.
- **`.cjs` matters:** with `"type": "module"`, a `.js` file is ESM and `require()` is undefined (`ERR_REQUIRE_ESM`). Name the launcher `bin/agent.cjs` (CommonJS) — or rewrite it with `import { spawn } from "node:child_process"` and keep `.js`. The launcher pattern below uses `require`, so use `.cjs`.
- `files` whitelist keeps the tarball small (only what's needed).

## The launcher pattern (opencode `bin/opencode`)

`bin/agent` is a ~60-line Node script that:
1. Detects `os.platform()`/`os.arch()` → `agent-<platform>-<arch>` (incl. linux musl + AVX2-baseline variants).
2. Resolves the real binary from `node_modules/agent-<platform>-<arch>/bin/<agent|agent.exe>` (installed as an optional dependency), or `OPENCODE_*`-style env override / cached path.
3. Spawns it inheriting stdio, forwarding SIGINT/SIGTERM/exit codes.

This is how "npm command" and "exe" become the same artifact: the npm package is the delivery channel, the compiled binary is the payload.

```js
// bin/agent.cjs (simplified) — .cjs because the package is "type": "module"
#!/usr/bin/env node
const { spawn } = require("node:child_process");
const os = require("node:os");
const p = `${os.platform()}-${os.arch()}`;
const bin = require.resolve(`agent-${p}/bin/agent`);
const child = spawn(bin, process.argv.slice(2), { stdio: "inherit" });
["SIGINT","SIGTERM"].forEach(sig => process.on(sig, () => child.kill(sig)));
child.on("exit", code => process.exit(code ?? 0));
```

## Workspaces

Repo `package.json` defines `web/*` workspaces; `web/sdk` is the publishable package. `npm ci --workspace web/sdk` installs only what's needed.

## Publishing rules/verification (Context7 `/npm/cli`, `npm/cli` ★)

- **npm OIDC trusted publishing is GA** (npm CLI ≥ 11.5.1, Node ≥ 22.14, 2025-07) — no publish token needed. In CI: set `permissions: { id-token: write }`, `setup-node` with `registry-url`, then `npm publish --provenance --access public --tag <tag>`; the publish authenticates via OIDC. A separate **read-only** `NPM_READ_TOKEN` is only for installing private deps (never for publish). Requires `node >= 22.14` (hence `engines` above) — verify via `npm publish --dry-run` first (see `github-actions` skill for the workflow).
- **Trusted-publisher config (2026-05-20 change):** npm now requires each trusted publisher to explicitly select at least one allowed action — `--allow-publish` and/or `--allow-stage-publish`. A new `npm trust` CLI exists (requires npm ≥ 11.15.0) for managing publishers. One trusted publisher per package; the workflow filename must match exactly (case-sensitive); **self-hosted runners are not supported** for npm OIDC — cloud runners only.
- **Provenance caveat:** `--provenance` is unavailable when publishing from a **private** source repository (GitHub changelog 2025-07-31). If the repo is private, drop `--provenance` or make it conditional.
- **Dist-tags:** `--tag alpha|beta|rc|latest` — a tag write touches ONLY that tag (`root['dist-tags'][tag] = version`); `latest` is untouched by `--tag alpha`. Resolve the tag from the version string (like opencode's resolver).
- `publishConfig.access=public` for unscoped public packages; avoid `gettings tag` surprises by **not** setting a default tag in most cases (release workflow always passes `--tag` explicitly).
- No secrets in the tarball: `files` + `.npmignore`; never ship `*.local.json`, `.env`, keys.
- Test with `npm publish --dry-run` locally before CI. `prepublishOnly` script runs the build on publish.

## Update/self-update

Add `scripts/upgrade.ts`-style check (`agent upgrade`) that pulls the latest npm version and re-installs the platform package — mirrors opencode `upgrade.ts` (plan.md D `agent_cli/upgrade.py` for CLI parity).