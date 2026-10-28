import { expect, test } from "@playwright/test";
import { createSession, shot } from "./helpers";

test("audit page shows an intact chain, filters, and opens row detail", async ({ page, request }) => {
  const s = await createSession(request, "audit e2e");
  expect((await request.post("/api/welcome/event", { data: { step: "doctor", action: "completed" } })).status()).toBe(204);
  await page.goto("/audit");
  await expect(page.getByTestId("chain-status")).toHaveAttribute("data-ok", "true");
  await expect(page.getByTestId("chain-status")).toContainText("chain ok");
  const rows = page.getByTestId("audit-row");
  await expect(rows.first()).toBeVisible();
  await page.getByLabel("Action").fill("welcome.completed");
  await page.getByRole("button", { name: "Filter", exact: true }).click();
  await expect(rows.first()).toContainText("welcome.completed");
  await shot(page, "audit-chain");
  await rows.first().click();
  const detail = page.getByTestId("audit-detail");
  await expect(detail).toContainText('"step": "doctor"');
  await expect(detail).toContainText("row_hash");
  await shot(page, "audit-detail");
  await page.keyboard.press("Escape");
  expect(s.id).toBeTruthy();
});

test("doctor lists grouped checks and re-runs", async ({ page }) => {
  await page.goto("/doctor");
  const checks = page.getByTestId("doctor-check");
  await expect(checks.first()).toBeVisible();
  expect(await checks.count()).toBeGreaterThanOrEqual(8);
  await expect(page.getByTestId("doctor-summary")).toContainText("ok");
  await expect(checks.filter({ hasText: "credentials" }).first()).toContainText("fake gateway");
  await expect(page.getByRole("heading", { name: "Server" })).toBeVisible();
  await page.getByRole("button", { name: "Run again" }).click();
  await expect(checks.first()).toBeVisible();
  await shot(page, "doctor-checks");
});

test("memory facts tab lists, pins and archives a fact; files and curator tabs render", async ({ page, request }) => {
  const marker = `e2e-ops-fact-${Date.now()}`;
  const res = await request.post("/api/memory/facts", { data: { content: `The release train is ${marker}` } });
  const { id } = (await res.json()) as { id: number };
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Facts" }).click();
  const row = page.locator(`[data-testid="fact-row"][data-fact-id="${id}"]`);
  await expect(row).toContainText(marker);
  await row.getByRole("button", { name: `Pin fact ${id}` }).click();
  await expect(row).toContainText("pinned");
  await row.getByRole("button", { name: `Mark fact ${id} helpful` }).click();
  await expect(page.getByTestId("fact-status")).toContainText("helpful");
  await shot(page, "memory-facts");
  await row.getByRole("button", { name: `Forget fact ${id}` }).click();
  await expect(row).toHaveCount(0);
  await page.getByLabel("Show archived").check();
  await expect(row).toHaveAttribute("data-archived", "true");
  await expect(row).toContainText("archived");

  await page.getByRole("tab", { name: "Files" }).click();
  await expect(page.getByRole("tab", { name: "MEMORY.md" })).toBeVisible();
  await expect(page.getByTestId("memory-file").first()).toBeVisible();
  await shot(page, "memory-files");

  await page.getByRole("tab", { name: "Curator" }).click();
  await page.getByRole("button", { name: "Dry run" }).click();
  await expect(page.getByTestId("curator-summary")).toContainText("dry run");
  await shot(page, "memory-curator");
});

test("welcome flow is resumable and skippable; banner shows on missing credentials", async ({ page }) => {
  await page.goto("/sessions");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/welcome");
  const step = page.getByTestId("welcome-step");
  await expect(step).toHaveAttribute("data-step", "doctor");
  await expect(page.getByTestId("welcome-doctor")).toContainText("ok");
  await page.getByRole("button", { name: "Next" }).click();
  await expect(step).toHaveAttribute("data-step", "credentials");
  await expect(page.getByTestId("welcome-credentials")).toContainText("Paste a key");
  await expect(page.getByTestId("provider-key")).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Skip step" }).click();
  await expect(step).toHaveAttribute("data-step", "model");
  await shot(page, "welcome-model");
  await page.reload();
  await expect(step).toHaveAttribute("data-step", "model"); // resumed from localStorage
  await page.getByRole("button", { name: "Skip setup" }).click();
  await expect(page).toHaveURL(/\/sessions$/);

  // Banner: forced via a doctor response with a missing key (the fake gateway needs none).
  await page.evaluate(() => localStorage.clear());
  await page.route("**/api/doctor", (route) =>
    route.fulfill({
      json: {
        checks: [{ check: "credentials", level: "warn", detail: "openrouter/x: missing OPENROUTER_API_KEY", remedy: "set it" }],
        summary: { ok: 0, warn: 1, error: 0 },
      },
    }),
  );
  await page.goto("/sessions");
  const banner = page.getByTestId("welcome-banner");
  await expect(banner).toBeVisible();
  await shot(page, "welcome-banner");
  await banner.getByRole("button", { name: "Dismiss setup banner" }).click();
  await expect(banner).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  await expect(banner).toHaveCount(0);
});

test("voice button reflects /api/voice/status", async ({ page, request }) => {
  const s = await createSession(request, "voice e2e");
  const status = (await (await request.get("/api/voice/status")).json()) as {
    enabled: boolean;
    backends: { kind: string; ok: boolean }[];
  };
  const sttReady = status.enabled && status.backends.some((b) => b.kind === "stt" && b.ok);
  const ttsReady = status.enabled && status.backends.some((b) => b.kind === "tts" && b.ok);
  await page.goto(`/session/${s.id}`);
  const mic = page.getByTestId("voice-button");
  await expect(mic).toBeVisible();
  const hasRecorder = await page.evaluate(() => typeof MediaRecorder !== "undefined");
  await expect(mic).toHaveAttribute("data-ready", String(sttReady && hasRecorder));
  if (sttReady && hasRecorder) await expect(mic).toBeEnabled();
  else await expect(mic).toBeDisabled();
  const speaker = page.getByTestId("voice-speaker");
  if (ttsReady) await expect(speaker).toBeEnabled();
  else await expect(speaker).toBeDisabled();
  await shot(page, "voice-button");
});
