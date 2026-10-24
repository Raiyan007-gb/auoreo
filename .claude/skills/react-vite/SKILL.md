---
name: react-vite
description: Build the web UI (web/app) with React 19 + Vite 8 + TypeScript — SPA structure, hooks for SSE/state, contexts, routing, build output served by the FastAPI server. Use when working in web/app in this repo.
---

# React + Vite — web/app SPA

Vite scaffolds + builds the React SPA; the built `dist/` is served statically by `agent_api` on the same port (plan.md Part C.1 "one port, all clients").

Context7: `/vitejs/vite/v8.0.10` · `/websites/react_dev_reference_react` · `/remix-run/react-router` · `/websites/tailwindcss`.

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

```ts
// vite.config.ts — Vite 8: Rolldown bundler, Oxc transforms (esbuild options deprecated)
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";      // v6: Oxc-based Fast Refresh (no Babel)
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // dev only: forward same-origin /api/* (incl. the SSE /api/event stream) to agent_api
    proxy: { "/api": { target: "http://127.0.0.1:<agent_api port>", changeOrigin: true } },
  },
  build: { outDir: "dist" },
});
```

- React Compiler is opt-in with plugin-react v6: `reactCompilerPreset` + `@rolldown/plugin-babel`. Use `build.rolldownOptions` (not `rollupOptions`) for advanced bundling.

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
- In `useEvents(onEvent)`, wrap the callback with `useEffectEvent` (stable since React 19.2) so the `EventSource` subscription isn't torn down/re-opened whenever `onEvent` changes identity: `const handle = useEffectEvent(onEvent); useEffect(() => subscribeEvents(url, seq, (e) => handle(e)), [url]);`.
- Context: one `ServerContext` for base URL + client, one `EventsContext` distributing the shared SSE stream to views (plan.md D `web/app/context/`). Keep heavy state in hooks, not contexts. React 19: render `<ServerContext value={…}>` directly (`.Provider` is legacy); read with `use(ServerContext)` or `useContext`.

## Pages (plan.md D app `pages/`)

Home (sessions list) · Session (thread) · SessionDetail · Settings · Providers · Models · Tools · Permissions · Agents · Skills · Commands · Usage · Auth. Route with `react-router` v7 (single package — `react-router-dom` is only a legacy re-export): `createBrowserRouter([...])` from `"react-router"`, created once outside the tree, rendered via `<RouterProvider router={router} />` from `"react-router/dom"` (data mode: loaders/actions); `<BrowserRouter>` (declarative mode) also works. Deep-link to `/session/:id` — `agent_api` must fall back to `index.html` for unknown non-`/api` paths.

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

- Tailwind 4 (`npm i tailwindcss @tailwindcss/vite`, add `tailwindcss()` to Vite plugins, then `@import "tailwindcss";` in CSS — no `tailwind.config.js`/PostCSS needed) or vanilla CSS modules — keep consistent with `i18n/` and theme tokens (plan.md D app `styles/`).
- A11y basics: semantic elements, `aria-label` on icons/spinners, keyboard focus order; light/dark theme via CSS vars (opencode TUI/web both honor themes).

## Build + serve contract

- Single `dist/` built on `npm run build`; CI builds it before the release image.
- The SPA talks only to the same-origin API (relative `/api/*`) so no CORS needed when served by the same process; use CORS config only for remote app frontends (`server --cors`).