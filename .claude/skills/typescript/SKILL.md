---
name: typescript
description: TypeScript 5.9/6.x conventions for the web/ tree — strict settings, module layout, async patterns, Vite tooling, generated types from OpenAPI. Use when writing web/tui, web/app, web/sdk, web/desktop in this repo.
---

# TypeScript 5.9/6.x — web/ tree conventions

TypeScript is only used for the **clients** (web/tui, web/app, web/desktop, web/sdk) — plan.md Part D. All engine/server code stays Python (`src/`).

Context7: `/websites/typescriptlang` (TS 6.0 release notes) · `/microsoft/typescript/v6.0.2` · `/microsoft/typescript-go` (TS 7).

> **TS 6.0 is stable** (2026-03-23; 5.9.3 was the last 5.x) and is the **last JS-based release** —
> TS 7.0 is the Go native port (`@typescript/native-preview`, `npx tsgo`; the command is `tsc` from
> 7.0 RC on). TS 6.0 default changes: `strict: true`, `module: esnext`, `target` floats to the
> latest ES (currently `es2025`), `noUncheckedSideEffectImports: true`, **`types: []`** (no more
> auto-including every `@types/*`), and **`rootDir` = the tsconfig's directory**. Deprecated in 6.0
> and removed in 7.0: `baseUrl` (put the prefix in `paths`), `moduleResolution: node`/`node10`
> (use `bundler` or `nodenext`), `classic`, `target: ES5`, `module: AMD/UMD/System`, `outFile`,
> `downlevelIteration`, `esModuleInterop: false`. The tsconfig below is TS 6/7-safe — keep it
> explicit for clarity. `tsc --init` (since 5.9) also recommends `verbatimModuleSyntax`,
> `isolatedModules`, `moduleDetection: "force"`, `exactOptionalPropertyTypes`.

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
- Because `types` now defaults to `[]`, each package lists its globals explicitly: `web/tui` → `"types": ["node"]` (+ `npm i -D @types/node`), `web/app` → `"types": ["vite/client"]` (for `import.meta.env`). "Cannot find name 'process'" after upgrading means a missing `types` entry.
- No `any` in committed code; if unavoidable, scope it with a comment.

## Layout

- `web/app` → Vite React SPA (browser). `web/tui` → Ink React-for-CLI (terminal). `web/desktop` → Electron shell (later). `web/sdk` → published TS SDK (generated).
- Shared client helpers (SSE transport, API client wrapper) live in `web/sdk` or a small `web/shared` so TUI and app reuse them.

## Async + events (matches SSE backend)

```ts
// web/app/src/lib/sse.ts — subscribe to a single /api/event stream, replay on reconnect
export function subscribeEvents(baseUrl: URL, lastSeq: number, onEvent: (e: EventPayload) => void) {
  const es = new EventSource(new URL(`/api/event?last_seq=${lastSeq}`, baseUrl)); // avoids "//api" from a trailing-slash base
  es.addEventListener("message", (ev) => { try { onEvent(JSON.parse(ev.data)) } catch {} });
  return () => es.close();
}
```

- The browser `EventSource` auto-reconnects and re-sends `Last-Event-ID`; the server replays from the last `seq` — the single shared stream is how TUI + app stay in sync (plan.md Part C.1 / `sse-events` skill).
- Use `readonly` modifiers on interfaces, `satisfies` for literal shapes, and tagged unions (`type: "..."` with `switch` narrowing) for event/message payloads consumed from `agent_schema`-generated types.

## Tooling

- `npm`/`pnpm` workspaces at repo root for `web/*`; `tsc --noEmit` is the typecheck CI gate.
- `vite build` produces `dist/` served by `agent_api` on the same port (plan.md Part C.1; see `fastapi` skill static section).
- Env vars: only `VITE_`-prefixed vars are exposed to client code (`import.meta.env.VITE_*`; type them in `vite-env.d.ts` via `interface ImportMetaEnv`); keep secrets server-side only — never bundle API keys into the web bundle.

## Codegen (no drift)

- Do NOT hand-write types for the API. Run `openapi-ts` / `openapi-typescript` off `agent_api`'s `/openapi.json` → `web/sdk/types.gen.ts` (see `sdk` skill). If you see duplicate/divergent types, regenerate — don't patch by hand.