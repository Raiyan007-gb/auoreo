# auoreo (npm)

`npm install -g auoreo` gives you the `auoreo` command backed by the native, self-contained
binary for your platform. Python is not required.

## How the launcher picks a binary

`bin/auoreo.cjs` tries these in order and runs the first one it finds with inherited stdio.
It passes the exit code through, and if the child dies from a signal, the launcher exits from
that same signal:

1. `AUOREO_BINARY=/path/to/auoreo`: an explicit override. If the path does not exist, the launcher fails instead of falling back.
2. **The platform package** `@auoreo/cli-<platform>-<arch>[-musl]`, installed automatically as an
   optional dependency. npm installs only the one whose `os`/`cpu`/`libc` match the host. The
   launcher detects musl because `process.report.getReport().header.glibcVersionRuntime` is
   absent there.
3. **A system `auoreo` on `PATH`**. The launcher skips itself here: its own symlink, npm's
   sh/`.cmd` shims, and any launcher re-entered through `AUOREO_NPM_LAUNCHER=1`.
4. **`uvx auoreo`** if [uv](https://docs.astral.sh/uv/) is installed. This runs the PyPI package.
5. Otherwise it prints an error that lists every install option and exits with code 1.

There is no AVX2 variant to choose from. The payload is a PyInstaller-frozen CPython built for
the baseline x86-64 ISA, so the AVX2/non-AVX2 split that spec L217 mentions for Bun-style
binaries does not apply.

## Release artifact contract

The release workflow builds the PyInstaller onefile binaries. `scripts/platforms.mjs` is the
single source of truth for how npm packages map to release artifacts:

| npm package                   | `os`/`cpu`/`libc`    | release artifact          | binary in package  |
|-------------------------------|----------------------|---------------------------|--------------------|
| `@auoreo/cli-linux-x64`       | linux / x64 / glibc  | `auoreo-linux-x64`        | `bin/auoreo`       |
| `@auoreo/cli-linux-arm64`     | linux / arm64 / glibc| `auoreo-linux-arm64`      | `bin/auoreo`       |
| `@auoreo/cli-linux-x64-musl`  | linux / x64 / musl   | `auoreo-linux-x64-musl`   | `bin/auoreo`       |
| `@auoreo/cli-darwin-arm64`    | darwin / arm64       | `auoreo-macos-arm64`      | `bin/auoreo`       |
| `@auoreo/cli-win32-x64`       | win32 / x64          | `auoreo-windows-x64.exe`  | `bin/auoreo.exe`   |

If the release workflow skips an artifact (musl, for example), the platform package for it is
not published. Users on that platform fall through to PATH or `uvx`.

## Versioning

The npm version follows `src/auoreo/__init__.py:__version__`. PEP 440 pre-releases map to
semver: `1.2.0b1` becomes `1.2.0-beta.1`, `rc2` becomes `-rc.2`, and `.dev3` becomes `-dev.3`.

```bash
node packages/npm/scripts/sync-version.mjs          # rewrite every package.json
node packages/npm/scripts/sync-version.mjs --check  # CI: fail when out of sync
```

## Publishing (release job)

```bash
node packages/npm/scripts/sync-version.mjs --check
node packages/npm/scripts/pack-platforms.mjs --artifacts ./artifacts --strict   # -> packages/npm/dist/<id>
for d in packages/npm/dist/*/; do npm publish "$d" --tag "$TAG" --access public; done
npm publish packages/npm --tag "$TAG"          # after the platform packages
```

`$TAG` follows the `auoreo upgrade` channel: `latest` for stable, then `beta` and `dev`.
Publish with npm trusted publishing (OIDC, `id-token: write`). No long-lived token is needed.

## Tests

```bash
node --test "packages/npm/test/*.test.mjs"
```
