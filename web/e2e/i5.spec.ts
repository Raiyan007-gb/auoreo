import { expect, type Page, test } from "@playwright/test";
import { chatAndWait, createSession, send, shot } from "./helpers";

/** Append canned events to one session's replay (`GET /api/events?follow=false&session_id=…`). */
async function appendReplay(page: Page, sid: string, events: { type: string; data: Record<string, unknown> }[]) {
  const base = 900_000_000;
  const extra = events
    .map((e, i) => {
      const env = { id: String(base + i), seq: base + i, type: e.type, aggregate_id: sid, aggregate_seq: base + i, data: e.data, created_at: new Date().toISOString() };
      return `id: ${env.id}\nevent: ${env.type}\ndata: ${JSON.stringify(env)}\n\n`;
    })
    .join("");
  await page.route(
    (url) => url.pathname === "/api/events" && url.searchParams.get("follow") === "false" && url.searchParams.get("session_id") === sid,
    async (route) => {
      const res = await route.fetch();
      await route.fulfill({ response: res, body: (await res.text()) + extra });
    },
  );
}

test("share link: create, open the read-only page without auth, revoke", async ({ page, request, browser }) => {
  const s = await createSession(request, "e2e share");
  await chatAndWait(request, s.id, "share me please");
  await page.goto(`/session/${s.id}`);
  await page.getByTestId("share-button").click();
  await page.getByTestId("share-create").click();
  const url = await page.getByTestId("share-url").inputValue();
  expect(url).toMatch(/\/share\/[\w-]+$/);
  await shot(page, "i5-share-dialog");

  const viewerCtx = await browser.newContext(); // no cookie, no token: the link is the capability
  const viewer = await viewerCtx.newPage();
  await viewer.goto(url);
  await expect(viewer.getByTestId("share-title")).toHaveText("e2e share");
  await expect(viewer.getByTestId("share-readonly")).toBeVisible();
  await expect(viewer.getByTestId("msg-user")).toContainText("share me please");
  await expect(viewer.getByTestId("msg-assistant")).toContainText("echo: share me please");
  await expect(viewer.getByTestId("composer")).toHaveCount(0);
  await expect(viewer.getByTestId("msg-copy")).toHaveCount(0);
  await expect(viewer.getByTestId("share-button")).toHaveCount(0);
  await shot(viewer, "i5-share-page");

  await page.getByTestId("share-revoke").click();
  await expect(page.getByTestId("share-note")).toContainText("Revoked 1 link");
  await viewer.reload();
  await expect(viewer.getByTestId("share-error")).toContainText("revoked");
  await viewerCtx.close();
});

test("skills hub: search, install lands quarantined, approve, remove", async ({ page, request }) => {
  const src = (await (await request.get("/api/skills/sources")).json()) as { taps: string[] };
  test.skip(src.taps.length === 0, "no hub tap configured (run through `npm run e2e`)");
  await page.goto("/skills");
  await page.getByTestId("skills-hub-tab").click();
  const search = async () => {
    await page.getByTestId("hub-search-input").fill("e2e");
    await page.getByTestId("hub-search").click();
  };
  await search();
  await expect(page.getByTestId("hub-hit")).toHaveCount(2);
  const hit = page.locator('[data-testid="hub-hit"][data-name="e2e-hub-skill"]');
  await hit.getByTestId("hub-install").click();
  await expect(hit.getByTestId("quarantine-badge")).toBeVisible();
  const installed = page.locator('[data-testid="hub-installed-row"][data-name="e2e-hub-skill"]');
  await expect(installed).toHaveAttribute("data-status", "quarantined");
  await shot(page, "i5-skills-hub-quarantined");

  await hit.getByTestId("hub-approve").click();
  await expect(hit.getByTestId("approved-badge")).toBeVisible();
  await expect(installed).toHaveAttribute("data-status", "approved");
  await page.getByRole("tab", { name: "Installed" }).click();
  await expect(page.locator('[data-testid="skill-row"][data-name="e2e-hub-skill"]')).toBeVisible();

  await page.getByTestId("skills-hub-tab").click();
  await search();
  await hit.getByTestId("hub-remove").click();
  await expect(installed).toHaveCount(0);
  await expect(hit).toContainText("not installed");
  await shot(page, "i5-skills-hub");
});

test("diff view on edit and apply_patch cards (collapsible)", async ({ page, request }) => {
  const s = await createSession(request, "e2e diff");
  await page.goto(`/session/${s.id}`);
  const edit = { path: "i5-diff-demo.txt", old_string: "alpha\nbeta\ngamma", new_string: "alpha\nBETA\ngamma\ndelta" };
  await send(page, `/tool edit ${JSON.stringify(edit)}`); // same args as str_replace
  const card = page.locator('[data-testid="tool-card"][data-tool="edit"]');
  const diff = card.getByTestId("tool-diff");
  await expect(diff).toBeVisible();
  await expect(diff.getByTestId("diff-added")).toHaveText("+2");
  await expect(diff.getByTestId("diff-removed")).toHaveText("−1");
  await expect(diff.locator('[data-kind="del"]')).toHaveText(/beta/);
  await expect(diff.locator('[data-kind="add"]').first()).toHaveText(/BETA/);
  await shot(page, "i5-diff-view");
  await diff.getByTestId("tool-diff-toggle").click();
  await expect(diff.getByTestId("diff-lines")).toHaveCount(0);
  await diff.getByTestId("tool-diff-toggle").click();
  await expect(diff.getByTestId("diff-lines")).toBeVisible();
  await rejectIfAsked(page, card); // nothing is written (the file does not exist either)

  const patch = "*** Begin Patch\n*** Update File: demo.py\n@@\n-x = 1\n+x = 2\n*** End Patch";
  await send(page, `/tool apply_patch ${JSON.stringify({ patch })}`);
  const pcard = page.locator('[data-testid="tool-card"][data-tool="apply_patch"]');
  await expect(pcard.getByTestId("tool-diff")).toContainText("demo.py");
  await expect(pcard.getByTestId("diff-added")).toHaveText("+1");
  await expect(pcard.getByTestId("diff-removed")).toHaveText("−1");
  await rejectIfAsked(page, pcard);
});

/** Wait until the call either asks for approval (then reject it) or already failed. */
async function rejectIfAsked(page: Page, card: ReturnType<Page["locator"]>) {
  const approval = page.getByTestId("approval-card");
  await expect(async () => {
    expect((await card.getAttribute("data-status")) === "error" || (await approval.count()) > 0).toBe(true);
  }).toPass();
  if (await approval.count()) {
    await approval.getByRole("button", { name: "Reject" }).click();
    await approval.getByRole("button", { name: "Reject" }).click();
  }
  await expect(card).toHaveAttribute("data-status", "error");
}

test("approval card: edit the arguments, approve, the edited call runs", async ({ page, request }) => {
  const s = await createSession(request, "e2e edit args");
  await page.goto(`/session/${s.id}`);
  await send(page, '/tool bash {"command": "echo original-arg"}');
  const card = page.getByTestId("approval-card");
  await card.getByTestId("approval-edit").click();
  const editor = card.getByTestId("approval-edit-json");
  await expect(editor).toHaveValue(/original-arg/);
  await editor.fill("{not json");
  await expect(card.getByTestId("approval-edit-error")).toBeVisible();
  await expect(card.getByTestId("approval-edit-submit")).toBeDisabled();
  await editor.fill('{"command": "echo edited-by-human"}');
  await shot(page, "i5-approval-edit");
  await card.getByTestId("approval-edit-submit").click();
  const tool = page.locator('[data-testid="tool-card"][data-tool="bash"]');
  await expect(tool).toHaveAttribute("data-status", "completed");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("edited-by-human");
  const audit = (await (await request.get(`/api/audit?session_id=${s.id}&action=tool.input_edited`)).json()) as {
    items?: { decision: string }[];
    rows?: { decision: string }[];
  };
  expect(JSON.stringify(audit)).toContain("tool.input_edited");
});

test("message copy button and raw/rendered toggle", async ({ page, request, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const s = await createSession(request, "e2e copy raw");
  await chatAndWait(request, s.id, "**bold** words");
  await page.goto(`/session/${s.id}`);
  const msg = page.getByTestId("msg-assistant").last();
  await expect(msg.locator("strong")).toHaveText("bold");
  await msg.hover();
  await msg.getByTestId("msg-raw-toggle").click();
  await expect(msg.getByTestId("msg-raw")).toHaveText("echo: **bold** words");
  await shot(page, "i5-message-raw");
  await msg.getByTestId("msg-copy").click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("echo: **bold** words");
  await msg.getByTestId("msg-raw-toggle").click();
  await expect(msg.locator("strong")).toHaveText("bold");
});

test("several pending questions show as tabs", async ({ page, request }) => {
  const s = await createSession(request, "e2e question tabs");
  // A second pending question (as another tool would raise) rides along with the real ones.
  await page.route(
    (url) => url.pathname === "/api/questions/pending",
    async (route) => {
      const real = (await (await route.fetch()).json()) as unknown[];
      const extra = { request_id: "q-extra", session_id: s.id, questions: [{ question: "Injected second question?", header: "Second", options: [{ label: "ok" }] }] };
      await route.fulfill({ json: [...real, extra] });
    },
  );
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("question-form")).toContainText("Injected second question?");
  const ask = { questions: [{ question: "Which db?", header: "DB", options: [{ label: "sqlite" }, { label: "postgres" }] }] };
  await send(page, `/tool ask_user_question ${JSON.stringify(ask)}`);
  const tabs = page.getByTestId("question-tab");
  await expect(tabs).toHaveCount(2);
  await shot(page, "i5-question-tabs");
  await tabs.filter({ hasText: "DB" }).click();
  await expect(tabs.filter({ hasText: "DB" })).toHaveAttribute("aria-selected", "true");
  const form = page.getByTestId("question-form");
  await form.getByRole("button", { name: "sqlite" }).click();
  await form.getByRole("button", { name: "Send answer" }).click();
  await expect(page.getByTestId("msg-assistant").last()).toContainText("sqlite");
  await expect(tabs).toHaveCount(0);
});

test("mcp.elicitation.open opens only on click; breaker codes read clearly", async ({ page, request, baseURL }) => {
  const s = await createSession(request, "e2e signals");
  await chatAndWait(request, s.id, "hello");
  const target = `${baseURL}/api/health`;
  await appendReplay(page, s.id, [
    { type: "mcp.elicitation.open", data: { server: "demo-mcp", url: target, domain: "127.0.0.1", elicitation_id: "el-1" } },
    { type: "mcp.elicitation.open", data: { server: "evil", url: "javascript:alert(1)", domain: "x", elicitation_id: "el-2" } },
    { type: "run.blocked", data: { run_id: "run_b1", error: { code: "GUARDIAN_BREAKER", message: "3 consecutive guardian denials" } } },
    { type: "run.blocked", data: { run_id: "run_b2", error: { code: "DENIAL_LIMIT", message: "20 permission denials" } } },
    { type: "run.blocked", data: { run_id: "run_b3", error: { code: "DENIAL_LOOP", message: "3 consecutive permission denials" } } },
  ]);
  const opened: string[] = [];
  page.context().on("page", (p) => opened.push(p.url()));
  await page.goto(`/session/${s.id}`);
  const card = page.getByTestId("elicitation-card");
  await expect(card).toHaveCount(1); // the javascript: link is never offered
  await expect(card.getByTestId("elicitation-domain")).toHaveText("127.0.0.1");
  await expect(page.locator('[data-testid="thread-notice"][data-code="GUARDIAN_BREAKER"]')).toContainText("guardian");
  await expect(page.locator('[data-testid="thread-notice"][data-code="DENIAL_LIMIT"]')).toContainText("too many permission denials");
  await expect(page.locator('[data-testid="thread-notice"][data-code="DENIAL_LOOP"]')).toContainText("three permission denials in a row");
  await shot(page, "i5-elicitation-and-breakers");
  expect(opened).toEqual([]); // nothing opened before the click
  const popup = page.context().waitForEvent("page");
  await card.getByTestId("elicitation-open").click();
  const tab = await popup;
  await tab.waitForLoadState();
  expect(tab.url()).toBe(target);
  await expect(card).toHaveCount(0);
});

test("sessions table: project column and Queue action", async ({ page, request }) => {
  const s = await createSession(request, "e2e queue target");
  await page.goto("/sessions");
  const row = page.locator(`[data-testid="session-row"][data-session-id="${s.id}"]`);
  await expect(row.getByTestId("session-project")).toHaveText(/\S+/);
  await row.getByRole("button", { name: "Queue", exact: true }).click();
  await page.getByTestId("queue-input").fill("queued from the table");
  await shot(page, "i5-sessions-queue");
  await page.getByTestId("queue-submit").click();
  await expect(page.getByTestId("queue-notice")).toContainText("Queued");
  await expect
    .poll(async () => JSON.stringify(await (await request.get(`/api/sessions/${s.id}/messages`)).json()), { timeout: 20_000 })
    .toContain("echo: queued from the table");
});

test("memory page shows the usage/compression gauge", async ({ page, request }) => {
  await request.post("/api/memory/facts", { data: { content: "e2e gauge fact about tabs", category: "preference" } });
  await page.goto("/memory");
  const gauge = page.getByTestId("memory-usage");
  await expect(gauge).toBeVisible();
  await expect(gauge.getByTestId("memory-compression")).toHaveAttribute("aria-valuenow", /^\d+$/);
  await expect(gauge.getByTestId("memory-counts")).toContainText("Facts");
  await shot(page, "i5-memory-gauge");
});

test("jobs and teams reuse the thread cards + token badge for run transcripts", async ({ page, request }) => {
  await request.post("/api/cron/morning_brief/run");
  await page.goto("/jobs");
  const row = page.locator('[data-testid="cron-row"][data-name="morning_brief"]');
  await expect(row.getByTestId("cron-last")).toContainText("ok", { timeout: 20_000 });
  await row.getByRole("button", { name: "Runs of morning_brief" }).click();
  const drawer = page.getByTestId("runs-drawer");
  await drawer.getByTestId("transcript-toggle").first().click();
  const transcript = drawer.getByTestId("run-transcript");
  await expect(transcript.getByTestId("msg-assistant").first()).toBeVisible();
  await expect(transcript.getByTestId("token-badge")).toContainText("tok");
  await shot(page, "i5-jobs-transcript");
  await page.keyboard.press("Escape");

  const lead = await createSession(request, "e2e team lead");
  const name = `i5-team-${Date.now() % 100000}`;
  await chatAndWait(request, lead.id, `/tool team_create {"name": "${name}"}`);
  const team = ((await (await request.get("/api/teams")).json()) as { id: string; name: string }[]).find((t) => t.name === name);
  expect(team).toBeTruthy();
  await page.goto(`/teams?team=${team!.id}`);
  const member = page.getByTestId("member-row").first();
  await member.getByRole("button").first().click();
  const tt = page.getByTestId("run-transcript");
  await expect(tt.getByTestId("msg-user").first()).toBeVisible();
  await expect(tt.getByTestId("token-badge")).toBeVisible();
  await shot(page, "i5-teams-transcript");
});
