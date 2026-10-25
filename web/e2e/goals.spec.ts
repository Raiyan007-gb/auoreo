import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

interface Goal {
  id: number;
  title: string;
  progress: number;
}
interface Proposed {
  suggestion: { id: number; status: string };
  created: boolean;
}

test("goals: create via API, see it, update progress in the UI", async ({ page, request }) => {
  const res = await request.post("/api/goals", { data: { title: "e2e ship M6", description: "all gates green" } });
  expect(res.status()).toBe(201);
  const goal = (await res.json()) as Goal;

  await page.goto("/goals");
  const row = page.locator(`[data-testid="goal-row"][data-goal-id="${goal.id}"]`);
  await expect(row).toContainText("e2e ship M6");
  await expect(row.getByTestId("goal-progress")).toHaveText("0%");

  await row.getByLabel("Progress %").fill("40");
  await row.getByRole("button", { name: "Save" }).click();
  await expect(row.getByTestId("goal-progress")).toHaveText("40%");
  await expect(row.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
  const after = (await (await request.get(`/api/goals/${goal.id}`)).json()) as Goal;
  expect(after.progress).toBeCloseTo(0.4);

  // a change made elsewhere (API) shows up live via the goal.updated event
  await request.patch(`/api/goals/${goal.id}`, { data: { progress: 0.75 } });
  await expect(row.getByTestId("goal-progress")).toHaveText("75%");

  await row.getByLabel("Status").selectOption("done");
  await expect(row.getByTestId("goal-status")).toHaveText("done");
  await shot(page, "goals");
});

test("goals: absorb one goal into another from the dialog", async ({ page, request }) => {
  const a = (await (await request.post("/api/goals", { data: { title: "e2e dup A" } })).json()) as Goal;
  const b = (await (await request.post("/api/goals", { data: { title: "e2e dup B" } })).json()) as Goal;
  await page.goto("/goals");
  const row = page.locator(`[data-testid="goal-row"][data-goal-id="${a.id}"]`);
  await row.getByRole("button", { name: "Absorb…" }).click();
  const dialog = page.getByRole("dialog", { name: "Absorb goal" });
  await dialog.getByLabel("Absorb into").selectOption(String(b.id));
  await dialog.getByRole("button", { name: "Absorb" }).click();
  await expect(dialog).toBeHidden();
  await expect(row.getByTestId("goal-status")).toHaveText(`absorbed → #${b.id}`);
});

test("suggestions: propose via the API, accept in the UI, status changes", async ({ page, request }) => {
  const res = await request.post("/api/suggestions", {
    data: { title: "e2e: review the open goals", kind: "goal", body: "Two goals look stale." },
  });
  expect(res.status()).toBe(201);
  const { suggestion } = (await res.json()) as Proposed;
  const denyMe = ((await (await request.post("/api/suggestions", { data: { title: "e2e: deny me" } })).json()) as Proposed)
    .suggestion;

  await page.goto("/goals");
  await page.getByRole("tab", { name: /Suggestions/ }).click();
  const card = page.locator(`[data-testid="suggestion-card"][data-suggestion-id="${suggestion.id}"]`);
  await expect(card).toContainText("Two goals look stale.");
  await expect(card).toHaveAttribute("data-status", "pending");
  await shot(page, "suggestions");

  await card.getByRole("button", { name: "Accept" }).click();
  await expect(card).toHaveAttribute("data-status", "accepted");
  await expect(card.getByTestId("suggestion-status")).toHaveText("accepted");

  const deny = page.locator(`[data-testid="suggestion-card"][data-suggestion-id="${denyMe.id}"]`);
  await deny.getByRole("button", { name: "Deny" }).click();
  await expect(deny).toHaveAttribute("data-status", "denied");

  const accepted = (await (await request.get("/api/suggestions?status=accepted")).json()) as { id: number }[];
  expect(accepted.map((s) => s.id)).toContain(suggestion.id);
});
