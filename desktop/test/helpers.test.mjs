// node --test desktop/test : pure main-process helpers, no Electron required.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { parseListening, findBinary, loginRequest, RELOGIN_MS, randomToken, updateChannel, isAppUrl, TOKEN_COOKIE } = require(
  join(root, "lib.cjs"),
);

test("parseListening extracts the discovery URL from serve output", () => {
  assert.equal(parseListening("INFO: started\nserver listening on http://127.0.0.1:53211\n"), "http://127.0.0.1:53211");
  assert.equal(
    parseListening("server listening on http://127.0.0.1:4096 (4 workers; sticky sessions required)"),
    "http://127.0.0.1:4096",
  );
  assert.equal(
    parseListening("server listening on http://127.0.0.1:4096 and unix:/tmp/a.sock"),
    "http://127.0.0.1:4096",
  );
  assert.equal(parseListening("server listening on http://[::1]:8080"), "http://[::1]:8080");
  assert.equal(parseListening("exec-server ready\nINFO: Waiting for application startup."), null);
  assert.equal(parseListening(""), null);
});

test("findBinary: AUOREO_BINARY, then bundled resources, then PATH", () => {
  const have = new Set(["/env/auoreo", "/res/bin/auoreo", "/usr/bin/auoreo", "/opt/x/auoreo"]);
  const exists = (p) => have.has(p);
  const base = { resourcesPath: "/res", pathEnv: "/nope:/usr/bin:/opt/x", exists };
  assert.equal(findBinary("linux", { ...base, env: { AUOREO_BINARY: "/env/auoreo" } }), "/env/auoreo");
  assert.equal(findBinary("linux", base), "/res/bin/auoreo");
  assert.equal(findBinary("darwin", { ...base, resourcesPath: "/elsewhere" }), "/usr/bin/auoreo");
  assert.equal(findBinary("linux", { pathEnv: "/nope", exists }), null);
});

test("findBinary uses auoreo.exe and ';' PATH on Windows", () => {
  const have = new Set(["C:\\Program Files\\AUOREO\\resources\\bin\\auoreo.exe", "C:\\tools\\auoreo.exe"]);
  const exists = (p) => have.has(p);
  assert.equal(
    findBinary("win32", { resourcesPath: "C:\\Program Files\\AUOREO\\resources", pathEnv: "C:\\tools", exists }),
    "C:\\Program Files\\AUOREO\\resources\\bin\\auoreo.exe",
  );
  assert.equal(findBinary("win32", { pathEnv: "C:\\nope;C:\\tools", exists }), "C:\\tools\\auoreo.exe");
});

test("loginRequest: same-origin POST /api/auth/login with the password; cookie name matches the server", () => {
  const r = loginRequest("http://127.0.0.1:53211/sessions?x=1", "s3cret");
  assert.equal(r.url, "http://127.0.0.1:53211/api/auth/login");
  assert.equal(r.init.method, "POST");
  assert.equal(r.init.headers.origin, "http://127.0.0.1:53211");
  assert.deepEqual(JSON.parse(r.init.body), { password: "s3cret" });
  assert.equal(loginRequest("https://box.local:4096", "t").url, "https://box.local:4096/api/auth/login");
  assert.throws(() => loginRequest("file:///tmp/index.html", "t"), /unsupported/);
  assert.throws(() => loginRequest("http://127.0.0.1:1", ""), /empty token/);
  assert.ok(RELOGIN_MS < 15 * 60 * 1000);
  const auth = readFileSync(join(root, "..", "src", "auoreo", "api", "session_auth.py"), "utf8");
  assert.match(auth, new RegExp(`COOKIE = "${TOKEN_COOKIE}"`));
});

test("randomToken is long, URL-safe and unique", () => {
  const a = randomToken();
  assert.match(a, /^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(a, randomToken());
});

test("updateChannel mirrors auoreo upgrade channels", () => {
  assert.equal(updateChannel("0.1.0"), "stable");
  assert.equal(updateChannel("0.2.0-beta.1"), "beta");
  assert.equal(updateChannel("0.2.0-rc.1"), "beta");
  assert.equal(updateChannel("0.2.0-dev.7"), "dev");
  assert.equal(updateChannel("0.2.0", { AUOREO_CHANNEL: "DEV" }), "dev");
  assert.equal(updateChannel("0.2.0-beta.1", { AUOREO_CHANNEL: "bogus" }), "beta");
});

test("isAppUrl only admits the server origin", () => {
  const s = "http://127.0.0.1:53211";
  assert.equal(isAppUrl("http://127.0.0.1:53211/approvals", s), true);
  assert.equal(isAppUrl("http://127.0.0.1:9999/", s), false);
  assert.equal(isAppUrl("https://example.com/", s), false);
  assert.equal(isAppUrl("not a url", s), false);
});

test("main/preload keep the renderer locked down", () => {
  const main = readFileSync(join(root, "main.cjs"), "utf8");
  for (const flag of ["contextIsolation: true", "sandbox: true", "nodeIntegration: false", "requestSingleInstanceLock"]) {
    assert.ok(main.includes(flag), flag);
  }
  assert.ok(!/loadURL\([^)]*token/i.test(main), "token must never be put in the URL");
  assert.match(main, /if \(!app\.isPackaged/);
  const preload = readFileSync(join(root, "preload.cjs"), "utf8");
  assert.match(preload, /exposeInMainWorld\("auoreoDesktop", Object\.freeze\(\{ version: arg\("version"\), channel: arg\("channel"\) \}\)\)/);
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  assert.ok(pkg.devDependencies.electron && pkg.devDependencies["electron-builder"]);
  assert.equal(pkg.main, "main.cjs");
});
