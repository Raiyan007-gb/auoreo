import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

/** Use the pre-installed Chromium (never `playwright install` here). */
function chromiumPath(): string | undefined {
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers"].filter(Boolean) as string[];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const dir of readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
      const bin = join(root, dir, "chrome-linux64", "chrome");
      if (existsSync(bin)) return bin;
    }
  }
  return undefined;
}

const executablePath = chromiumPath();

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
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
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, launchOptions: { executablePath } },
    },
  ],
});
