import { expect, test } from "@playwright/test";
import { createSession, send, shot } from "./helpers";

/**
 * The `/ws` control plane is built by a parallel workstream; until it lands, these tests mock
 * it with `page.routeWebSocket` speaking `src/auoreo/schema/ws.py` exactly, and forward the
 * client's `control_response` to the real REST endpoint so the backend run completes.
 */
test("approval goes over the WebSocket control_response when /ws is up", async ({ page, request, baseURL }) => {
  const frames: Record<string, unknown>[] = [];
  await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
    ws.onMessage(async (raw) => {
      const f = JSON.parse(String(raw)) as Record<string, unknown>;
      frames.push(f);
      if (f.type === "hello") {
        ws.send(
          JSON.stringify({ type: "connected", server_seq: 0, generation: 1, pending_permissions: [], pending_questions: [] }),
        );
        ws.send(JSON.stringify({ type: "ping", tick: 1, server_seq: 0 }));
      }
      if (f.type === "control_response") {
        const r = f.response as { kind: string; reply: string; message?: string | null };
        const res = await request.post(`${baseURL}/api/permissions/${String(f.request_id)}/reply`, {
          data: { reply: r.reply, message: r.message ?? null },
        });
        ws.send(JSON.stringify({ type: "ack", request_id: f.request_id, ok: res.ok() }));
      }
    });
  });

  const s = await createSession(request, "e2e ws approval");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "open");
  await send(page, '/tool bash {"command": "echo ws"}');
  await page.getByTestId("approval-card").getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator('[data-testid="tool-card"][data-tool="bash"]')).toHaveAttribute("data-status", "completed");

  const hello = frames.find((f) => f.type === "hello");
  expect(hello).toMatchObject({ type: "hello", channels: null });
  expect(frames).toContainEqual({ type: "pong", tick: 1 });
  const resp = frames.find((f) => f.type === "control_response");
  expect(resp).toMatchObject({ response: { kind: "permission", reply: "once", message: null } });
  expect(typeof resp?.request_id).toBe("string");
  await shot(page, "ws-approval");
});

test("question form answers via REST when the WebSocket is unavailable", async ({ page, request }) => {
  await page.routeWebSocket(/\/ws(\?|$)/, (ws) => ws.close({ code: 4003, reason: "unauthorized" }));
  const s = await createSession(request, "e2e question");
  await page.goto(`/session/${s.id}`);
  await expect(page.getByTestId("ws-state")).toHaveAttribute("data-state", "unavailable");
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
