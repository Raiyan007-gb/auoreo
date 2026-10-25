# AUOREO desktop (Electron shell)

The desktop app is a thin shell around the local server. It does not reimplement the UI. It
shows the same `web/dist` SPA that `auoreo serve` hosts on its single port.

## What it does

1. **Takes the single-instance lock.** If you launch the app a second time, the existing window comes to the front.
2. **Finds the `auoreo` binary** (`lib.cjs:findBinary`) in this order: `$AUOREO_BINARY`, then the copy bundled at
   `<resources>/bin/auoreo[.exe]` through electron-builder `extraResources`, then `PATH`.
3. **Spawns `auoreo serve --port 0`** with a fresh random `AUOREO_SERVER_PASSWORD` (256-bit) for each launch.
4. **Waits for the discovery line** `server listening on http://…`, parsed by `parseListening`, with a 60 s timeout.
5. **Authenticates the window with an `auoreo_token` cookie.** The cookie is HttpOnly, host-only and `SameSite=strict`
   (`tokenCookie`), and the server already accepts it (`api/deps.py`, `api/ws.py`). The token never appears in a URL,
   in the renderer's JS or in localStorage. Fetch, SSE `/api/events` and WS `/ws` all send the cookie automatically.
6. **Opens a locked-down `BrowserWindow`** at `<url>/sessions`: `contextIsolation: true`, `sandbox: true` and
   `nodeIntegration: false`. Navigation is restricted to the server origin, other links open in the system browser,
   and permission prompts are granted only for that origin.
7. **Stops the server on quit**: SIGTERM first, then SIGKILL after 5 s.

`preload.cjs` exposes exactly `window.auoreoDesktop = { version, channel }`. It has no IPC and no Node access.

## Updates

`electron-updater` is wired in only when `app.isPackaged`, and `AUOREO_DISABLE_UPDATES` turns it off. The channel
follows `auoreo upgrade`: `stable`, `beta` or `dev`, taken from `$AUOREO_CHANNEL` or from the app version's
pre-release tag (`-beta.N` / `-rc.N` give beta, `-dev.N` gives dev). Prerelease channels set `allowPrerelease`.

## Develop

Electron is not part of the root or `web/` install. Install it here only when you need to run the shell:

```bash
cd desktop
npm install                       # electron 45, electron-builder 26, electron-updater 6
AUOREO_BINARY=$(cd .. && uv run which auoreo) npm start
npm test                          # pure helpers only, no Electron needed (CI runs this)
```

## Release

The release workflow, which belongs to the binaries/release workstream, should do the following:

1. Stage the PyInstaller artifact for each target:
   - `auoreo-macos-arm64` goes to `resources/bin/mac-arm64/auoreo`
   - `auoreo-linux-x64` goes to `resources/bin/linux-x64/auoreo`
   - `auoreo-linux-arm64` goes to `resources/bin/linux-arm64/auoreo`
   - `auoreo-windows-x64.exe` goes to `resources/bin/win-x64/auoreo.exe`
2. Set `version` in `desktop/package.json` to the release version.
3. Set `AUOREO_CHANNEL=stable|beta|dev`. The `publish.channel` field uses it, and electron-updater reads the
   matching `<channel>*.yml`.
4. Run `npx electron-builder --config electron-builder.yml --publish always` on macOS, Windows and Linux runners
   with `GH_TOKEN`.

Signing is driven by environment variables and never committed. The configuration documents it, but CI here does
not run it:

| Platform | Env |
|---|---|
| macOS sign | `CSC_LINK`, `CSC_KEY_PASSWORD` (Developer ID Application .p12) |
| macOS notarize | `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER` (or `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`) |
| Windows | `WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD`, or Azure Trusted Signing (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) |
| Linux | none (AppImage and deb are unsigned) |

Tauri remains a spike only (spec L1055). Native mobile is deferred, and the installable PWA covers phones and tablets.
