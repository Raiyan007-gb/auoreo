---
name: react-vite
description: Build the web UI (web/app) with React 19 + Vite 8 + TypeScript — SPA structure, hooks for SSE/state, contexts, routing, build output served by the FastAPI server. Use when working in web/app in this repo.
---

# React + Vite — web/app SPA

Vite scaffolds + builds the React SPA; the built `dist/` is served statically by `agent_api` on the same port (plan.md Part C.1 "one port, all clients").

## Scaffold

```bash
npm create vite@latest web-app -- --template react-ts
# ⚠ scaffolds Vite 8 (Rolldown bundler) — requires Node ≥ 20.19 / 22.12
# configure base/build.outDir → ../app/dist, then agent_api mounts that dir (fastapi skill)
```

Dev: `npm run dev` (Vite dev server, HMR). Prod: `npm run build` → `vite build` outputs to `dist/`.

```json
{ "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" } }
```

## Core React patterns (React 19)

Entry + state (recommended modern shape):

```tsx
import { createRoot } from "react-dom/client";
import { useState, useEffect } from "react";

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  useEffect(() => {
    let cancelled = false;
    listSessions().then((s) => { if (!cancelled) setSessions(s); });
    return () => { cancelled = true; };
  }, []);
  return <SessionList sessions={sessions} />;
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
```

- `useEffect` cleanup: clear timers, abort fetches, close `EventSource` (see `typescript` skill `subscribeEvents`) — prevents leaks on unmount.
- Custom hooks isolate server access: `useSessions`, `useMessages(sessionId)`, `useEvents(onEvent)` (plan.md Part D app `hooks/`). Presentational components just consume them.
- Context: one `ServerContext` for base URL + client, one `EventsContext` distributing the shared SSE stream to views (plan.md D `web/app/context/`). Keep heavy state in hooks, not contexts.

## Pages (plan.md D app `pages/`)

Home (sessions list) · Session (thread) · SessionDetail · Settings · Providers · Models · Tools · Permissions · Agents · Skills · Commands · Usage · Auth. Route with `react-router` (`<BrowserRouter>`/`createBrowserRouter`); deep-link to `/session/:id`.

## ToolCall card + token badge

Render tool state (pending → running → completed | error) from the event-driven message model (plan.md Part G). Use the `ToolState` tagged union generated from `agent_schema` and switch on `status`:

```tsx
function ToolCallView({ state }: { state: ToolState }) {
  switch (state.status) {
    case "pending": return <Spinner label={state.input} />;
    case "running": return <Spinner label="running…" />;
    case "completed": return <CodeBlock>{String(state.result)}</CodeBlock>;
    case "error": return <ErrorBox>{state.error.message}</ErrorBox>;
  }
}
```

## Styling + a11y

- Tailwind 4 (config `@import "tailwindcss";` in CSS) or vanilla CSS modules — keep consistent with `i18n/` and theme tokens (plan.md D app `styles/`).
- A11y basics: semantic elements, `aria-label` on icons/spinners, keyboard focus order; light/dark theme via CSS vars (opencode TUI/web both honor themes).

## Build + serve contract

- Single `dist/` built on `npm run build`; CI builds it before the release image.
- The SPA talks only to the same-origin API (relative `/api/*`) so no CORS needed when served by the same process; use CORS config only for remote app frontends (`server --cors`).