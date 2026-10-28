import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Locator, type Page, test } from "@playwright/test";

/**
 * Screenshot comparison, light and dark, desktop and phone width. Baselines live next to this file
 * (`visual.spec.ts-snapshots/`) with the platform in the name, because text rasterises differently
 * on Windows, macOS and Linux. Without baselines for this platform the spec skips (nothing to compare);
 * `npm run e2e:visual -- --update-snapshots` writes them (CI: the "visual baselines" workflow).
 *
 * Only surfaces that look the same on every fresh server are compared: empty states, forms and
 * dialogs, not lists generated from the repo's agents or skills (those would trip on every new
 * definition). Ports and build hashes are masked; Doctor, Audit and Memory (machine- and
 * time-specific) are covered by the accessibility and keyboard specs instead.
 */
const SNAPS = join(dirname(fileURLToPath(import.meta.url)), "visual.spec.ts-snapshots");
const hasBaselines = existsSync(SNAPS) && readdirSync(SNAPS).some((f) => f.endsWith(`-${process.platform}.png`));

/** The sidebar footer shows this server's host:port, which changes every run. */
const volatile = (page: Page): Locator[] => [
  page.locator('aside[aria-label="Primary"] span.truncate.font-mono'),
  page.getByTestId("ws-state"),
];

interface Scene {
  name: string;
  open: (page: Page) => Promise<void>;
  mask?: (page: Page) => Locator[];
}
const go = (path: string, tab?: string): ((page: Page) => Promise<void>) => async (page) => {
  await page.goto(path);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", /open|unavailable/);
  await page.locator("main").waitFor();
  if (tab) await page.getByRole("tab", { name: tab }).click();
  await page.waitForTimeout(900); // skeletons and entrance animation settle
};

const DESKTOP: Scene[] = [
  { name: "sessions-empty", open: go("/sessions") },
  { name: "inbox-empty", open: go("/inbox") },
  { name: "goals-empty", open: go("/goals") },
  { name: "meetings-empty", open: go("/meetings") },
  { name: "teams", open: go("/teams") },
  { name: "usage-empty", open: go("/usage") },
  { name: "settings-models", open: go("/settings") },
  { name: "settings-general", open: go("/settings", "General"), mask: (p) => [p.locator("pre")] }, // health JSON carries the build hash
  {
    name: "new-session-dialog",
    open: async (page) => {
      await go("/sessions")(page);
      await page.getByRole("button", { name: "New session" }).first().click();
      await page.getByRole("dialog").waitFor();
      await page.waitForTimeout(300);
    },
  },
];
const PHONE: Scene[] = [
  { name: "sessions-empty", open: go("/sessions") },
  { name: "inbox-empty", open: go("/inbox") },
  {
    name: "drawer",
    open: async (page) => {
      await go("/sessions")(page);
      await page.getByRole("button", { name: "Open navigation" }).click();
      await page.waitForTimeout(500);
    },
  },
];

for (const theme of ["light", "dark"] as const) {
  for (const [size, scenes, viewport] of [
    ["desktop", DESKTOP, { width: 1280, height: 800 }],
    ["phone", PHONE, { width: 390, height: 800 }],
  ] as const) {
    for (const scene of scenes) {
      test(`visual ${size} ${theme} ${scene.name}`, async ({ page }, info) => {
        const updating = info.config.updateSnapshots === "all" || info.config.updateSnapshots === "changed"; // `-u` alone means "changed"
        test.skip(!hasBaselines && !updating, `no ${process.platform} baselines yet (npm run e2e:visual -- --update-snapshots)`);
        await page.setViewportSize(viewport);
        await page.addInitScript((t) => localStorage.setItem("auoreo_theme", t), theme);
        await scene.open(page);
        await expect(page).toHaveScreenshot(`${size}-${scene.name}-${theme}.png`, {
          mask: [...volatile(page), ...(scene.mask?.(page) ?? [])],
          maxDiffPixelRatio: 0.012,
          animations: "disabled",
          caret: "hide",
        });
      });
    }
  }
}
