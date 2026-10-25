import { expect, test } from "@playwright/test";
import { createSession, send, shot } from "./helpers";

const BASH = '/tool bash {"command": "echo hi"}';

test("approval footer appears for a bash call and approving completes the run", async ({ page, request }) => {
  const s = await createSession(request, "e2e approve bash");
  await page.goto(`/session/${s.id}`);
  await send(page, BASH);

  const footer = page.getByTestId("approval-card");
  await expect(footer).toBeVisible();
  await expect(footer).toContainText("bash");
  await expect(page.locator('[data-testid="tool-card"][data-tool="bash"]')).toContainText("awaiting approval");
  await shot(page, "approval-footer");

  await footer.getByRole("button", { name: "Approve once" }).click();
  await expect(footer).toBeHidden();
  const card = page.locator('[data-testid="tool-card"][data-tool="bash"]');
  await expect(card).toHaveAttribute("data-status", "completed");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("hi");
  await expect(page.getByTestId("session-status")).toHaveText("idle");
  await shot(page, "approval-completed");
});

test("approvals page shows the cross-session queue and rejects inline", async ({ page, request }) => {
  const s = await createSession(request, "e2e approvals queue");
  const res = await request.post("/api/chat", { data: { session_id: s.id, message: BASH } });
  expect(res.status()).toBe(202);

  await page.goto("/approvals");
  const group = page.getByTestId("approval-group").filter({ hasText: "e2e approvals queue" });
  await expect(group).toBeVisible();
  await expect(group.getByTestId("approval-card")).toContainText("bash");
  await shot(page, "approvals");

  await group.getByRole("button", { name: "Reject" }).click();
  await page.getByLabel("Reason for rejecting (optional)").fill("not now");
  await group.getByRole("button", { name: "Reject" }).click();
  await expect(group).toBeHidden();

  const pending = (await (await request.get(`/api/permissions/pending?session_id=${s.id}`)).json()) as unknown[];
  expect(pending).toHaveLength(0);
});
