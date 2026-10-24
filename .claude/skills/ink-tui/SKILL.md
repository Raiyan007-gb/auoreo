---
name: ink-tui
description: Build web/tui, the terminal UI, with Ink (React for CLI) — render app, Box/Text layout, useInput/useFocus keyboard handling, useApp lifecycle, SSE transport. Use when working in web/tui in this repo.
---

# Ink — terminal UI (web/tui)

Ink renders React components to the terminal (Flexbox layout). The TUI is a **peer client of the same server** as the web app — both attach to `agent_api` via HTTP + SSE (plan.md Part C.1).

Docs: https://github.com/vadimdemedes/ink · Context7: `/vadimdemedes/ink` (React-for-CLI).

## Entry + render

```tsx
import { useState } from "react";
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
- `render()` returns an `Instance`: `{ rerender, unmount, waitUntilExit, waitUntilRenderFlush, cleanup, clear }` — keep a handle for programmatic exit. `waitUntilExit()` resolves with the value passed to `exit(value)` (rejects on `exit(error)`).
- `render(<App/>, { alternateScreen: true })` renders in the alternate screen buffer (like vim/htop) and restores the shell on exit — interactive TTY only, ignored in CI/piped stdout.

## Keyboard + focus (multi-pane)

```tsx
import { useInput, usePaste, useFocus, useFocusManager } from "ink";

function PromptFooter() {
  const { isFocused } = useFocus({ id: "prompt", autoFocus: true });
  const [buf, setBuf] = useState("");
  useInput((input, key) => {
    if (key.return) { submit(buf); setBuf(""); }          // Enter
    else if (key.backspace) setBuf((b) => b.slice(0, -1));
    else if (key.escape) cancel();
    else if (!key.ctrl && !key.meta && !key.tab) setBuf((b) => b + input);
  }, { isActive: isFocused });
  usePaste((text) => setBuf((b) => b + text), { isActive: isFocused }); // bracketed paste, one string
  return <Text color={isFocused ? "cyan" : "white"}>{"> " + buf}</Text>;
}
```

- Ink's focus manager already handles **Tab / Shift+Tab** (`focusNext`/`focusPrevious`) and **Escape clears active focus** — don't re-implement Tab cycling; give panes `useFocus({ id })` (message list / prompt / model picker) and gate each `useInput` with `isActive: isFocused`.
- `useFocusManager()` → `{ focus(id), focusNext, focusPrevious, enableFocus, disableFocus, activeId }` to move focus programmatically.
- Hold "input buffer" state in the component (Ink input is event-based; there's no native <input>). `usePaste` delivers pasted text on a separate channel (never forwarded to `useInput` while active).

## App lifecycle / terminal control

```tsx
import { useApp } from "ink";
const { exit, waitUntilRenderFlush, suspendTerminal } = useApp();
```

- `exit()` unmounts the app (`exit(value)` resolves `waitUntilExit()` with `value`, `exit(err)` rejects it); `waitUntilRenderFlush()` for sequencing output with shell.
- `await suspendTerminal(async () => {})` to hand the terminal to a child process (e.g. `$EDITOR`, a pager) then restore + full redraw — useful for `agent run`/reviews. Also callable without a callback (`const s = await suspendTerminal(); … await s.resume()`) or as `await using s = await suspendTerminal()`. Nested suspension throws; no-op handoff in non-TTY mode.

## Streaming the event feed

- Same shared `GET /api/event` SSE stream as the web app (see `typescript` skill `subscribeEvents` + `sse-events` skill). Node's SSE: use `eventsource`/`EventSource` polyfill or `@microsoft/fetch-event-source`; reconnect + resume via `Last-Event-ID`.
- Render `message.partial` deltas into an assistant bubble; `tool.started/completed` into `ToolCallView` state lines; `permission.requested`/`question.asked` into prompt footer.

## Commands (slash) + help (plan.md D tui `commands/`)

- Parse `/name args` in the prompt footer; route to command handlers; inject `/{agent CliArg...}` style prompts. OpenAPI-driven SDK for API calls (see `sdk` skill).

## Gotchas

- **Ink v7 requires Node.js ≥ 22**; current **7.1.x** declares peer `react`/`@types/react` **≥ 19.3.0** (v7.0.0 needed ≥ 19.2) — pin `react` accordingly in `web/tui`. Physical Backspace sets `key.backspace` (not `key.delete`), `key.meta` no longer set on plain Escape.
- Ink is single-threaded; keep `useInput` handlers cheap and non-blocking.
- Long-running: use setState (or `instance.rerender(<App …/>)` from outside React) rather than mutating the rendered tree directly; one `render()` per stdout — call `unmount()` before rendering again.
- Terminal themes: expose color via props/theme copy so `Theme` reuse works (app + TUI share tokens).
- Windows: Ink supports Windows terminals; test `web/tui` on Windows host (this repo's dev machine) and in WSL/CI (linux + darwin).