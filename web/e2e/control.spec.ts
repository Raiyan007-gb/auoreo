import { expect, type Page, test } from "@playwright/test";
import { createSession, send, shot } from "./helpers";

/** Record every JSON frame the page sends/receives on the real `/ws` control plane. */
function recordWs(page: Page) {
  const sent: Record<string, unknown>[] = [];
  const received: Record<string, unknown>[] = [];
  const parse = (payload: string | Buffer) => {
    try {
      return JSON.parse(String(payload)) as Record<string, unknown>;
    } catch {
      return null;
    }
  };
  page.on("websocket", (ws) => {
    if (!/\/ws(\?|$)/.test(ws.url())) return;
    ws.on("framesent", (f) => {
      const j = parse(f.payload);
      if (j) sent.push(j);
    });
    ws.on("framereceived", (f) => {
      const j = parse(f.payload);
      if (j) received.push(j);
    });
  });
  return { sent, received };
}

test("approval goes over the real WebSocket control_response", async ({ page, request }) => {
  const frames = recordWs(page);
  const s = await createSession(request, "e2e ws approval");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "open");
  await send(page, '/tool bash {"command": "echo ws"}');
  const card = page.getByTestId("approval-card");
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Approve once" }).click();
  await expect(card).toBeHidden();
  await expect(page.locator('[data-testid="tool-card"][data-tool="bash"]')).toHaveAttribute("data-status", "completed");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("ws");

  // hello seeded from GET /api/events/latest (a live tail, never a full replay).
  const hello = frames.sent.find((f) => f.type === "hello");
  expect(hello).toMatchObject({ type: "hello", channels: null });
  expect(typeof hello?.last_seq).toBe("number");
  expect(frames.received.find((f) => f.type === "connected")).toMatchObject({
    type: "connected",
    server_seq: expect.any(Number),
    generation: expect.any(Number),
  });
  const resp = frames.sent.find((f) => f.type === "control_response");
  expect(resp).toMatchObject({ response: { kind: "permission", reply: "once", message: null } });
  const ack = frames.received.find((f) => f.type === "ack" && f.request_id === resp?.request_id);
  expect(ack).toMatchObject({ ok: true });
  // The live tail delivered the permission events over the WS.
  expect(frames.received.some((f) => f.type === "permission.requested" && f.aggregate_id === s.id)).toBe(true);
  await shot(page, "ws-approval");
});

test("interrupt over the WebSocket cancels a pending bash call", async ({ page, request }) => {
  const frames = recordWs(page);
  const s = await createSession(request, "e2e ws interrupt");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "open");
  await send(page, '/tool bash {"command": "echo never"}');
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await page.getByRole("button", { name: "Interrupt" }).click();

  await expect(page.getByTestId("thread-notice").filter({ hasText: "Run interrupted" })).toBeVisible();
  await expect(page.getByTestId("approval-card")).toHaveCount(0); // the cancelled call's prompt is gone
  await expect(page.locator('[data-testid="tool-card"][data-tool="bash"]')).toHaveAttribute("data-status", "error");
  await expect(page.getByTestId("session-status")).toHaveText("Idle");
  await expect(page.getByText("Interrupt sent (ws)")).toBeVisible();
  const req = frames.sent.find((f) => f.type === "control_request");
  expect(req).toMatchObject({ request: { kind: "interrupt", session_id: s.id } });
  expect(frames.received.find((f) => f.type === "ack" && f.request_id === req?.request_id)).toMatchObject({
    ok: true,
    detail: "cancelled",
  });
  await shot(page, "ws-interrupt");
});

test("rejecting a tool call shows the PERMISSION_REJECTED notice", async ({ page, request }) => {
  const s = await createSession(request, "e2e reject notice");
  await page.goto(`/session/${s.id}`);
  await send(page, '/tool bash {"command": "echo no"}');
  const card = page.getByTestId("approval-card");
  await card.getByRole("button", { name: "Reject" }).click();
  await card.getByRole("button", { name: "Reject" }).click();
  const notice = page.locator('[data-testid="thread-notice"][data-code="PERMISSION_REJECTED"]');
  await expect(notice).toContainText("Run blocked");
  await expect(notice).toContainText("rejected");
});

/** Kept as a mock: it is the only way to force the REST fallback path deterministically. */
test("question form answers via REST when the WebSocket is unavailable", async ({ page, request }) => {
  await page.routeWebSocket(/\/ws(\?|$)/, (ws) => ws.close({ code: 4003, reason: "unauthorized" }));
  const s = await createSession(request, "e2e question");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "unavailable");
  await expect(page.getByTestId("ws-state")).toContainText("offline");
  await send(
    page,
    '/tool ask_user_question {"questions": [{"question": "Pick a color", "header": "Color", "options": [{"label": "Red"}, {"label": "Blue"}]}]}',
  );
  const form = page.getByTestId("question-form");
  await expect(form).toContainText("Pick a color");
  await form.getByRole("button", { name: "Blue" }).click();
  await expect(form.getByRole("button", { name: "Blue" })).toHaveAttribute("aria-pressed", "true");
  await shot(page, "question-form");
  await form.getByRole("button", { name: "Send answer" }).click();
  await expect(form).toBeHidden();
  const card = page.locator('[data-testid="tool-card"][data-tool="ask_user_question"]');
  await expect(card).toHaveAttribute("data-status", "completed");
  await expect(card.getByTestId("tool-result-preview")).toContainText("Blue");
});
