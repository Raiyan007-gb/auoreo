// AUOREO desktop shell: spawn `auoreo serve --port 0` with a per-launch password, wait for
// its discovery line, authenticate the window with an HttpOnly cookie (never a URL token)
// and show the bundled web UI (web/dist, served by the binary) in a sandboxed BrowserWindow.
"use strict";

const { app, BrowserWindow, dialog, session, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { findBinary, isAppUrl, parseListening, randomToken, tokenCookie, updateChannel } = require("./lib.cjs");

const START_TIMEOUT_MS = 60_000;
const channel = updateChannel(app.getVersion(), process.env);
let server = null;
let serverUrl = null;
let win = null;

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  app.whenReady().then(start).catch(fail);
}

function fail(err) {
  dialog.showErrorBox("AUOREO could not start", String(err instanceof Error ? err.message : err));
  app.quit();
}

function startServer(token) {
  const binary = findBinary(process.platform, {
    resourcesPath: app.isPackaged ? process.resourcesPath : path.join(__dirname, "resources"),
    pathEnv: process.env.PATH,
    env: process.env,
  });
  if (!binary) {
    throw new Error("auoreo binary not found: reinstall the app, or install the CLI (npm i -g auoreo / uv tool install auoreo).");
  }
  const child = spawn(binary, ["serve", "--port", "0"], {
    env: { ...process.env, AUOREO_SERVER_PASSWORD: token },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  server = child;
  return new Promise((resolve, reject) => {
    let buffered = "";
    const timer = setTimeout(() => reject(new Error(`server did not start within ${START_TIMEOUT_MS / 1000}s`)), START_TIMEOUT_MS);
    const onData = (chunk) => {
      const text = chunk.toString();
      process.stdout.write(`[auoreo] ${text}`);
      buffered = (buffered + text).slice(-4096);
      const url = parseListening(buffered);
      if (url) {
        clearTimeout(timer);
        resolve(url);
      }
    };
    child.stdout.on("data", onData);
    child.stderr.on("data", onData);
    child.on("error", (e) => reject(e));
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      server = null;
      if (!serverUrl) reject(new Error(`server exited before listening (code ${code}, signal ${signal})`));
      else if (!app.isQuitting) fail(new Error(`server stopped unexpectedly (code ${code}, signal ${signal})`));
    });
  });
}

function hardenNavigation(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url) && !isAppUrl(url, serverUrl)) void shell.openExternal(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url, serverUrl)) {
      event.preventDefault();
      if (/^https?:/.test(url)) void shell.openExternal(url);
    }
  });
}

async function start() {
  const token = randomToken();
  serverUrl = await startServer(token);
  await session.defaultSession.cookies.set(tokenCookie(serverUrl, token));
  // Nothing but our own origin gets camera/mic/notification prompts granted implicitly.
  session.defaultSession.setPermissionRequestHandler((wc, _permission, cb) => cb(isAppUrl(wc.getURL(), serverUrl)));

  win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 720,
    minHeight: 480,
    title: "AUOREO",
    backgroundColor: "#18181b",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      additionalArguments: [`--auoreo-version=${app.getVersion()}`, `--auoreo-channel=${channel}`],
    },
  });
  hardenNavigation(win.webContents);
  win.once("ready-to-show", () => win.show());
  win.on("closed", () => {
    win = null;
  });
  await win.loadURL(`${serverUrl}/sessions`);
  setupUpdater();
}

/** electron-updater only in packaged builds; the channel mirrors `auoreo upgrade`. */
function setupUpdater() {
  if (!app.isPackaged || process.env.AUOREO_DISABLE_UPDATES) return;
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch {
    return; // not bundled (dev install without the dependency)
  }
  autoUpdater.channel = channel;
  autoUpdater.allowPrerelease = channel !== "stable";
  autoUpdater.allowDowngrade = false;
  autoUpdater.checkForUpdatesAndNotify().catch((e) => console.error("update check failed:", e));
}

function stopServer() {
  const child = server;
  if (!child || child.exitCode !== null) return;
  child.kill(); // SIGTERM (TerminateProcess on Windows); uvicorn shuts down gracefully
  setTimeout(() => {
    if (child.exitCode === null) child.kill("SIGKILL");
  }, 5_000).unref();
}

app.on("before-quit", () => {
  app.isQuitting = true;
  stopServer();
});
app.on("window-all-closed", () => app.quit());
process.on("exit", stopServer);
