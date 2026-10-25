// G5b: Webhooks + Messaging pages, ctx_viz gauge, shared EventsContext, Cmd-K parity.
import { expect, test } from "@playwright/test";
import { chatAndWait, createSession, send, shot } from "./helpers";

test("webhooks page: route table with last delivery, deliveries drawer", async ({ page }) => {
  await page.goto("/webhooks");
  await expect(page.getByRole("heading", { name: "Webhooks" })).toBeVisible();
  const row = page.locator('[data-testid="webhook-row"][data-name="github-pr"]');
  await expect(row).toContainText("/hooks/github-pr");
  await expect(row).toContainText("pull_request");
  await expect(row).toContainText("missing"); // secret state, value never shown
  await expect(row.getByTestId("webhook-last")).toContainText(/never|#\d+/);
  await expect(page.getByRole("columnheader", { name: "Last delivery" })).toBeVisible();
  await shot(page, "webhooks");
  await row.getByRole("button", { name: "Deliveries of github-pr" }).click();
  await expect(page.getByTestId("deliveries-drawer")).toContainText("Replay runs a dry run first");
  // The nav carries the dedicated page; Jobs keeps its tab.
  await page.keyboard.press("Escape");
  await expect(page.getByRole("link", { name: "Webhooks" }).first()).toBeVisible();
});

test("messaging page: connectors, pairing error, allowlist note, menu clamp warning", async ({ page }) => {
  await page.route("**/api/commands", async (route) => {
    const res = await route.fetch();
    const cmds = (await res.json()) as unknown[];
    cmds.push({ name: "summarize_the_whole_project_history_now", description: "long label", handler: "prompt", args_hint: "" });
    await route.fulfill({ response: res, json: cmds });
  });
  await page.goto("/platforms");
  await expect(page.getByRole("heading", { name: "Messaging" })).toBeVisible();
  const tg = page.locator('[data-testid="platform-card"][data-name="telegram"]');
  await expect(tg).toBeVisible();
  await expect(page.locator('[data-testid="platform-card"][data-name="discord"]')).toBeVisible();
  await expect(tg).toContainText("connector");
  await expect(tg.getByTestId("allowlist-empty")).toBeVisible();
  await expect(tg).toContainText("plugins.telegram.allowlist");
  const preview = tg.getByTestId("menu-preview");
  await expect(preview.getByTestId("menu-entry").filter({ hasText: "/compact" })).toBeVisible();
  await expect(preview.getByTestId("menu-clamp-warning")).toContainText("longer than 32 characters");
  await expect(preview.locator('[data-testid="menu-entry"][data-clamped="true"]')).toContainText("39/32");
  await shot(page, "platforms");
  await tg.getByLabel("Pairing code").fill("000000");
  await tg.getByRole("button", { name: "Pair" }).click();
  await expect(tg.getByRole("alert").filter({ hasText: /pairing code|not running|unknown/i })).toBeVisible();
});

test("ctx gauge in the session header and per session on Usage", async ({ page, request }) => {
  const s = await createSession(request, "e2e ctx gauge");
  await chatAndWait(request, s.id, "hello gauge");
  const ctx = (await (await request.get(`/api/sessions/${s.id}/context`)).json()) as {
    context_window: number;
    messages_tokens: number;
    system_tokens: number;
  };
  expect(ctx.messages_tokens).toBeGreaterThan(0);
  await page.goto(`/session/${s.id}`);
  const gauge = page.getByTestId("ctx-gauge");
  await expect(gauge).toBeVisible();
  await expect(gauge.getByRole("meter")).toHaveAttribute("aria-valuemax", String(ctx.context_window));
  await gauge.hover();
  const tip = page.getByTestId("ctx-tooltip").first();
  await expect(tip).toContainText("System prompt");
  await expect(tip).toContainText("Tool schemas");
  await expect(tip).toContainText("Messages");
  await shot(page, "ctx-gauge");
  await page.goto("/usage");
  await expect(page.locator(`[data-testid="usage-ctx"][data-session="${s.id}"]`)).toBeVisible();
});

test("one shared EventSource per tab across pages; the thread still streams", async ({ page, request }) => {
  await page.addInitScript(() => {
    const Native = window.EventSource;
    const w = window as unknown as { __esUrls: string[] };
    w.__esUrls = [];
    window.EventSource = class extends Native {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init);
        w.__esUrls.push(String(url));
      }
    } as typeof EventSource;
  });
  const s = await createSession(request, "e2e shared stream");
  await page.goto(`/session/${s.id}`);
  await send(page, "hello shared stream");
  await expect(page.getByTestId("thread")).toContainText("hello shared stream");
  await expect(page.locator('[data-testid="session-status"]')).toHaveText("idle", { timeout: 20_000 });
  // Client-side navigation to pages that follow the server channel reuses the same stream.
  await page.getByRole("link", { name: "Plugins & MCP" }).click();
  await page.getByRole("link", { name: "Jobs" }).click();
  await page.getByRole("link", { name: "Webhooks" }).first().click();
  const urls = await page.evaluate(() => (window as unknown as { __esUrls: string[] }).__esUrls);
  expect(urls.length).toBe(1);
  expect(urls[0]).toContain("/api/events?after_seq=");
  expect(urls[0]).not.toContain("session_id=");
});

test("command palette: session verbs, models, skills and plugins", async ({ page, request }) => {
  const s = await createSession(request, "e2e palette parity");
  await page.goto(`/session/${s.id}`);
  await page.keyboard.press("Control+k");
  const input = page.getByPlaceholder("Jump to a page, session, agent or command…");
  for (const c of ["steer", "queue", "fork", "resume"])
    await expect(page.locator(`[data-testid="palette-verb"][data-command="${c}"]`)).toHaveCount(1);
  for (const c of ["model", "compact", "goal", "review"])
    await expect(page.locator(`[data-testid="palette-command"][data-command="${c}"]`)).toHaveCount(1);
  await expect(page.getByTestId("palette-model").first()).toBeAttached();
  await expect(page.getByTestId("palette-skill").first()).toBeAttached();
  await expect(page.getByTestId("palette-plugin").first()).toBeAttached();
  await input.fill("steer");
  await page.locator('[data-testid="palette-verb"][data-command="steer"]').click();
  await expect(page.getByTestId("composer")).toHaveValue("/steer ");
  await page.keyboard.press("Control+k");
  await input.fill("resume");
  await page.locator('[data-testid="palette-verb"][data-command="resume"]').click();
  await expect(input).toHaveValue("session ");
  await shot(page, "palette-parity");
});
