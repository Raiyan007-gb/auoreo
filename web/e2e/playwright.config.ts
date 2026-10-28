import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium, defineConfig, devices } from "@playwright/test";

/** Use the pre-installed Chromium (never `playwright install` here): `PLAYWRIGHT_CHROMIUM_PATH`,
 *  a Linux build under the browsers path, Playwright's own install, or else an installed Chrome. */
function chromiumPath(): string | undefined {
  const pinned = process.env.PLAYWRIGHT_CHROMIUM_PATH;
  if (pinned && existsSync(pinned)) return pinned;
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers"].filter(Boolean) as string[];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const dir of readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
      const bin = join(root, dir, "chrome-linux64", "chrome");
      if (existsSync(bin)) return bin;
    }
  }
  // Playwright's own Chromium (`npx playwright install chromium`, as CI does) beats a system Chrome,
  // so every platform runs the same browser build where it can.
  if (existsSync(chromium.executablePath())) return undefined;
  const installed = [
    join(process.env.PROGRAMFILES ?? "C:\\Program Files", "Google", "Chrome", "Application", "chrome.exe"),
    join(process.env.LOCALAPPDATA ?? "", "Google", "Chrome", "Application", "chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ];
  return installed.find((p) => existsSync(p));
}

const executablePath = chromiumPath();

/**
 * Fake mic input for the RTC call spec (G5b): 1.2 s of a 440 Hz tone, then 1.5 s of silence,
 * looped by Chromium. Loud enough for the energy VAD, with gaps so utterances endpoint.
 */
function fakeMicWav(): string {
  const rate = 48_000;
  const tone = Math.round(rate * 1.2);
  const n = tone + Math.round(rate * 1.5);
  const buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + n * 2, 4);
  buf.write("WAVEfmt ", 8);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < tone; i++) buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 440 * i) / rate) * 0.3 * 32767), 44 + i * 2);
  const dir = join(tmpdir(), "auoreo-e2e-media");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "fake-mic.wav");
  writeFileSync(path, buf);
  return path;
}

/** Chromium fake capture devices: no permission prompt, camera = test pattern, mic = the WAV. */
const MEDIA_ARGS = [
  "--use-fake-device-for-media-stream",
  "--use-fake-ui-for-media-stream",
  `--use-file-for-fake-audio-capture=${fakeMicWav()}`,
  "--auto-select-desktop-capture-source=Entire screen",
  "--autoplay-policy=no-user-gesture-required",
];

export default defineConfig({
  testDir: ".",
  // Screenshot comparison runs on its own (`npm run e2e:visual`), against a fresh server.
  testMatch: process.env.VISUAL ? "visual.spec.ts" : "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  outputDir: "../test-results",
  use: {
    baseURL: process.env.BASE_URL ?? "http://127.0.0.1:4096",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [
    {
      name: "chromium",
      // (a project's own testIgnore replaces the top-level one, so visual.spec.ts is listed here)
      testIgnore: process.env.VISUAL ? "rtc.spec.ts" : ["rtc.spec.ts", "visual.spec.ts"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, launchOptions: { executablePath } },
    },
    {
      // --- G5b: WebRTC call client against the aiortc server (needs the `rtc` extra)
      name: "chromium-media",
      testMatch: process.env.VISUAL ? "none.spec.ts" : "rtc.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        permissions: ["microphone", "camera"],
        launchOptions: { executablePath, args: MEDIA_ARGS },
      },
    },
  ],
});
