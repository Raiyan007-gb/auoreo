---
name: github-actions
description: Configure CI/CD with GitHub Actions — push/PR checks, tag-triggered multi-channel releases (npm, Docker/GHCR, PyPI) with OIDC trusted publishing, sdk drift guard. Use when editing .github/workflows in this repo.
---

# GitHub Actions — CI/CD + releases (plan.md Part J)

Workflows: `ci.yml` (lint/typecheck/unit/integration each push+PR), `publish.yml` (tag → version + build + all channels), `containers.yml` (GHCR), `sdk.yml` (drift guard). Modeled on opencode's `publish.yml`.

## Trigger gating

```yaml
on:
  push:
    branches: [dev]        # main/stable track
    tags: ['v*']           # releases  (⚠ one push: block, not two — duplicate keys fail to parse)
  workflow_dispatch:
    inputs:
      bump: { type: choice, options: [major, minor, patch] }
```

## CI (per push/PR)

```yaml
jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6          # v6 (2025-11); pin SHA for supply-chain safety
      - uses: astral-sh/setup-uv@v9.0.0    # v8+ are immutable — major/minor tags not published; pin exact
      - run: uv sync --frozen
      - run: uvx ruff check src/ && uv run mypy src/
      - run: uv run pytest -q
  tsc:
    runs-on: ubuntu-latest
    steps: [ checkout (@v6), setup-node@v6 (node 24; auto-cache), npm ci --workspaces, npm run typecheck (tsc --noEmit) ]
    # + npm run build  → confirms web UI builds
```

## Release (tag-triggered, all channels — the opencode pattern)

1. `version` job — `scripts/version.ts` bumps version, drafts GitHub Release (changelog), outputs `version`.
2. `build-cli` — builds native binaries (darwin/linux/windows × x64/arm64 + musl/baseline) via PyInstaller/Nuitka or Bun compile (`scripts/build.ts`); uploads `.zip` assets to the GitHub Release.
3. `sign-cli-windows` — **Azure Trusted Signing** (`azure/artifact-signing-action`) + verify `Get-AuthenticodeSignature` + repack + attach to release (windows users avoid SmartScreen; plan.md Part J).
4. **npm publish** with OIDC trusted publishing (no token):

```yaml
permissions:
  id-token: write          # required for npm OIDC trusted publishing (and SLSA provenance)
steps:
  - uses: actions/setup-node@v4
    with:
      node-version: 24          # npm OIDC needs npm >= 11.5.1 / Node >= 22.14; use 24
      registry-url: 'https://registry.npmjs.org'
      package-manager-cache: false   # never cache in release builds
  - run: npm ci
  - run: npm run build --workspace web/sdk
  - run: npm publish --workspace web/sdk --provenance --access public --tag ${{ steps.dist_tag.outputs.tag }}
    env:
      NODE_AUTH_TOKEN: ${{ secrets.NPM_READ_TOKEN }}   # READ-ONLY token: only for installing private deps, NOT for publish
```
   - **npm OIDC trusted publishing is GA** (npm CLI ≥ 11.5.1, Node ≥ 22.14, 2025-07). Configure the publisher once in npm account settings (repo + workflow → `id-token: write`); then `npm publish` authenticates via OIDC with **no publish token**. If the package pulls private deps, supply a separate read-only `NPM_READ_TOKEN` for `npm ci`.
   - `--provenance` (SLSA) still required alongside `id-token: write`; use `--access public` for unscoped public packages.
   - Compute dist-tag from version (`alpha|beta|rc|latest`) like opencode's per-tier dist-tags; `--tag X` writes only that tag (verified: npm publish.js — `root['dist-tags'][tag] = version`, `latest` untouched by `--tag alpha`).
5. **Docker/GHCR** — `docker/login-action` (GHCR), `docker/setup-buildx`, `docker/build-push-action` platforms `linux/amd64,linux/arm64`, tags `ghcr.io/<org>/agent-platform:${tag}` + `latest`, `provenance: true`.
6. **PyPI — trusted publishing (OIDC, no token)**:

```yaml
permissions:
  id-token: write          # REQUIRED for trusted publishing
jobs:
  build:   # python -m build / uv build → dist/ → upload-artifact (separate job = least privilege)
  publish:
    needs: build
    environment: pypi      # + protection rules optional
    permissions: { id-token: write }
    steps:
      - uses: actions/download-artifact@v4
      - uses: pypa/gh-action-pypi-publish@release/v1   # no username/password → OIDC
```

Verified source: GitHub Docs OIDC-in-PyPI guide (Context7 `/websites/github_en_actions`).

## Conventions

- Pin actions by **SHA** (or exact immutable version) for supply-chain safety (opencode pins full SHAs; `astral-sh/setup-uv` v8+ no longer publishes major/minor tags).
- Separate build from publish jobs; only the publish job gets `id-token: write`.
- Environments (`dev`/`pypi`) for deploy protection + audit.
- Secrets: npm uses **OIDC trusted publishing — no publish token** (optional read-only `NPM_READ_TOKEN` for private deps); PyPI needs **no secret** (OIDC). Store GHCR auth via `GITHUB_TOKEN`.
- Keep `ci.yml` fast: cache `uv` + npm (`actions/cache`), split jobs, run e2e only on PR + release.