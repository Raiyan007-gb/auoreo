---
name: npm
description: Publish web/sdk and the binary launcher to npm — package.json bin, workspaces, OIDC trusted publishing + provenance, staged publishing, dist-tags, and the bin/agent Node launcher that spawns the native binary. Use when building web/* release channel.
---

# npm — JS distribution channel (plan.md Part J)

npm is the channel that gives devs **one command** (`npm i -g agent-platform`) producing a real native binary — via a tiny `bin/agent` Node launcher that spawns the platform binary, exactly like opencode's `packages/opencode/bin/opencode`.

## package.json essentials

```json
{
  "name": "agent-platform",
  "version": "1.2.3",
  "type": "module",
  "bin": { "agent": "./bin/agent.cjs" },
  "files": ["bin", "dist"],
  "engines": { "node": ">=22.14" }
}
```

- ⚠ `"type": "module"` makes this an ESM package — hence `bin/agent.cjs` (see note below). (No comments in real `package.json` — it's strict JSON.)
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
#!/usr/bin/env node
// bin/agent.cjs (simplified) — .cjs because the package is "type": "module"; shebang MUST be line 1
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

## Publishing rules/verification (Context7 `/npm/cli`, `/websites/npmjs`)

- **npm OIDC trusted publishing is GA** (npm CLI ≥ 11.5.1, Node ≥ 22.14, 2025-07) — no publish token needed. In CI: set `permissions: { id-token: write, contents: read }`, `actions/setup-node@v6` with `registry-url: https://registry.npmjs.org` and `package-manager-cache: false` (no caching in release builds; npm's example uses `node-version: '24'`), then `npm publish --tag <tag>`; the publish authenticates via OIDC. A separate **read-only** `NPM_READ_TOKEN` (as `NODE_AUTH_TOKEN` on `npm ci`) is only for installing private deps (never for publish). Requires `node >= 22.14` (hence `engines` above) — verify via `npm publish --dry-run` first (see `github-actions` skill for the workflow).
- **Provenance is automatic** with trusted publishing from GitHub Actions/GitLab when the repo is **public** and the package is public — `--provenance` is no longer needed (harmless to keep). From a **private** source repo no provenance is generated, even for public packages — drop or condition the flag.
- **Trusted-publisher config:** each trusted publisher must select at least one allowed action — `--allow-publish` and/or `--allow-stage-publish`. Manage them with `npm trust <provider>` (requires npm ≥ 11.15.0, account 2FA, package must already exist; `npm trust <provider> --help` lists provider claims). Up to **10** trusted publishers per package; existing ones can't be edited — delete + recreate. The workflow filename must match exactly (case-sensitive); **self-hosted runners are not supported** — cloud runners only (GitHub-hosted, GitLab.com shared, CircleCI cloud; CircleCI gets no provenance).
- **Staged publishing (optional, stronger):** `npm stage publish` uploads the version without making it public and without a 2FA prompt; a maintainer then runs `npm stage approve <stage-id>` (2FA) — also `stage list|view|reject|download`. npm recommends trust relationships allow only stage-publish and packages be set to "require 2FA and disallow tokens".
- **Dist-tags:** `--tag alpha|beta|rc|latest` — a tag write touches ONLY that tag (`root['dist-tags'][tag] = version`); `latest` is untouched by `--tag alpha`. Resolve the tag from the version string (like opencode's resolver).
- **Access:** unscoped packages (`agent-platform`, `agent-<platform>-<arch>`) are always public; `--access public` / `publishConfig.access` only matters for **scoped** packages (default restricted) and only on first publish. Avoid "default tag" surprises by **not** setting `publishConfig.tag` in most cases (release workflow always passes `--tag` explicitly).
- No secrets in the tarball: `files` + `.npmignore`; never ship `*.local.json`, `.env`, keys.
- Test with `npm publish --dry-run` locally before CI. `prepublishOnly` script runs the build on publish.

## Update/self-update

Add `scripts/upgrade.ts`-style check (`agent upgrade`) that pulls the latest npm version and re-installs the platform package — mirrors opencode `upgrade.ts` (plan.md D `agent_cli/upgrade.py` for CLI parity).