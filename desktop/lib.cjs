// Pure helpers for the Electron main process (unit-tested without Electron: desktop/test).
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

/** The cookie `auoreo serve` accepts as a token (api/deps.py COOKIE). */
const TOKEN_COOKIE = "auoreo_token";
const CHANNELS = ["stable", "beta", "dev"];

/** First `http(s)://…` URL from the server's discovery line, or null.
 * `auoreo serve` prints `server listening on http://127.0.0.1:PORT` (optionally followed by
 * ` (N workers; …)` or ` and http://…`) once the app is ready. */
function parseListening(stdout) {
  const m = /server listening on (https?:\/\/[^\s()]+)/.exec(String(stdout));
  return m ? m[1].replace(/[.,;]+$/, "") : null;
}

function exeName(platform) {
  return platform === "win32" ? "auoreo.exe" : "auoreo";
}

/**
 * Locate the `auoreo` binary: $AUOREO_BINARY → the copy bundled under
 * `<resources>/bin/` (electron-builder extraResources) → PATH. Returns null when none exists.
 * `paths` = {resourcesPath, pathEnv, env, exists} (all optional, injectable for tests).
 */
function findBinary(platform, paths = {}) {
  const exists = paths.exists ?? ((p) => fs.existsSync(p) && fs.statSync(p).isFile());
  const env = paths.env ?? {};
  const name = exeName(platform);
  const sep = platform === "win32" ? ";" : ":";
  const join = platform === "win32" ? path.win32.join : path.posix.join;
  const candidates = [];
  if (env.AUOREO_BINARY) candidates.push(env.AUOREO_BINARY);
  if (paths.resourcesPath) candidates.push(join(paths.resourcesPath, "bin", name));
  for (const dir of String(paths.pathEnv ?? "").split(sep).filter(Boolean)) candidates.push(join(dir, name));
  return candidates.find((p) => exists(p)) ?? null;
}

/** `session.cookies.set()` details that authenticate every same-origin request (fetch, SSE,
 * WebSocket) without putting the token in a URL: host-only, HttpOnly, SameSite=strict,
 * session-lifetime, scoped to `/` of the server origin. */
function tokenCookie(url, token) {
  const origin = new URL(url);
  if (!/^https?:$/.test(origin.protocol)) throw new Error(`unsupported server URL: ${url}`);
  if (!token) throw new Error("empty token");
  return {
    url: `${origin.protocol}//${origin.host}/`,
    name: TOKEN_COOKIE,
    value: token,
    path: "/",
    httpOnly: true,
    secure: origin.protocol === "https:",
    sameSite: "strict",
  };
}

/** A fresh per-launch server password (256 bits, URL-safe). */
function randomToken() {
  return crypto.randomBytes(32).toString("base64url");
}

/** Update channel mirroring `auoreo upgrade`: $AUOREO_CHANNEL if valid, else derived from the
 * app version's prerelease tag (1.2.0-beta.1 → beta, 1.2.0-dev.3 → dev, else stable). */
function updateChannel(version, env = {}) {
  const wanted = String(env.AUOREO_CHANNEL ?? "").toLowerCase();
  if (CHANNELS.includes(wanted)) return wanted;
  const pre = /-([a-z]+)/i.exec(String(version))?.[1]?.toLowerCase();
  if (pre === "dev") return "dev";
  if (pre === "beta" || pre === "alpha" || pre === "rc") return "beta";
  return "stable";
}

/** Only the local server's origin may be shown in the window; everything else opens externally. */
function isAppUrl(target, serverUrl) {
  try {
    return new URL(target).origin === new URL(serverUrl).origin;
  } catch {
    return false;
  }
}

module.exports = { parseListening, findBinary, tokenCookie, randomToken, updateChannel, isAppUrl, TOKEN_COOKIE, CHANNELS };
