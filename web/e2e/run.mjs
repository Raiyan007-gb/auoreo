#!/usr/bin/env node
// `npm run e2e`: with BASE_URL set, run Playwright against it. Without it, boot a keyless
// backend (`AUOREO_GATEWAY=fake uv run auoreo serve --port 0`, fresh AUOREO_HOME) from the
// repo root, wait for "server listening on …", run the suite, then stop the server.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = resolve(here, "..");
const repoRoot = resolve(webDir, "..");
const extra = process.argv.slice(2);

function runPlaywright(baseUrl) {
  return new Promise((done) => {
    const p = spawn("npx", ["playwright", "test", "-c", join(here, "playwright.config.ts"), ...extra], {
      cwd: webDir,
      stdio: "inherit",
      env: { ...process.env, BASE_URL: baseUrl },
    });
    p.on("exit", (code) => done(code ?? 1));
  });
}

async function main() {
  if (process.env.BASE_URL) process.exit(await runPlaywright(process.env.BASE_URL));

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
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const stop = () => {
    server.kill("SIGTERM");
    rmSync(home, { recursive: true, force: true });
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
