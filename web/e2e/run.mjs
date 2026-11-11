#!/usr/bin/env node
// `npm run e2e`: with BASE_URL set, run Playwright against it. Without it, boot a keyless
// backend (`AUOREO_GATEWAY=fake uv run auoreo serve --port 0`, fresh AUOREO_HOME) from the
// repo root, wait for "server listening on …", run the suite, then stop the server.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = resolve(here, "..");
const repoRoot = resolve(webDir, "..");
// `--visual`: run only the screenshot-comparison spec (it needs a fresh, empty server and is kept
// out of the normal run). `npm run e2e:visual -- --update-snapshots` rewrites this platform's baselines.
const args = process.argv.slice(2);
const visual = args.includes("--visual");
if (visual) process.env.VISUAL = "1";
// The file goes first: `--update-snapshots` takes an optional value and would swallow a path after it.
const extra = visual ? ["visual.spec.ts", ...args.filter((a) => a !== "--visual")] : args;

function runPlaywright(baseUrl) {
  return new Promise((done) => {
    // Node + the CLI file directly (not `npx`): no shell needed, so Windows (npx.cmd) and paths
    // with spaces or quotes work the same everywhere.
    const cli = join(webDir, "node_modules", "@playwright", "test", "cli.js");
    const p = spawn(process.execPath, [cli, "test", "-c", join(here, "playwright.config.ts"), ...extra], {
      cwd: webDir,
      stdio: "inherit",
      env: { ...process.env, BASE_URL: baseUrl },
    });
    p.on("exit", (code) => done(code ?? 1));
  });
}

// --- I5: a local skills-hub tap (HTTP JSON index) so the Skills → Hub tab can search/install.
const HUB_SKILL = (name) =>
  `---\nname: ${name}\ndescription: Use when you need to ${name} things carefully and step by step.\n---\n` +
  `# ${name}\n\nFollow these steps exactly: read the input, plan, act, verify the outcome.\n`;

function startTap() {
  return new Promise((ok) => {
    const srv = createServer((req, res) => {
      const base = `http://127.0.0.1:${srv.address().port}`;
      if (req.url === "/index.json") {
        const skills = ["e2e-hub-skill", "e2e-other-skill"].map((name) => ({
          name,
          description: `${name} from the e2e tap`,
          version: "1.0.0",
          url: `${base}/${name}/SKILL.md`,
        }));
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ skills }));
        return;
      }
      const m = /^\/([a-z0-9-]+)\/SKILL\.md$/.exec(req.url ?? "");
      if (m) res.writeHead(200, { "content-type": "text/markdown" }).end(HUB_SKILL(m[1]));
      else res.writeHead(404).end();
    });
    srv.listen(0, "127.0.0.1", () => ok(srv));
  });
}

// --- Settings → Models: a local OpenAI-compatible provider (`GET /v1/models`). The backend points
// provider model-list calls here via AUOREO_TEST_PROVIDER_BASE (honoured only with the fake gateway).
const E2E_GOOD_KEY = "sk-proj-e2e-good-key-0123456789abcdef";

function startProvider() {
  return new Promise((ok) => {
    const srv = createServer((req, res) => {
      if ((req.url ?? "").split("?")[0] !== "/v1/models") return void res.writeHead(404).end();
      if (req.headers.authorization !== `Bearer ${E2E_GOOD_KEY}`) {
        const error = { message: "Incorrect API key provided (e2e fake provider)", type: "invalid_request_error" };
        return void res.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ error }));
      }
      const data = ["e2e-model-alpha", "e2e-model-beta", "text-embedding-3-small"].map((id) => ({
        id,
        object: "model",
        owned_by: "e2e",
      }));
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ object: "list", data }));
    });
    srv.listen(0, "127.0.0.1", () => ok(srv));
  });
}

async function main() {
  if (process.env.BASE_URL) process.exit(await runPlaywright(process.env.BASE_URL));

  const tap = await startTap();
  const tapUrl = `http://127.0.0.1:${tap.address().port}/index.json`;
  const provider = await startProvider();
  const home = mkdtempSync(join(tmpdir(), "auoreo-e2e-"));
  const server = spawn("uv", ["run", "auoreo", "serve", "--port", "0"], {
    cwd: repoRoot,
    // fake stt/tts keep voice flows (meetings e2e) keyless and model-free
    env: {
      ...process.env,
      AUOREO_HOME: home,
      AUOREO_GATEWAY: "fake",
      // vad:fake = energy VAD, so the RTC call e2e endpoints the fake-mic tone into utterances
      AUOREO_BACKENDS: '{"stt":"fake","tts":"fake","vad":"fake"}',
      AUOREO_G6: JSON.stringify({ skills: { taps: [tapUrl] } }), // --- I5
      AUOREO_TEST_PROVIDER_BASE: `http://127.0.0.1:${provider.address().port}`, // --- Settings → Models
      AUOREO_M7: JSON.stringify({ ssh: { web: true } }), // --- M7: the Hosts page (agents still cannot use hosts)
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stop = () => {
    tap.close();
    provider.close();
    // Windows: `uv run` → auoreo.exe → python; killing the wrapper leaves the server running.
    if (process.platform === "win32") spawnSync("taskkill", ["/F", "/T", "/PID", String(server.pid)], { stdio: "ignore" });
    else server.kill("SIGTERM");
    try {
      rmSync(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch {
      /* Windows may still hold the DBs for a moment; the OS temp dir cleans it up later */
    }
  };
  process.on("SIGINT", () => {
    stop();
    process.exit(130);
  });

  const baseUrl = await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error("backend did not start within 60s")), 60_000);
    const onData = (buf) => {
      const text = buf.toString();
      process.stderr.write(`[server] ${text}`);
      const m = /server listening on (http:\/\/[^\s]+)/.exec(text);
      if (m) {
        clearTimeout(timer);
        ok(m[1]);
      }
    };
    server.stdout.on("data", onData);
    server.stderr.on("data", onData);
    server.on("exit", (code) => fail(new Error(`backend exited early (${code})`)));
  });
  server.stdout.removeAllListeners("data");
  server.stderr.removeAllListeners("data");
  server.stdout.resume();
  server.stderr.resume();

  const code = await runPlaywright(baseUrl);
  stop();
  process.exit(code);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
