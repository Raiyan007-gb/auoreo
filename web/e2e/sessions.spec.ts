import { expect, test } from "@playwright/test";
import { createSession, send, shot } from "./helpers";

test("sessions page lists a created session", async ({ page, request }) => {
  const s = await createSession(request, "e2e listed session");
  await page.goto("/sessions");
  const row = page.locator(`[data-testid="session-row"][data-session-id="${s.id}"]`);
  await expect(row).toBeVisible();
  await expect(row).toContainText("e2e listed session");
  await expect(row.getByRole("button", { name: "Continue" })).toBeEnabled();
  await shot(page, "sessions");

  await row.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(new RegExp(`/session/${s.id}$`));
});

test("new session dialog creates a session from the agent picker", async ({ page }) => {
  await page.goto("/sessions");
  await page.getByRole("button", { name: "New session" }).click();
  await expect(page.getByLabel("Agent")).toBeVisible();
  await expect(page.getByLabel("Agent").locator("option")).not.toHaveCount(1);
  await page.getByLabel("Title (optional)").fill("e2e from dialog");
  await shot(page, "new-session-dialog");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/session\/ses_/);
  await expect(page.getByTestId("session-title")).toHaveText("e2e from dialog");
});

test("session thread shows the tool card and the echoed answer", async ({ page, request }) => {
  const s = await createSession(request, "e2e tool read");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByText("No messages yet")).toBeVisible();
  await send(page, '/tool read {"path": "README.md"}');

  const card = page.locator('[data-testid="tool-card"][data-tool="read"]');
  await expect(card).toHaveAttribute("data-status", "completed");
  await expect(card).toContainText("README.md");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("Tool result:");
  await expect(page.getByTestId("msg-assistant").last()).toHaveAttribute("data-streaming", "false");
  await expect(page.getByTestId("token-badge")).not.toHaveText(/^0 tok/);

  await card.getByRole("button").first().click();
  await expect(card.getByText("Arguments")).toBeVisible();
  await shot(page, "session");

  // Reload: the thread is rebuilt from the durable event log.
  await page.reload();
  await expect(page.locator('[data-testid="tool-card"][data-tool="read"]')).toHaveAttribute("data-status", "completed");
});
