---
name: typescript
description: TypeScript 5.9/6.x conventions for the web/ tree — strict settings, module layout, async patterns, Vite tooling, generated types from OpenAPI. Use when writing web/tui, web/app, web/sdk, web/desktop in this repo.
---

# TypeScript 5.9/6.x — web/ tree conventions

TypeScript is only used for the **clients** (web/tui, web/app, web/desktop, web/sdk) — plan.md Part D. All engine/server code stays Python (`src/`).

> **TS 6.0 is stable** (2026-03-23; 5.9.3 was the last 5.x). TS 6.0 changes defaults: `strict`
> now defaults `true`, `moduleResolution` defaults to `bundler`. The tsconfig below is still
> valid (explicit flags are now partially redundant) — keep it explicit for clarity. `tsc --init`
> under 6.0 additionally recommends `verbatimModuleSyntax`, `isolatedModules`,
> `moduleDetection: "force"`, `noUncheckedSideEffectImports`, `exactOptionalPropertyTypes`.

## Strict config (tsconfig.base.json)

```json
{
  "compilerOptions": {
    "strict": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "noUncheckedIndexedAccess": true,
    "forceConsistentCasingInFileNames": true
  }
}
```

- `noUncheckedIndexedAccess` forces handling of `arr[i]` returns `T | undefined` — matches the codebase standard (no silent `undefined` access).
- No `any` in committed code; if unavoidable, scope it with a comment.

## Layout

- `web/app` → Vite React SPA (browser). `web/tui` → Ink React-for-CLI (terminal). `web/desktop` → Electron shell (later). `web/sdk` → published TS SDK (generated).
- Shared client helpers (SSE transport, API client wrapper) live in `web/sdk` or a small `web/shared` so TUI and app reuse them.

## Async + events (matches SSE backend)

```ts
// web/app/src/lib/sse.ts — subscribe to a single /api/event stream, replay on reconnect
export function subscribeEvents(baseUrl: URL, lastSeq: number, onEvent: (e: EventPayload) => void) {
  const es = new EventSource(`${baseUrl}/api/event?last_seq=${lastSeq}`);
  es.addEventListener("message", (ev) => { try { onEvent(JSON.parse(ev.data)) } catch {} });
  return () => es.close();
}
```

- The browser `EventSource` auto-reconnects and re-sends `Last-Event-ID`; the server replays from the last `seq` — the single shared stream is how TUI + app stay in sync (plan.md Part C.1 / `sse-events` skill).
- Use `readonly` modifiers on interfaces, `satisfies` for literal shapes, and tagged unions (`type: "..."` with `switch` narrowing) for event/message payloads consumed from `agent_schema`-generated types.

## Tooling

- `npm`/`pnpm` workspaces at repo root for `web/*`; `tsc --noEmit` is the typecheck CI gate.
- `vite build` produces `dist/` served by `agent_api` on the same port (plan.md Part C.1; see `fastapi` skill static section).
- Env vars: prefix client-visible config with `VITE_` when using Vite define/env; keep secrets server-side only — never bundle API keys into the web bundle.

## Codegen (no drift)

- Do NOT hand-write types for the API. Run `openapi-ts` / `openapi-typescript` off `agent_api`'s `/openapi.json` → `web/sdk/types.gen.ts` (see `sdk` skill). If you see duplicate/divergent types, regenerate — don't patch by hand.