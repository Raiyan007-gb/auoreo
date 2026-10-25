# Releasing AUOREO

Pushing a tag `v<version>` runs `.github/workflows/release.yml`. The tag must match
`auoreo.__version__` and `[project].version` exactly (`scripts/check_version_tag.py`). The
spec rule is version == tag == `auoreo --version`.

```bash
# bump src/auoreo/__init__.py and pyproject.toml together, commit, then:
git tag v0.2.0 && git push origin v0.2.0      # stable
git tag v0.3.0rc1 && git push origin v0.3.0rc1  # beta (a/b/rc), marked as a pre-release
git tag v0.3.0.dev1 && git push origin v0.3.0.dev1  # dev
```

## What the workflow produces

| Job | Output |
|---|---|
| `guard` | Fails the run if the tag and the version differ. Works out the channel: `stable`, `beta` (a/b/rc) or `dev` (`.devN`) |
| `web` | `web/dist` (artifact shared by every job that packages it) |
| `python-dist` | `uv build` wheel + sdist. The hatch hook (`packaging/hatch_build.py`) packs the bundled definitions and `web/dist` into `auoreo/_bundled/`. The job fails if `web/dist/index.html` is missing (never ship a wheel without the UI) |
| `pypi` | Trusted publishing (OIDC, no token): environment `pypi`, `id-token: write` on this job only |
| `image` | `ghcr.io/<owner>/auoreo` for `linux/amd64,linux/arm64`, with provenance and an SBOM. Tags: `X.Y.Z`, `X.Y`, and `latest` (stable releases only) |
| `binaries` | PyInstaller onefile builds on ubuntu-latest (linux-x64), ubuntu-24.04-arm (linux-arm64), macos-14 (macos-arm64) and windows-latest (windows-x64). Each one is smoke-tested (`--version` == tag) |
| `binary-musl` | linux-x64-musl, built inside `python:3.12-alpine`. Experimental (`continue-on-error`) |
| `github-release` | The GitHub Release with the binaries, `SHA256SUMS`, wheel/sdist and `manifest-<channel>.json`. The manifests are also uploaded to the rolling `updates` release |

## One-time setup

1. **PyPI trusted publisher**: on PyPI, add a GitHub publisher for this repository with
   workflow `release.yml` and environment `pypi`. Then create the `pypi` environment in the
   repository settings (adding required reviewers is recommended).
2. **GHCR**: no setup needed. `GITHUB_TOKEN` with `packages: write` pushes the image. Make the
   package public in its settings if you want anonymous pulls.
3. **Update signing key** (required for `auoreo upgrade`):
   ```bash
   uv run python packaging/sign_manifest.py keygen --out update-signing-key.pem
   ```
   Store the PEM as the `UPDATE_SIGNING_KEY` secret. Put the printed public key into
   `PINNED_PUBKEYS` in `src/auoreo/core/upgrade.py`, then delete the local file. If the
   secret is not set, the release is still created but no manifests are published, so
   installs do not see the release as an update.
   Key rotation: append the new public key one release ahead of switching the secret.
   Installs trust every key in `PINNED_PUBKEYS`.

### Code signing (optional; each step runs only when its secrets exist)

The signing steps check secrets through job-level `env` (`if: env.MACOS_CERT != ''`).
`secrets.*` cannot be used directly in a step `if`. When the secrets are absent, the
binaries ship unsigned and the steps are skipped.

| Platform | Secrets / variables | What runs |
|---|---|---|
| macOS | `MACOS_CERT` (base64 Developer ID Application .p12), `MACOS_CERT_PASSWORD`, `MACOS_SIGN_IDENTITY` (e.g. `Developer ID Application: Name (TEAMID)`), `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | Temporary keychain → `codesign --options runtime --timestamp` → `xcrun notarytool submit --wait`. A bare binary cannot be stapled; Gatekeeper checks the notarization online |
| Windows | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_SIGNING_ACCOUNT_NAME`, `AZURE_SIGNING_CERTIFICATE_PROFILE`, variable `AZURE_SIGNING_ENDPOINT` | `azure/login` (OIDC) → `azure/artifact-signing-action` (Authenticode, SHA256, RFC 3161 timestamp) |
| Linux | none | Integrity comes from `SHA256SUMS` and the signed update manifest |

Signing happens before the artifact upload, so `SHA256SUMS` and the manifests hash the
signed files.

## Update manifests and `auoreo upgrade`

`manifest-<channel>.json`:

```json
{"version": "0.2.0", "channel": "stable", "min_schema_version": 1,
 "assets": {"linux-x64": {"url": ".../auoreo-linux-x64", "sha256": "..."}},
 "sig": "<base64 ed25519 over the canonical JSON without sig>"}
```

The canonical JSON uses sorted keys, `(",", ":")` separators and UTF-8. Channel fan-out:

- a stable release is published to `stable`, `beta` and `dev`;
- a beta release to `beta` and `dev`;
- a dev build to `dev` only.

Installs read `settings.update_url`. The default is
`https://github.com/<owner>/auoreo/releases/download/updates/manifest-{channel}.json`. This is
a rolling `updates` pre-release that the workflow overwrites. GitHub's `latest/download`
URL only ever points at the newest *stable* release, so the beta and dev channels could not
use it.

`auoreo upgrade [--channel stable|beta|dev] [--check] [--rollback] [--yes]`:

1. Fetch the manifest. Verify the signature against `PINNED_PUBKEYS` plus the optional
   `settings.update_pubkeys` (for self-hosted mirrors) before reading any field.
2. Refuse in any of these cases:
   - the channel does not match;
   - the release would be a downgrade (use `--rollback`);
   - there is a **schema gap**. `min_schema_version` is the oldest `state.db` schema the new
     release can migrate *from* (`MIN_UPGRADE_SCHEMA`). If the local `SCHEMA_VERSION` is
     lower, you must upgrade through an intermediate release first. A newer schema in the
     new release is fine, because `Database.init()` migrates forward.
3. Frozen binary: download into `auoreo.new` next to the binary and verify the sha256 (a
   mismatch deletes the file). Copy the current binary to `auoreo.prev` (N-1), then
   `os.replace` the new binary in and write `<home>/upgrade/receipt.json`
   `{action, from, to, channel, sha256, ts}`. Restart running servers afterwards.
   - **Windows**: a running `.exe` cannot be replaced. The new file is staged as
     `auoreo.new.exe` and the command prints the `move` commands to run once auoreo is
     closed. It never kills processes.
4. pip / uv tool / pipx / checkout installs: print `pip install -U auoreo`,
   `uv tool upgrade auoreo`, `pipx upgrade auoreo` or `git pull && uv sync`. Beta and dev
   channels add the pre-release flag. These installs never swap files.
5. `--rollback` swaps `auoreo.prev` back in, and the current binary becomes the new `.prev`.
   A `state.db` already migrated by the newer release may refuse to open with the older
   binary ("schema newer than this build"). Restore a backup in that case.

## Container

```bash
docker build -t auoreo .                                   # web + uv + slim runtime
docker run -p 4096:4096 -e AUOREO_SERVER_PASSWORD=change-me -v auoreo-data:/data auoreo
AUOREO_SERVER_PASSWORD=change-me docker compose up --build  # compose.yaml
```

- The image binds `0.0.0.0` (`AUOREO_HOST`). `auoreo serve` refuses to bind a non-loopback
  address without `AUOREO_SERVER_PASSWORD` (exit 2), so the password is **required**.
  `/api/health` is unauthenticated, so the `HEALTHCHECK` can use it. Every other route needs
  `Authorization: Bearer <password>`.
- State lives in the `/data` volume (`AUOREO_HOME`). The runtime runs as the non-root user
  `auoreo` (uid 10001).
- Extras: the default build includes `--extra rtc`. Add more with
  `--build-arg EXTRAS="--extra rtc --extra redis --extra otel"`. Heavy stacks
  (`rerank-local`/torch, perception models) are left out on purpose.
- Multi-worker: build with the `redis` extra, then run
  `AUOREO_WORKERS=2 AUOREO_REDIS_URL=redis://redis:6379/0 docker compose --profile redis up`.
  Sticky sessions at the load balancer are still required.

## Binaries locally

```bash
(cd web && npm ci && npm run build)
uv run --with 'pyinstaller>=6.22,<7' python scripts/build_binary.py [--platform-name]
./dist/auoreo --version && AUOREO_GATEWAY=fake ./dist/auoreo doctor
```

The spec (`packaging/pyinstaller/auoreo.spec`) bundles:

- the definition folders and `web/dist` at the bundle root, which `bundled_root()` resolves
  through `sys._MEIPASS`;
- the Alembic scripts;
- sqlite-vec's `vec0` loadable extension;
- uvicorn, litellm and tiktoken data;
- the third-party modules imported by tool handlers and backends (found by an AST scan in
  `packaging/pyinstaller/collect.py`).

Heavy optional stacks are excluded (`EXCLUDED_MODULES`: playwright, torch, perception,
local voice). Install the wheel if you need them. Nuitka is a P2 spike.

## Checks

`tests/unit/test_packaging.py` covers:

- Dockerfile structure (stages, non-root `USER`, `HEALTHCHECK`, no secrets in `ENV`/`ARG`);
- compose;
- release workflow structure and the conditional signing steps;
- the version guard;
- manifest signing;
- PyInstaller inputs;
- real `uv build --wheel` runs from a temporary copy of the project (contents, plus failure
  without `web/dist` and the `AUOREO_ALLOW_NO_WEB_DIST=1` escape hatch).

Locally, `hadolint Dockerfile` and `actionlint` are clean.
