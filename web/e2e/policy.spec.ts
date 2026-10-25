import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

// --- W1-C: model/effort bandit card on the Usage page (off by default)
test("usage page shows the model policy card", async ({ page, request }) => {
  const status = await request.get("/api/policy/bandit");
  expect(status.ok()).toBeTruthy();
  const body = (await status.json()) as { enabled: boolean; arms: { key: string }[] };

  await page.goto("/usage");
  const card = page.getByTestId("policy-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Model policy");
  await expect(page.getByTestId("policy-state")).toHaveText(body.enabled ? "learning" : "off");
  await expect(card).toContainText(body.arms[0]?.key ?? "default");
  await shot(page, "usage-policy");
});
