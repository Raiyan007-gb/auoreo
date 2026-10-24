---
name: electron
description: Package the web UI as a desktop app (web/desktop) with Electron + electron-builder — main/renderer split, code signing/notarization, auto-update. Use in Phase 4+ when shipping desktop builds.
---

# Electron — desktop shell (web/desktop; Phase 4+, plan.md Part J)

The desktop app is a thin Electron shell around the `web/app` SPA (same client single-port pattern — plan.md Part C.1). Main process spawns/hosts the `agent_api` server (or attaches to a remote one) and a BrowserWindow loads the SPA.

## Main vs renderer

- **main.ts** (Node) — app lifecycle, creates BrowserWindow, spawns `agent serve --port 4096` (on demand) as a child process, routes IPC, `app.setAboutPanelOptions` etc.
- **preload.ts** (contextIsolation) — `contextBridge.exposeInMainWorld("agent", { baseUrl, events: subscribeAll })` for typed calls without exposing Node.
- **renderer** — the built `web/app` SPA (`dist/`).

```ts
// main.ts
import { app, BrowserWindow } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";

let serverProc: ChildProcess | undefined;
app.whenReady().then(() => {
  serverProc = spawn("agent", ["serve", "--port", "4096"], { stdio: "inherit" });
  const win = new BrowserWindow({
    width: 1200, height: 800,
    // contextIsolation/sandbox are already the defaults (nodeIntegration false) — keep them explicit, never relax
    webPreferences: { contextIsolation: true, sandbox: true, preload: path.join(__dirname, "preload.js") },
  });
  win.loadFile(path.join(__dirname, "../renderer/index.html"));  // or VITE_* base url
});
app.on("will-quit", () => serverProc?.kill());
```

- Security checklist (Electron security tutorial): validate `event.senderFrame.origin` in every `ipcMain.handle`; block unexpected navigation via `app.on("web-contents-created")` → `will-navigate` + `setWindowOpenHandler` (open external links with `shell.openExternal` only for allow-listed origins). A sandboxed preload can only `require("electron")` (`contextBridge`/`ipcRenderer`) — no Node modules there.
- Keep secrets out of the renderer: all key handling lives in main/preload; renderer only gets the http events.
- `--port` collision: reuse port discovery + fallback (bind port 0 → free port; pass `baseUrl` via IPC after spawn, like opencode SDK URL-detection).

## electron-builder (packaging)

```bash
npx electron-builder --mac --x64 --arm64 --win --linux
```

`electron-builder.config.ts` (electron-builder **v27** schema — Node ≥ 22.12, ESM; `npx electron-builder migrate-schema` rewrites v26 configs):

```ts
import type { Configuration } from "electron-builder";

export default {
  appId: "ai.agentplatform.desktop",
  productName: "Agent Platform",
  directories: { output: "release" },
  files: ["dist/**", "main/**"],
  mac: {
    target: ["dmg", "zip"],               // zip is REQUIRED for electron-updater (Squirrel.Mac); dmg alone breaks mac auto-update
    category: "public.app-category.developer-tools",
    sign: { hardenedRuntime: true },      // v27: all mac signing keys live under mac.sign (default true; required for notarization)
    notarize: true,
  },
  win: {
    target: ["nsis", "zip"],
    // v27: win.sign replaces azureSignOptions/signtoolOptions; auth via AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET
    sign: { type: "azure", publisherName: "<cert subject CN>", endpoint: "<Trusted Signing account endpoint (region URI)>",
            codeSigningAccountName: "<account>", certificateProfileName: "<profile>" },
  },
  linux: { target: ["AppImage", "deb"], category: "Development" },
  publish: { provider: "github", owner: "<org>", repo: "agent-platform" },
} satisfies Configuration;
```

- **Code signing:** macOS needs Developer ID + notarization — pass `CSC_LINK`/`CSC_KEY_PASSWORD` (electron-builder manages a temp keychain; opencode instead uses `apple-actions/import-codesign-certs`) plus **one** notarization credential set: `APPLE_API_KEY` (.p8 path)/`APPLE_API_KEY_ID`/`APPLE_API_ISSUER` or `APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID`. Stock Electron needs no custom entitlements file (don't add `allow-unsigned-executable-memory`). Windows uses **Azure Trusted Signing** via `win.sign` (plan.md Part J, github-actions skill). Unsigned = SmartScreen/Gatekeeper warnings (plan.md Part J decision matrix).
- Release pipeline parallel job `build-electron` in `publish.yml` (matrix per OS/arch like opencode's), gated to stable releases.

## Auto-update

electron-builder `publish` (GitHub) + `electron-updater` in main: `autoUpdater.checkForUpdatesAndNotify()` on launch (differential download of the mac zip / NSIS blockmap). Mirror the CLI `upgrade` flow so desktop stays current with the tag cadence (plan.md Part J).

- **v27 signed update manifests are required by default:** `latest*.yml` is Ed25519-signed at publish and verified before download — generate a key once with `electron-builder create-update-key`, store it as the `ELECTRON_BUILDER_UPDATE_SIGN_KEY` CI secret (publishing fails without it; `updateManifest: false` is the only opt-out).
- v27 electron-updater API changes: `quitAndInstall({ isSilent, isForceRunAfter })` (options object, not positional args); `autoInstallOnAppQuit = false` → `autoInstallEvent = "manual"`; `downloadUpdate()` resolves `{ updateFile }`.

Context7: `/electron/electron` (security), `/electron-userland/electron-builder` (config, signing, notarization, electron-updater).