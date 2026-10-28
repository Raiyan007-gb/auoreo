import { expect, test } from "@playwright/test";
import type { Item } from "../app/src/lib/thread";
import { groupToolRuns, summarizeTools } from "../app/src/lib/toolGroups";

const tool = (n: number, name = "read", status: "completed" | "error" | "running" | "pending" = "completed"): Item => ({
  kind: "tool",
  key: `t${n}`,
  runId: "r1",
  callId: `c${n}`,
  name,
  args: "{}",
  status,
});
const say = (n: number): Item => ({ kind: "assistant", key: `a${n}`, text: "ok", streaming: false });

test("a finished run of 3+ tool calls collapses into one group, keyed by its first call", () => {
  const rows = groupToolRuns([tool(1), tool(2), tool(3), tool(4), say(1)]);
  expect(rows.map((r) => r.kind)).toEqual(["toolgroup", "assistant"]);
  const g = rows[0];
  expect(g?.kind === "toolgroup" && g.tools.length).toBe(4);
  expect(g?.key).toBe("g:t1");
});

test("short runs, runs at the end of the thread, and unfinished calls stay as cards", () => {
  expect(groupToolRuns([tool(1), tool(2), say(1)]).map((r) => r.kind)).toEqual(["tool", "tool", "assistant"]);
  expect(groupToolRuns([say(1), tool(1), tool(2), tool(3)]).map((r) => r.kind)).toEqual(["assistant", "tool", "tool", "tool"]);
  const live = groupToolRuns([tool(1), tool(2), tool(3, "bash", "running"), say(1)]);
  expect(live.map((r) => r.kind)).toEqual(["tool", "tool", "tool", "assistant"]);
  const waiting = groupToolRuns([tool(1), tool(2), tool(3), tool(4, "bash", "pending"), say(1)]);
  expect(waiting.map((r) => r.kind)).toEqual(["toolgroup", "tool", "assistant"]);
});

test("errors count as finished, and the summary lists the most used tools first", () => {
  const rows = groupToolRuns([tool(1, "grep", "error"), tool(2), tool(3), tool(4, "bash"), say(1)]);
  expect(rows[0]?.kind).toBe("toolgroup");
  const tools = rows[0]?.kind === "toolgroup" ? rows[0].tools : [];
  expect(summarizeTools(tools)).toBe("read ×2 · grep · bash");
  expect(summarizeTools([tool(1, "a"), tool(2, "b"), tool(3, "c"), tool(4, "d")] as never)).toBe("a · b · c · +1 more");
});
