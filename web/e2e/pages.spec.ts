import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

test("harness lists at least 20 tools and reload shows a diff", async ({ page }) => {
  await page.goto("/harness");
  await page.getByRole("tab", { name: /tools/ }).click();
  const rows = page.locator('[data-testid="harness-row"][data-kind="tool"]');
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThanOrEqual(20);
  await expect(rows.filter({ hasText: "bash" }).first()).toContainText("bundled");
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByTestId("reload-result")).toContainText("generation");
  await shot(page, "harness");
});

test("memory add + search round trip", async ({ page }) => {
  const marker = `e2e-fact-${Date.now()}`;
  await page.goto("/memory");
  await page.getByLabel("Fact", { exact: true }).fill(`The deploy codename is ${marker}`);
  await page.getByRole("button", { name: "Add fact" }).click();
  await expect(page.getByText(/Saved as fact:\d+/)).toBeVisible();
  await page.getByPlaceholder("Search memory…").fill(marker);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const hit = page.getByTestId("memory-hit").filter({ hasText: marker });
  await expect(hit).toBeVisible();
  await expect(hit).toContainText(/fact:\d+|fact/);
  await shot(page, "memory");
});

test("usage, settings, audit and doctor render", async ({ page }) => {
  await page.goto("/usage");
  await expect(page.getByRole("heading", { name: "Usage" })).toBeVisible();
  await expect(page.getByText("Total tokens")).toBeVisible();
  await expect(page.getByTestId("usage-cache-hit")).toHaveText(/^\d+%$/); // cached / prompt, main turns
  await expect(page.getByTestId("chart-cached")).toBeVisible();
  await shot(page, "usage-page");

  await page.goto("/settings#general");
  await expect(page.getByTestId("health-json")).toContainText('"status": "ok"');
  await shot(page, "settings");
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.goto("/sessions");
  await shot(page, "sessions-dark");
  await page.goto("/settings#general");
  await page.getByRole("radio", { name: "Light" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await page.goto("/audit");
  await expect(page.getByText("auoreo doctor")).toBeVisible();
  await shot(page, "audit");
  await page.goto("/doctor");
  await expect(page.getByRole("heading", { name: "Doctor" })).toBeVisible();
  await shot(page, "doctor");
});

test("command palette opens with Ctrl+K and navigates", async ({ page }) => {
  await page.goto("/sessions");
  await page.keyboard.press("Control+k");
  const input = page.getByPlaceholder("Jump to a page, session, agent or command…");
  await expect(input).toBeVisible();
  await input.fill("harness");
  await shot(page, "palette");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/harness$/);
});

test("command palette caps each registry group at three matches while searching", async ({ page, request }) => {
  const skills = (await (await request.get("/api/harness/skills")).json()) as unknown[];
  test.skip(skills.length < 4, "needs at least four skills to observe the cap");
  await page.goto("/sessions");
  await page.keyboard.press("Control+k");
  const input = page.getByPlaceholder("Jump to a page, session, agent or command…");
  await expect(input).toBeVisible();
  await input.fill("e"); // matches every skill through its description
  await expect(page.getByTestId("palette-skill").first()).toBeVisible();
  expect(await page.getByTestId("palette-skill").count()).toBeLessThanOrEqual(3);
});
