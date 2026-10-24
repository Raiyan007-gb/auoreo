---
name: native-binary
description: Build and release per-OS standalone native binaries (PyInstaller/Nuitka/Bun compile) for the agent platform — onefile builds, per-OS matrix, Windows code-signing, GitHub Release assets, npm delivery. Use when working on scripts/build.ts, scripts/version.ts, or the Part J native-binary channel.
---

# Native binaries — PyInstaller / Nuitka (plan.md Part J)

The "native binary" channel ships a per-OS/arch standalone executable so end-users get a real binary (`agent` / `agent.exe`) with no runtime to install. opencode delivers the same via its `bin/opencode` Node launcher + `opencode-<platform>-<arch>` packages (see `npm` skill). Choose a tool:

| Tool | Pros | Cons | Current |
|---|---|---|---|
| **PyInstaller** | Mature, onefile, cross-OS support, wide hook coverage | Larger output; startup extracts to temp dir; slower start in onefile mode | 6.22.x (6.22.3, 2026-09; Py ≤3.15 since 6.21) |
| **Nuitka** | Compiles to C, smaller + faster, `--mode=onefile`/`--project` (reads `pyproject.toml`) | Longer compile; needs a C compiler (Zig supported as fallback) | 4.2.x (4.2.2 current; Py 3.14 official, 3.15 experimental) |
| **Bun compile** (TS path) | `bun build --compile [--target=bun-<os>-<arch>[-musl]]` single binary for `web/*` tooling; **can cross-compile** | Not for the Python engine | — |

For this Python-first repo, **PyInstaller is the pragmatic default** for `agent` (the Python engine), with Nuitka as an optimization.

Context7: `/websites/pyinstaller_en_stable` (+ `/pyinstaller/pyinstaller/v6.22.3` changelog) · `/websites/nuitka_net` · `/websites/bun_sh` · `/azure/login`.

## PyInstaller onefile (agent_cli)

```bash
uv run pyinstaller --onefile \
  --name agent \
  --paths src \
  --collect-all agent_schema --collect-all agent_core --collect-all agent_api \
  src/agent_cli/main.py
# → dist/agent (linux/macos) or dist/agent.exe (windows)
```

- Use **onefolder (`--onedir`)** for faster startup + easier debugging during dev; ship `--onefile` for end-users (plan.md Part J decision matrix: "⭐ signed exe, no deps").
- Collect data files (web UI `dist/`, templates, skills) with `--add-data "web/app/dist:web/app/dist"` — PyInstaller ≥ 6.0 accepts **POSIX `source:dest` syntax on Windows too** (now the recommended cross-platform form; the legacy `;` still works but is discouraged). Locate them at runtime relative to the bundle root:

```python
import sys
from pathlib import Path

BASE_DIR = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
web_dir = Path(BASE_DIR) / "web" / "app" / "dist"
# PyInstaller ≥4.3 sets module __file__ to its in-bundle path, so __file__-relative lookups
# also work frozen (docs' preferred form) — place data with a matching --add-data dest.
```

- **No cross-compile** (PyInstaller and Nuitka): build each OS on its own runner (macOS→macOS, Windows→Windows, Linux→Linux). CI uses a per-OS matrix (see `github-actions` skill build-cli job).

## Per-OS/arch matrix (mirror opencode publish.yml)

| Platform | Arch | Build host | Variants |
|---|---|---|---|
| macOS | x64, arm64 | macos-14+ (x64 & arm) | Universal optional |
| Windows | x64, arm64 | windows-2022+ | x64-baseline (no AVX2) |
| Linux | x64, arm64 | ubuntu-24.04 | glibc + **musl** (Alpine), avx2/baseline |

- musl: build inside an Alpine/musl container so `agent` runs on Alpine; detect glibc vs musl like opencode's `bin/opencode` (check `/etc/alpine-release` or `ldd --version`).
- Name artifacts `agent-<platform>-<arch>[-(baseline|musl)]`; upload each to GitHub Release + npm package (see `npm` skill launcher).
- Note for Bun-compiled tooling: current Bun ships one x64 binary (SSE4.2 floor, AVX2/AVX-512 picked at runtime); `-baseline`/`-modern` target suffixes are accepted but resolve to the same binary, so a separate baseline build is redundant there.

## Windows code-signing (required for Windows users)

Unsigned `.exe` → SmartScreen blocks + AV warnings (plan.md Part J decision matrix). Use **Azure Trusted Signing** in CI (same as opencode):

```yaml
- uses: azure/login@v3   # OIDC: job needs `permissions: id-token: write`
  with: { client-id: ..., tenant-id: ..., subscription-id: ... }
- uses: azure/artifact-signing-action@v2   # v2 (2026-05) — rebranded from Trusted Signing; new inputs file-digest/timestamp-rfc3161
  with:
    endpoint: ${{ vars.AZURE_TRUSTED_SIGNING_ENDPOINT }}
    signing-account-name: ${{ secrets.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME }}
    certificate-profile-name: ${{ secrets.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE }}
    files: |
      dist/agent-windows-x64/agent.exe
      dist/agent-windows-x64-baseline/agent.exe
# verify: Get-AuthenticodeSignature dist/.../agent.exe  (must be Valid)
# ⚠ the action documents NO Windows ARM (arm64) signing support — verify before signing win-arm64
# then: repack .zip and `gh release upload` to the tag
```

macOS binaries need Developer ID + notarization (`xcrun notarytool`, or electron-builder flow for the desktop app — see `electron` skill).

## Release wiring (scripts/build.ts + version.ts)

- `scripts/build.ts` — orchestrates the matrix: compile, name artifacts, sign (Windows), repack `.zip`, upload to the GitHub Release for the current tag (uses `gh release upload --clobber`).
- `scripts/version.ts` — bumps version, drafts the GitHub Release, outputs `version`/`tag` for the rest of `publish.yml` (plan.md Part J pipeline).
- Deliver the binary two ways: (1) GitHub Release `.zip` assets (curl/install script), (2) npm package as the platform-specific optional dep consumed by the `bin/agent` launcher (see `npm` skill).

## Rules for this repo

- Build in CI only — never build binaries locally and commit them; reproducible via pinned base images + `uv.lock`.
- Keep the frozen app slim: exclude dev deps, compress web `dist/`, don't bundle secrets.
- `agent --version` must read the same version string the tag/npm/PyPI use (single source in `scripts/version.ts`).
- Test the exact artifact: release acceptance installs the `.zip` binary on a clean machine and runs `agent run` (plan.md Part K).