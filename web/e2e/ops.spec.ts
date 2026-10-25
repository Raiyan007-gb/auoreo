import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { chatAndWait, createSession, createUntitled, send, shot } from "./helpers";

test("fork creates a new session with the same messages", async ({ page, request }) => {
  const s = await createSession(request, "e2e fork source");
  await chatAndWait(request, s.id, "hello from the fork source");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("msg-user").first()).toContainText("hello from the fork source");

  await page.getByRole("button", { name: "Fork" }).click();
  await expect(page).not.toHaveURL(new RegExp(`/session/${s.id}$`));
  await expect(page).toHaveURL(/\/session\/ses_/);
  const forkId = page.url().split("/").pop() ?? "";
  expect(forkId).not.toBe(s.id);
  await expect(page.getByTestId("msg-user").first()).toContainText("hello from the fork source");
  await expect(page.getByTestId("msg-assistant").first()).toContainText("hello from the fork source");
  await expect(page.getByTestId("thread-notice").filter({ hasText: `Forked from ${s.id}` })).toBeVisible();
  await shot(page, "fork");

  const msgs = (await (await request.get(`/api/sessions/${forkId}/messages`)).json()) as { content: string | null }[];
  const orig = (await (await request.get(`/api/sessions/${s.id}/messages`)).json()) as { content: string | null }[];
  expect(msgs.map((m) => m.content)).toEqual(orig.map((m) => m.content));
});

test("archive hides a session from the list and the archived filter shows it", async ({ page, request }) => {
  const s = await createSession(request, "e2e archive me");
  await page.goto("/sessions");
  const row = page.locator(`[data-testid="session-row"][data-session-id="${s.id}"]`);
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Archive", exact: true }).click();
  await expect(row).toBeHidden();

  await page.getByTestId("archived-filter").click();
  await expect(page).toHaveURL(/\?archived=true$/);
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-archived", "true");
  await expect(row).toContainText("archived");
  await shot(page, "sessions-archived");

  await row.getByRole("button", { name: "Unarchive", exact: true }).click();
  await expect(row).toHaveAttribute("data-archived", "false");
  await page.getByTestId("archived-filter").click();
  await expect(row).toBeVisible();
});

test("delete from the session page removes it after confirming", async ({ page, request }) => {
  const s = await createSession(request, "e2e delete me");
  await page.goto(`/session/${s.id}`);
  await page.getByRole("button", { name: "Delete session" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete session?" });
  await expect(dialog).toContainText("e2e delete me");
  await shot(page, "delete-confirm");
  await dialog.getByRole("button", { name: "Delete" }).click();
  await expect(page).toHaveURL(/\/sessions$/);
  await expect(page.locator(`[data-testid="session-row"][data-session-id="${s.id}"]`)).toHaveCount(0);
  expect((await request.get(`/api/sessions/${s.id}`)).status()).toBe(404);
});

test("export as Markdown downloads the transcript", async ({ page, request }) => {
  const s = await createSession(request, "e2e export");
  await chatAndWait(request, s.id, "export marker 4242");
  await page.goto(`/session/${s.id}`);
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Export as Markdown" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe(`${s.id}.md`);
  const path = await download.path();
  expect(readFileSync(path, "utf8")).toContain("export marker 4242");
});

test("/compact on a tiny session shows the nothing-to-compact notice", async ({ page, request }) => {
  const s = await createSession(request, "e2e compact");
  await chatAndWait(request, s.id, "tiny");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("msg-user").first()).toContainText("tiny");
  await send(page, "/compact");
  await expect(page.getByTestId("action-notice")).toContainText(/nothing to compact/i);
  await expect(page.getByTestId("action-notice")).toHaveAttribute("data-level", "warn");
  // compaction.started / compaction.failed land in the thread as system notices.
  await expect(page.locator('[data-testid="thread-notice"][data-code="compaction.failed"]')).toContainText(
    /nothing to compact/i,
  );
  await expect(page.getByTestId("composer")).toHaveValue("");
  await shot(page, "compact-notice");
});

test("an untitled session picks up session.title_updated live", async ({ page, request }) => {
  const s = await createUntitled(request);
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("session-title")).toHaveText("Untitled session");
  await send(page, "Plan the quarterly offsite agenda");
  await expect(page.getByTestId("session-title")).toHaveText("Plan the quarterly offsite agenda");
});

test("usage page shows non-zero tokens after a turn and the session badge tooltip reads the ledger", async ({
  page,
  request,
}) => {
  const s = await createSession(request, "e2e usage");
  await chatAndWait(request, s.id, "count my tokens please");
  await page.goto("/usage");
  await expect(page.getByTestId("usage-total-tokens")).not.toHaveText("0");
  await expect(page.getByTestId("usage-total-calls")).not.toHaveText("0");
  await expect(page.getByRole("table", { name: "Usage by model" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Usage by session" })).toContainText("e2e usage");
  await shot(page, "usage");

  await page.goto(`/session/${s.id}`);
  await page.getByTestId("token-badge").hover();
  await expect(page.getByTestId("usage-tooltip").first()).toContainText(/Usage ledger: \d+ call/);
});

test("command palette lists compact and inserts /compact into the composer", async ({ page, request }) => {
  const s = await createSession(request, "e2e palette");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "open");
  await page.keyboard.press("Control+k");
  const item = page.locator('[data-testid="palette-command"][data-command="compact"]');
  await expect(item).toBeVisible();
  await shot(page, "palette-commands");
  await item.click();
  await expect(page.getByTestId("composer")).toHaveValue("/compact ");
});

test("assistant markdown renders code blocks with a copy button", async ({ page, request }) => {
  const s = await createSession(request, "e2e markdown");
  await page.goto(`/session/${s.id}`);
  await send(page, "Here is **bold** and code:\n\n```py\nprint('hi')\n```");
  const answer = page.getByTestId("msg-assistant").last();
  await expect(answer.locator("strong")).toHaveText("bold");
  await expect(answer.getByTestId("code-block")).toContainText("print('hi')");
  await expect(answer.getByRole("button", { name: "Copy code" })).toBeVisible();
  await shot(page, "markdown");
});
