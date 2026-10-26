import { expect, test } from "@playwright/test";
import { chatAndWait, createSession, send, shot } from "./helpers";

const BASH = '/tool bash {"command": "echo hi"}';

test("thinking block streams, collapses and the toggle is remembered", async ({ page, request }) => {
  const s = await createSession(request, "e2e thinking");
  await page.goto(`/session/${s.id}`);
  await send(page, "/think the plan");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("echo: the plan");
  const block = page.getByTestId("thinking-block");
  await expect(block).toBeVisible();
  await expect(block).toHaveAttribute("data-streaming", "false");
  await block.getByRole("button").click(); // finished thoughts start collapsed
  await expect(page.getByTestId("thinking-text")).toContainText("Thinking about 'the plan'");
  await shot(page, "g7-thinking");

  const toggle = page.getByTestId("thinking-toggle");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(block).toBeHidden();
  await page.reload();
  await expect(page.getByTestId("msg-assistant").last()).toContainText("echo: the plan");
  await expect(page.getByTestId("thinking-toggle")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByTestId("thinking-block")).toHaveCount(0);
  await page.getByTestId("thinking-toggle").click();
  await expect(page.getByTestId("thinking-block")).toBeVisible();
});

test("@skill mention autocompletes and loads the skill for the turn", async ({ page, request }) => {
  const s = await createSession(request, "e2e skill mention");
  await page.goto(`/session/${s.id}`);
  const box = page.getByTestId("composer");
  await box.fill("please review with @code-r");
  const list = page.getByTestId("skill-mentions");
  await expect(list).toBeVisible();
  await shot(page, "g7-skill-mention");
  await list.getByTestId("skill-mention").filter({ hasText: "@code-review" }).click();
  await expect(box).toHaveValue("please review with @code-review ");
  await box.press("Enter");
  await expect(page.locator('[data-testid="thread-notice"][data-code="skill.loaded"]')).toContainText("code-review");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("code-review");
});

test("plan panel shows update_plan steps and the plan review renders Markdown", async ({ page, request }) => {
  const s = await createSession(request, "e2e plan review");
  await page.goto(`/session/${s.id}`);
  const steps = [
    { step: "Read the code", status: "completed" },
    { step: "Write the fix", status: "in_progress" },
    { step: "Run the tests", status: "pending", done_when: "pytest passes" },
  ];
  await send(page, `/tool update_plan ${JSON.stringify({ steps, explanation: "three steps" })}`);
  const panel = page.getByTestId("plan-panel");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("plan-step")).toHaveCount(3);
  await expect(panel.getByTestId("plan-progress")).toHaveText("1/3");
  await expect(panel).toContainText("done when: pytest passes");

  await send(page, `/tool exit_plan_mode ${JSON.stringify({ plan: "# Fix plan\n\n- patch `app.py`\n- add a test" })}`);
  const review = page.getByTestId("plan-review");
  await expect(review).toBeVisible();
  await expect(review.getByTestId("plan-markdown").getByRole("heading", { name: "Fix plan" })).toBeVisible();
  await expect(review.getByTestId("plan-markdown").locator("code")).toHaveText("app.py");
  await shot(page, "g7-plan-review");
  await review.getByRole("button", { name: "Approve plan" }).click();
  await expect(review).toBeHidden();
  await expect(page.locator('[data-testid="tool-card"][data-tool="exit_plan_mode"]')).toHaveAttribute("data-status", "completed");
  // Reloading keeps the plan (seeded from GET /api/sessions/{id}/plan + event replay).
  await page.reload();
  await expect(page.getByTestId("plan-panel").getByTestId("plan-step")).toHaveCount(3);
});

test("ask_user_question: options-only vs free text, with a countdown", async ({ page, request }) => {
  const s = await createSession(request, "e2e ask user");
  await page.goto(`/session/${s.id}`);
  const questions = [
    { question: "Strict mode?", options: [{ label: "yes" }, { label: "no" }], allowCustom: false },
    { question: "Which db?", options: [{ label: "sqlite" }, { label: "postgres" }] },
  ];
  await send(page, `/tool ask_user_question ${JSON.stringify({ questions })}`);
  const form = page.getByTestId("question-form");
  await expect(form).toBeVisible();
  await expect(form.getByTestId("question-timer")).toContainText(/Agent waits \d+s more/);
  const [strict, db] = [form.getByTestId("question").nth(0), form.getByTestId("question").nth(1)];
  await expect(strict).toHaveAttribute("data-allow-custom", "false");
  await expect(strict.getByRole("textbox")).toHaveCount(0);
  await strict.getByRole("button", { name: "no" }).click();
  await db.getByRole("button", { name: "postgres" }).click();
  await db.getByLabel("Other answer for: Which db?").fill("version 17");
  await shot(page, "g7-ask-user");
  await form.getByRole("button", { name: "Send answer" }).click();
  await expect(form).toBeHidden();
  const card = page.locator('[data-testid="tool-card"][data-tool="ask_user_question"]');
  await expect(card).toHaveAttribute("data-status", "completed");
  await expect(page.getByTestId("msg-assistant").last()).toContainText('"customText": "version 17"');
});

test("approvals: bulk approve the matching group, then follow the audit link", async ({ page, request }) => {
  const a = await createSession(request, "e2e bulk a");
  const b = await createSession(request, "e2e bulk b");
  await request.post("/api/chat", { data: { session_id: a.id, message: BASH } });
  await request.post("/api/chat", { data: { session_id: b.id, message: BASH } });
  await page.goto("/approvals");
  const groupA = page.getByTestId("approval-group").filter({ hasText: "e2e bulk a" });
  const groupB = page.getByTestId("approval-group").filter({ hasText: "e2e bulk b" });
  await expect(groupA).toBeVisible();
  await expect(groupB).toBeVisible();
  const bulk = groupA.getByTestId("approve-all");
  await expect(bulk).toContainText(/Approve all \d+/);
  await expect(groupA.getByRole("button", { name: "Always" })).toBeVisible(); // not one-shot
  await shot(page, "g7-approvals-bulk");
  await bulk.click();
  await expect(groupA).toBeHidden();
  await expect(groupB).toBeHidden();
  const decisions = page.getByTestId("recent-decisions");
  await expect(decisions.getByTestId("decision-row").filter({ hasText: "e2e bulk b" })).toContainText("bulk");
  await expect(decisions.getByTestId("decision-row").filter({ hasText: "e2e bulk a" })).toContainText("bash");
  await shot(page, "g7-approvals-decisions");
  await decisions.getByTestId("decision-row").filter({ hasText: "e2e bulk a" }).getByTestId("audit-link").click();
  await expect(page).toHaveURL(/\/audit\?session_id=.*action=permission\.reply/);
  await expect(page.getByLabel("Session id")).toHaveValue(a.id);
  await expect(page.getByTestId("audit-row").first()).toContainText("permission.reply");
});

test("usage page shows token/cost charts and the budget guards", async ({ page, request }) => {
  const s = await createSession(request, "e2e usage charts");
  await chatAndWait(request, s.id, "hello usage");
  await page.goto("/usage");
  const charts = page.getByTestId("usage-charts");
  await expect(charts).toBeVisible();
  await expect(charts.getByTestId("chart-tokens").getByTestId("chart-bar")).not.toHaveCount(0);
  await expect(charts.getByTestId("chart-cost")).toBeVisible();
  const budget = page.getByTestId("budget-card");
  await expect(budget).toBeVisible();
  await expect(budget.getByTestId("budget-cap")).toHaveText(/per session|no spend cap/);
  await expect(budget.getByTestId("budget-row").first()).toBeVisible();
  await shot(page, "g7-usage-charts");
});

test("plugins page: per-session source toggle and the hot-reload log", async ({ page, request }) => {
  const s = await createSession(request, "e2e sources");
  await page.goto("/plugins");
  const card = page.getByTestId("session-sources");
  await expect(card).toBeVisible();
  await card.getByTestId("session-sources-select").selectOption(s.id);
  const row = card.locator('[data-testid="session-source"][data-id="plugin:telegram"]');
  await expect(row).toHaveAttribute("data-enabled", "true");
  await row.getByRole("checkbox").uncheck();
  await expect(row).toHaveAttribute("data-enabled", "false");
  const got = (await (await request.get(`/api/sessions/${s.id}/sources`)).json()) as { disabled: string[] };
  expect(got.disabled).toContain("plugin:telegram");
  await page.getByRole("button", { name: "Hot reload" }).click();
  await expect(page.getByTestId("reload-log").locator('[data-type="harness.reloaded"]').first()).toBeVisible();
  await shot(page, "g7-plugins-sources");
  await row.getByRole("checkbox").check();
  await expect(row).toHaveAttribute("data-enabled", "true");
});
