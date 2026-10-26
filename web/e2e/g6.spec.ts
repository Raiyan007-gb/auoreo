import { expect, test } from "@playwright/test";
import { shot } from "./helpers";

test("preference proposals: approve applies to PREFS.md with the approval event id, deny drops", async ({ page, request }) => {
  const marker = `e2e-pref-${Date.now()}`;
  const make = async (content: string) => {
    const r = await request.post("/api/memory/prefs/proposals", { data: { action: "add", content } });
    expect(r.status()).toBe(201);
    return ((await r.json()) as { id: string }).id;
  };
  const keep = await make(`Keep ${marker}`);
  const drop = await make(`Drop ${marker}`);
  await page.goto("/memory");
  await page.getByRole("tab", { name: "Curator" }).click();
  const keepRow = page.locator(`[data-testid="prefs-proposal"][data-proposal-id="${keep}"]`);
  const dropRow = page.locator(`[data-testid="prefs-proposal"][data-proposal-id="${drop}"]`);
  await expect(keepRow).toContainText(marker);
  await shot(page, "memory-prefs-proposals");
  await keepRow.getByRole("button", { name: `Approve preference ${keep}` }).click();
  await expect(keepRow).toHaveCount(0);
  await dropRow.getByRole("button", { name: `Deny preference ${drop}` }).click();
  await expect(dropRow).toHaveCount(0);
  const all = (await (await request.get("/api/memory/prefs/proposals")).json()) as {
    id: string;
    status: string;
    approval_event_id: number | null;
  }[];
  const approved = all.find((p) => p.id === keep);
  expect(approved?.status).toBe("approved");
  expect(approved?.approval_event_id).toBeGreaterThan(0);
  expect(all.find((p) => p.id === drop)?.status).toBe("denied");
  const files = (await (await request.get("/api/memory/files")).json()) as { files: { name: string; content: string }[] };
  const prefs = files.files.find((f) => f.name === "PREFS.md");
  expect(prefs?.content).toContain(`Keep ${marker}`);
  expect(prefs?.content).not.toContain(`Drop ${marker}`);
});
