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
  await expect(page.getByTestId("session-status")).toHaveText("Idle");
  await shot(page, "approval-completed");
});

test("approvals page shows the cross-session queue and rejects inline", async ({ page, request }) => {
  const s = await createSession(request, "e2e approvals queue");
  const res = await request.post("/api/chat", { data: { session_id: s.id, message: BASH } });
  expect(res.status()).toBe(202);

  const pendingFetches: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/permissions/pending")) pendingFetches.push(r.url());
  });
  await page.goto("/approvals");
  const group = page.getByTestId("approval-group").filter({ hasText: "e2e approvals queue" });
  await expect(group).toBeVisible();
  await expect(group.getByTestId("approval-card")).toContainText("bash");
  await shot(page, "approvals");
  // One approvals store: the shell badge and the inbox page share a single seed request.
  expect(pendingFetches).toHaveLength(1);

  await group.getByRole("button", { name: "Reject" }).click();
  await page.getByLabel("Reason for rejecting (optional)").fill("not now");
  await group.getByRole("button", { name: "Reject" }).click();
  await expect(group).toBeHidden();

  const pending = (await (await request.get(`/api/permissions/pending?session_id=${s.id}`)).json()) as unknown[];
  expect(pending).toHaveLength(0);
});

test("approvals page receives new requests live over the WebSocket (no poll, no replay)", async ({ page, request }) => {
  const old = await createSession(request, "e2e approvals old");
  await request.post("/api/chat", { data: { session_id: old.id, message: BASH } });
  await page.goto("/approvals");
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "open");
  await expect(page.getByTestId("approvals-live")).toHaveText("Live over WebSocket.");
  // The pre-existing request comes from the REST seed, not from replaying history.
  await expect(page.getByTestId("approval-group").filter({ hasText: "e2e approvals old" })).toBeVisible();

  const s = await createSession(request, "e2e approvals live");
  await request.post("/api/chat", { data: { session_id: s.id, message: BASH } });
  const group = page.getByTestId("approval-group").filter({ hasText: s.id });
  await expect(group).toBeVisible({ timeout: 2500 });
  await group.getByRole("button", { name: "Approve once" }).click();
  await expect(group).toBeHidden();
  const oldGroup = page.getByTestId("approval-group").filter({ hasText: "e2e approvals old" });
  await oldGroup.getByRole("button", { name: "Approve once" }).click();
  await expect(oldGroup).toBeHidden();
});
