import { expect, test, type Page } from "@playwright/test";
import { shot } from "./helpers";

async function enablePwa(page: Page): Promise<void> {
  await page.goto("/settings");
  const toggle = page.getByTestId("pwa-toggle");
  await expect(toggle).toBeEnabled();
  await toggle.check();
  await expect(page.getByTestId("pwa-status")).toHaveText("Service worker registered.");
  // Installed + activated + controlling this page (skipWaiting + clients.claim).
  await page.waitForFunction(async () => {
    const reg = await navigator.serviceWorker.getRegistration("/");
    const precached = Boolean(await caches.match("/index.html", { ignoreVary: true }));
    return Boolean(reg?.active && navigator.serviceWorker.controller && precached);
  });
}

test("manifest and service worker are served with PWA headers", async ({ request }) => {
  const manifest = await request.get("/manifest.webmanifest");
  expect(manifest.status()).toBe(200);
  expect(manifest.headers()["content-type"]).toContain("application/manifest+json");
  const body = (await manifest.json()) as { name: string; start_url: string; display: string; icons: unknown[] };
  expect(body).toMatchObject({ name: "AUOREO", start_url: "/sessions", display: "standalone" });
  expect(body.icons.length).toBeGreaterThanOrEqual(4);

  const sw = await request.get("/sw.js");
  expect(sw.status()).toBe(200);
  expect(sw.headers()["content-type"]).toContain("javascript");
  expect(sw.headers()["service-worker-allowed"]).toBe("/");
  expect(await sw.text()).not.toContain("__AUOREO_BUILD__"); // stamped at build time

  const list = (await (await request.get("/sw-assets.json")).json()) as { assets: string[] };
  expect(list.assets).toContain("/index.html");
  expect(list.assets.some((a) => a.startsWith("/assets/"))).toBe(true);
  expect(list.assets.filter((a) => a.startsWith("/api") || a === "/sw.js")).toEqual([]);
});

test("PWA is opt-in: no worker until the Settings toggle, removed again on disable", async ({ page }) => {
  await page.goto("/sessions");
  expect(await page.evaluate(() => navigator.serviceWorker.getRegistration("/").then(Boolean))).toBe(false);

  await enablePwa(page);
  expect(await page.evaluate(() => localStorage.getItem("auoreo_pwa"))).toBe("1");
  await shot(page, "pwa-settings");

  await page.getByTestId("pwa-toggle").uncheck();
  await expect(page.getByTestId("pwa-status")).toHaveText("Service worker removed.");
  expect(await page.evaluate(() => navigator.serviceWorker.getRegistration("/").then(Boolean))).toBe(false);
  expect(await page.evaluate(() => caches.keys())).toEqual([]);
});

test("API bypasses the worker; offline, /sessions still renders the cached shell", async ({ page, context }) => {
  await enablePwa(page);

  // Online: the API response comes from the network, the hashed bundle from the SW cache.
  const [health] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/api/health")),
    page.evaluate(() => fetch("/api/health", { cache: "no-store" }).then((r) => r.status)),
  ]);
  expect(health.fromServiceWorker()).toBe(false);
  const cachedPaths = () =>
    page.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        urls.push(...(await cache.keys()).map((r) => new URL(r.url).pathname));
      }
      return urls;
    });
  await expect.poll(cachedPaths).toContain("/index.html");
  const cached = await cachedPaths();
  expect(cached.filter((u) => /^\/(api|ws|hooks|v1|mcp|acp)\b/.test(u))).toEqual([]);

  // Offline: the API call fails (nothing replayed from cache) …
  await context.setOffline(true);
  const offlineApi = await page.evaluate(() =>
    fetch("/api/health", { cache: "no-store" }).then(
      (r) => `status ${r.status}`,
      () => "failed",
    ),
  );
  expect(offlineApi).toBe("failed");

  // … while a navigation to /sessions is answered by the worker with the cached shell.
  const nav = await page.goto("/sessions");
  expect(nav?.fromServiceWorker()).toBe(true);
  await expect(page.getByRole("complementary", { name: "Primary" })).toBeVisible();
  await expect(page.getByTestId("offline-banner")).toContainText("offline");
  await shot(page, "pwa-offline");

  await context.setOffline(false);
  await expect(page.getByTestId("offline-banner")).toBeHidden();
});
