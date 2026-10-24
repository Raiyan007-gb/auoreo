---
name: electron
description: Package the web UI as a desktop app (web/desktop) with Electron + electron-builder — main/renderer split, code signing/notarization, auto-update. Use in Phase 4+ when shipping desktop builds.
---

# Electron — desktop shell (web/desktop; Phase 4+, plan.md Part J)

The desktop app is a thin Electron shell around the `web/app` SPA (same client single-port pattern — plan.md Part C.1). Main process spawns/hosts the `agent_api` server (or attaches to a remote one) and a BrowserWindow loads the SPA.

## Main vs renderer

- **main.ts** (Node) — app lifecycle, creates BrowserWindow, spawns `agent serve --port 4096` (on demand) as a child process, routes IPC, `app.setAboutPanel` etc.
- **preload.ts** (contextIsolation) — `contextBridge.exposeInMainWorld("agent", { baseUrl, events: subscribeAll })` for typed calls without exposing Node.
- **renderer** — the built `web/app` SPA (`dist/`).

```ts
// main.ts
import { app, BrowserWindow } from "electron";
import { spawn } from "node:child_process";

let serverProc: ChildProcess | undefined;
app.whenReady().then(() => {
  serverProc = spawn("agent", ["serve", "--port", "4096"], { stdio: "inherit" });
  const win = new BrowserWindow({ width: 1200, height: 800, webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload.js") } });
  win.loadFile(path.join(__dirname, "../renderer/index.html"));  // or VITE_* base url
});
app.on("will-quit", () => serverProc?.kill());
```

- Keep secrets out of the renderer: all key handling lives in main/preload; renderer only gets the http events.
- `--port` collision: reuse port discovery + fallback (bind port 0 → free port; pass `baseUrl` via IPC after spawn, like opencode SDK URL-detection).

## electron-builder (packaging)

```bash
npx electron-builder --mac --x64 --arm64 --win --linux
```

`electron-builder.config.ts` (`/electron-userland/electron-builder` context7):

```ts
export default {
  appId: "ai.agentplatform.desktop",
  productName: "Agent Platform",
  directories: { output: "release" },
  files: ["dist/**", "main/**"],
  mac: { target: "dmg", category: "public.app-category.developer-tools" },
  win: { target: ["nsis", "zip"] },
  linux: { target: ["AppImage", "deb"], category: "Development" },
  publish: { provider: "github", owner: "<org>", repo: "agent-platform" },
};
```

- **Code signing:** macOS needs Developer ID + notarization (opencode uses `apple-actions/import-codesign-certs` + API key in CI); Windows uses **Azure Trusted Signing** (plan.md Part J, github-actions skill). Unsigned = SmartScreen/Gatekeeper warnings (plan.md Part J decision matrix).
- Release pipeline parallel job `build-electron` in `publish.yml` (matrix per OS/arch like opencode's), gated to stable releases.

## Auto-update

electron-builder `publish` (GitHub) + `electron-updater` in main: check on launch, download+install delta/zip. Mirror the CLI `upgrade` flow so desktop stays current with the tag cadence (plan.md Part J).