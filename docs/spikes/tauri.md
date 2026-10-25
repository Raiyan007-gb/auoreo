# Spike: Tauri shell (spec L1055: "spike only if binary size or signing dominates Electron")

**Decision:** keep **Electron** (`desktop/`) as the default desktop shell. Keep
`desktop-tauri/` as an experimental scaffold, which builds and runs on Linux. Its size
advantage is real, but it does not dominate the *shipped* app, because the `auoreo` server
binary (~200 MB PyInstaller onefile) is bundled either way. Signing effort is the same for both
shells. The Tauri scaffold also still lacks Electron's cookie-based authentication of the
window.

## What was built

`desktop-tauri/src-tauri/` (Tauri 2.12.5, wry 0.57.1), with the same contract as
`desktop/main.cjs`:

1. Spawn `auoreo serve --port 0` (`$AUOREO_BINARY` or `auoreo` on PATH) with a per-launch
   random `AUOREO_SERVER_PASSWORD`.
2. Parse the `server listening on <url>` discovery line (unit-tested, mirrors
   `desktop/lib.cjs`).
3. Open a `WebviewWindow` on `<url>/sessions`, with no Tauri IPC exposed to the remote page.
4. Kill the server on `RunEvent::Exit`.

`tauri.conf.json` has bundling off and no static windows; `static/index.html` is only the
required `frontendDist` placeholder. `Cargo.lock` and `target/` are git-ignored.

## Measurements (this container, linux-x64, 4 cores shared with a concurrent Nuitka build)

| Step | Result |
|---|---|
| Toolchain | `cargo 1.97.0`, `rustc 1.97.0` present |
| First `cargo check` | crates downloaded fine, then **failed** in `gdk-sys`: system library `gdk-3.0` not found (35 s) |
| After `apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev` (webkit2gtk 2.52.6, GTK 3.24.41) | `cargo check` ✅ in 1 m 13 s |
| `cargo test` | ✅ 1 passed (93 s, including the debug build) |
| `cargo build --release` (LTO, `opt-level = "s"`, strip, `panic = "abort"`) | ✅ 170 s, binary **4.57 MB** (4,572,832 bytes), dynamically linked against the system WebKitGTK (129 shared libraries in `ldd`) |
| Smoke run under Xvfb with the venv's `auoreo` | ✅ spawned `auoreo serve`, discovery line after 5.1 s, `/api/health` 200 on the printed URL; RSS: shell 160 MB, WebKitWebProcess 337 MB, WebKitNetworkProcess 55 MB, server 158 MB |
| Shell killed with SIGTERM | ⚠️ the `auoreo serve` child was **left running**: `RunEvent::Exit` does not fire on a signal. Needs `PR_SET_PDEATHSIG` (Linux), a job object (Windows) or a signal handler |
| Electron 45.0.0 runtime, for comparison (`npm i electron@45` here) | download zip **123.9 MB**, unpacked `dist/` **274 MB** (`electron` executable 224 MB) |

Not measured here: macOS/Windows builds, installers (`bundle.active: false`), code signing,
notarisation, AV scans, cold start on real hardware.

## Size and signing comparison

| | Electron 45 (desktop/) | Tauri 2 (desktop-tauri/) |
|---|---|---|
| Shell runtime | 274 MB unpacked / 124 MB zip on linux-x64 (measured) | 4.6 MB binary (measured) + the OS webview: WebKitGTK on Linux (a distro package dependency), WebView2 on Windows (preinstalled on Windows 11, evergreen bootstrapper otherwise), WKWebView on macOS (documented by Tauri) |
| `auoreo` server bundled next to it | ~200 MB PyInstaller onefile (the brief's figure; same for both shells) | same |
| Approx. shipped total | ≈ 330–480 MB unpacked | ≈ 205 MB + system webview |
| Rendering engine | pinned Chromium: same everywhere | three engines (WebKitGTK, Edge WebView2, WKWebView); the SPA, SSE, WebSocket and WebRTC (voice) need testing on each, and WebKitGTK's WebRTC support depends on the distro build |
| Signing / notarisation | electron-builder signs app + helpers; the server binary is signed separately (docs/release.md) | `tauri build` signs app (+ the `externalBin` sidecar); the server binary is signed the same way. The same certificates and Apple notarisation are needed: no saving |
| Auto-update | `electron-updater`, wired to `auoreo upgrade` channels | `tauri-plugin-updater` (own signature format, documented by Tauri): a second update-signing key and feed |
| Window auth | HttpOnly `auoreo_token` cookie set before load (no token in URLs) | not implemented: no Tauri v2 cookie-setting API was confirmed in this spike; needs a one-time login exchange endpoint or a cookie API |
| Toolchain in CI | Node only | Rust + per-OS webview dev packages (Linux: the apt list above) |

## Decision

- The shell runtime is 60× smaller (4.6 MB vs 274 MB), but the server sidecar is ~200 MB in
  both cases. The download shrinks by roughly 1.6–2.3×; it does not drop by orders of
  magnitude.
- Signing does not get simpler.
- Tauri costs three rendering engines to test, a second updater and key, and the missing
  window authentication.
- The spec's condition ("only if binary size or signing dominates") is **not met**.
  **Electron stays the default.**

## Follow-ups (only if Tauri is revisited)

1. Window auth: a `POST /api/auth/exchange` one-time code → HttpOnly cookie, or a Tauri cookie
   API if one lands. Then load `<url>/sessions`.
2. Child lifetime: `PR_SET_PDEATHSIG` on Linux, a job object on Windows, SIGTERM forwarding on
   macOS.
3. Bundle `auoreo` as `bundle.externalBin` (the target-triple-suffixed sidecar), plus
   `tauri-plugin-updater` mapped onto `auoreo upgrade` channels.
4. A WebRTC/voice check on WebKitGTK and WebView2 before any switch.
5. Re-evaluate if the server binary shrinks a lot (e.g. a much smaller Nuitka or wheel-based
   install), since the shell would then dominate the download.
