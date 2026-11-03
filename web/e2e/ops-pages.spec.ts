import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

test("skills page lists bundled skills and shows the frontmatter dialog", async ({ page }) => {
  await page.goto("/skills");
  await expect(page.getByRole("heading", { name: "Skills" })).toBeVisible();
  const row = page.locator('[data-testid="skill-row"][data-name="code-review"]');
  await expect(row).toContainText("bundled");
  await row.getByRole("button", { name: /code-review/ }).click();
  const dialog = page.getByTestId("skill-dialog");
  await expect(dialog).toContainText("name: code-review");
  await expect(dialog).toContainText("SKILL.md");
  await shot(page, "skills-dialog");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Reload" }).click();
  await expect(page.getByTestId("skills-reloaded")).toContainText("generation");
  await shot(page, "skills");
});

test("plugins page lists telegram/discord, MCP servers, and logs a hot reload", async ({ page }) => {
  await page.goto("/plugins");
  await expect(page.locator('[data-testid="plugin-row"][data-name="telegram"]')).toBeVisible();
  await expect(page.locator('[data-testid="plugin-row"][data-name="discord"]')).toBeVisible();
  await expect(page.locator('[data-testid="plugin-row"][data-name="telegram"]')).toContainText("connector");
  await expect(page.getByRole("button", { name: "Pair a chat with telegram" })).toBeVisible();
  await page.getByRole("button", { name: "Hot reload" }).click();
  await expect(
    page.locator('[data-testid="reload-log-entry"][data-type="harness.reloaded"]').first(),
  ).toContainText("generation");
  await shot(page, "plugins");
  await page.getByRole("tab", { name: "MCP servers" }).click();
  await expect(page.locator('[data-testid="mcp-row"][data-name="fetch"]')).toContainText("stdio");
  await shot(page, "plugins-mcp");
});

test("a bundled MCP server that is off can be enabled from the MCP tab", async ({ page }) => {
  await page.goto("/plugins");
  await page.getByRole("tab", { name: "MCP servers" }).click();
  const row = page.locator('[data-testid="mcp-row"][data-name="time"]');
  await expect(row).toContainText("Off");
  await row.getByRole("button", { name: "Enable time" }).click();
  // The runtime copy replaces the bundled one and the row leaves the Off state (connecting
  // to the server itself is bounded by the bridge and may still be "not connected").
  await expect(row).not.toContainText("Off", { timeout: 30_000 });
  await expect(row).toContainText("runtime");
});

test("jobs: morning_brief runs now with the fake gateway; runs drawer and delivery replay", async ({ page }) => {
  await page.goto("/jobs");
  const row = page.locator('[data-testid="cron-row"][data-name="morning_brief"]');
  await expect(row).toContainText("0 9 * * 1-5");
  await row.getByRole("button", { name: "Run now: morning_brief" }).click();
  await expect(page.getByTestId("cron-notice")).toContainText("morning_brief");
  await expect(row.getByTestId("cron-last")).toContainText("ok", { timeout: 20_000 });

  await row.getByRole("button", { name: "Pause" }).click();
  await expect(row).toHaveAttribute("data-paused", "true");
  await row.getByRole("button", { name: "Resume" }).click();
  await expect(row).toHaveAttribute("data-paused", "false");
  await shot(page, "jobs-cron");

  await row.getByRole("button", { name: "Runs of morning_brief" }).click();
  const drawer = page.getByTestId("runs-drawer");
  await expect(drawer.locator('[data-testid="run-row"][data-status="ok"]').first()).toBeVisible();
  await expect(drawer.getByRole("link", { name: /^ses_/ }).first()).toBeVisible();
  await shot(page, "jobs-runs");
  await page.keyboard.press("Escape");

  await page.getByRole("tab", { name: "Deliveries" }).click();
  await expect(page).toHaveURL(/tab=deliveries/);
  const delivery = page.getByTestId("delivery-row").filter({ hasText: "morning_brief" }).first();
  await expect(delivery).toHaveAttribute("data-status", "delivered", { timeout: 20_000 });
  await delivery.getByRole("button", { name: /Replay delivery/ }).click();
  const dialog = page.getByTestId("replay-dialog");
  await expect(dialog.getByTestId("replay-prompt")).toContainText("Summarize what needs attention");
  await shot(page, "jobs-replay-dry-run");
  await dialog.getByRole("button", { name: "Queue replay" }).click();
  await expect(dialog.getByTestId("replay-queued")).toContainText("Queued as delivery");
});

test("jobs: webhooks tab lists the github route with its deliveries drawer", async ({ page }) => {
  await page.goto("/jobs?tab=webhooks");
  const row = page.locator('[data-testid="webhook-row"][data-name="github-pr"]');
  await expect(row).toContainText("/hooks/github-pr");
  await expect(row).toContainText("github");
  await expect(row).toContainText("pull_request");
  await expect(row).toContainText("missing");
  await shot(page, "jobs-webhooks");
  await row.getByRole("button", { name: "Deliveries of github-pr" }).click();
  await expect(page.getByTestId("deliveries-drawer")).toContainText("No deliveries yet");
});

test("teams page renders and kanban create-task lands in ready", async ({ page }) => {
  await page.goto("/teams");
  await expect(page.getByRole("heading", { name: "Teams", level: 1 })).toBeVisible();
  await expect(page.getByText("No teams yet").or(page.getByTestId("team-item").first())).toBeVisible();
  await shot(page, "teams");

  await page.getByRole("tab", { name: "Kanban" }).click();
  const title = `e2e task ${Date.now()}`;
  await page.getByRole("button", { name: "New task" }).click();
  await page.getByLabel("Title").fill(title);
  await page.getByLabel("Details").fill("created by the ops-pages e2e");
  await page.getByRole("button", { name: "Create task" }).click();
  const ready = page.locator('[data-testid="kanban-column"][data-status="ready"]');
  await expect(ready.getByTestId("kanban-card").filter({ hasText: title })).toBeVisible();
  await page.reload();
  await expect(ready.getByTestId("kanban-card").filter({ hasText: title })).toBeVisible();
  await shot(page, "teams-kanban");
});
