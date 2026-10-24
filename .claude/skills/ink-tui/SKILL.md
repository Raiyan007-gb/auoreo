---
name: ink-tui
description: Build web/tui, the terminal UI, with Ink (React for CLI) — render app, Box/Text layout, useInput/useFocus keyboard handling, useApp lifecycle, SSE transport. Use when working in web/tui in this repo.
---

# Ink — terminal UI (web/tui)

Ink renders React components to the terminal (Flexbox layout). The TUI is a **peer client of the same server** as the web app — both attach to `agent_api` via HTTP + SSE (plan.md Part C.1).

Docs: https://github.com/vadimdemedes/ink · Context7: `/vadimdemedes/ink` (React-for-CLI).

## Entry + render

```tsx
import { render, Box, Text } from "ink";

function App({ sessionId }: { sessionId: string }) {
  const [events, setEvents] = useState<EventPayload[]>([]);
  // subscribeEvents(...) → append to state; render thread + footer
  return (
    <Box flexDirection="column">
      <Box flexGrow={1}><MessageThread sessionId={sessionId} events={events} /></Box>
      <PromptFooter />
    </Box>
  );
}

render(<App sessionId="sess_..."/>);
```

- Use `Box`/`Text` — Ink primitives (`Box` = flex container, `Text` = terminal text). `Text color={...}` for ANSI styling.
- `render()` returns `{ unmount, waitUntilExit }` — keep a handle for programmatic exit.

## Keyboard + focus (multi-pane)

```tsx
import { useInput, useFocus, useFocusManager } from "ink";

function PromptFooter({ active }: { active: boolean }) {
  const { isFocused, focus } = useFocus();
  useInput((input, key) => {
    if (!isFocused) return;
    if (key.return) submit(input);      // Enter
    else if (key.escape) cancel();
    else if (key.tab) focusNextPane();  // move between message list / prompt / model picker
  }, { isActive: active });
  return <Text color={isFocused ? "cyan" : "white"}>{"> " + input}</Text>;
}
```

- `useFocusManager().focus(id)` to move focus programmatically (e.g. Tab cycles panes).
- Hold "input buffer" state in the component (Ink input is event-based; there's no native <input>).

## App lifecycle / terminal control

```tsx
import { useApp } from "ink";
const { exit, waitUntilRenderFlush, suspendTerminal } = useApp();
```

- `exit()` unmounts the app; `waitUntilRenderFlush()` for sequencing output with shell.
- `suspendTerminal(async () => {})` to hand the terminal to a child process (e.g. `$EDITOR`, a pager) then restore — useful for `agent run`/reviews.

## Streaming the event feed

- Same shared `GET /api/event` SSE stream as the web app (see `typescript` skill `subscribeEvents` + `sse-events` skill). Node's SSE: use `eventsource`/`EventSource` polyfill or `@microsoft/fetch-event-source`; reconnect + resume via `Last-Event-ID`.
- Render `message.partial` deltas into an assistant bubble; `tool.started/completed` into `ToolCallView` state lines; `permission.requested`/`question.asked` into prompt footer.

## Commands (slash) + help (plan.md D tui `commands/`)

- Parse `/name args` in the prompt footer; route to command handlers; inject `/{agent CliArg...}` style prompts. OpenAPI-driven SDK for API calls (see `sdk` skill).

## Gotchas

- **Ink v7 requires Node.js ≥ 22 and React ≥ 19.2** (v7.0.0 breaking change) — and physical Backspace sets `key.backspace` (not `key.delete`), `key.meta` no longer set on plain Escape.
- Ink is single-threaded; keep `useInput` handlers cheap and non-blocking.
- Long-running: call `update()`/setState rather than mutating the rendered tree directly; the full `render()` is per-root.
- Terminal themes: expose color via props/theme copy so `Theme` reuse works (app + TUI share tokens).
- Windows: Ink supports Windows terminals; test `web/tui` on Windows host (this repo's dev machine) and in WSL/CI (linux + darwin).