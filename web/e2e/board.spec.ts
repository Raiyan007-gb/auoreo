import { expect, test } from "@playwright/test";
import { chatAndWait, createSession, shot } from "./helpers";

interface Team {
  id: string;
  name: string;
}

test("board tab lists keys, shows a value with producer/version, and the decisions timeline", async ({
  page,
  request,
}) => {
  // A session creates a team through the fake gateway's `/tool` hook, then the operator
  // writes two keys over the audited REST endpoint.
  const name = `e2e-board-${Date.now()}`;
  const s = await createSession(request, "e2e board");
  await chatAndWait(request, s.id, `/tool team_create {"name": "${name}"}`);
  const team = ((await (await request.get("/api/teams")).json()) as Team[]).find((t) => t.name === name);
  expect(team).toBeTruthy();
  const id = team!.id;

  const put = (key: string, value: string) => request.put(`/api/teams/${id}/board/${key}`, { data: { value } });
  expect((await put("plan", "1. build\n2. review")).status()).toBe(200);
  expect((await put("results/task-1", "first draft")).status()).toBe(200);
  const second = await put("results/task-1", "widget built: 3 files changed");
  expect((await second.json()).version).toBe(2);
  expect((await put("Bad Key", "x")).status()).toBe(422);
  expect((await put("leak", "sk-proj-abcdefghijklmnopqrstu")).status()).toBe(422);

  await page.goto(`/teams?tab=board&team=${id}`);
  await expect(page.getByRole("tab", { name: "Board" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Team", { exact: true })).toHaveValue(id);
  const rows = page.getByTestId("board-row");
  await expect(rows).toHaveCount(2);
  const row = page.locator('[data-testid="board-row"][data-key="results/task-1"]');
  await expect(row).toContainText("v2");
  await expect(row).toContainText("api");

  await row.getByRole("button", { name: "results/task-1" }).click();
  await expect(page.getByTestId("board-value")).toHaveText("widget built: 3 files changed");
  await expect(page.getByRole("heading", { name: "results/task-1" })).toBeVisible();

  await page.getByLabel("Key prefix").fill("results/");
  await expect(rows).toHaveCount(1);

  await expect(page.getByRole("heading", { name: "Supervisor decisions" })).toBeVisible();
  await expect(page.getByText("No decisions logged")).toBeVisible();
  await shot(page, "teams-board");
});
