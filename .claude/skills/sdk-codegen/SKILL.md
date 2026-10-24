---
name: sdk-codegen
description: Generate typed SDKs (TypeScript + Python) from the FastAPI/OpenAPI contract so API and clients never drift; run the drift guard in CI. Use when regenerating SDKs or editing schema in this repo.
---

# SDK codegen — contract-first (plan.md Part K, Part D `agent_sdk` + `web/sdk`)

`agent_schema` (Pydantic) → `agent_api` FastAPI → **`/openapi.json`** → generated TS + Python clients. If you change a schema, you must regenerate + commit the SDK — CI rejects drift.

## Pipeline

```
agent_schema models → FastAPI routes → app.openapi()/openapi.json
   ├─ openapi-typescript (TS)  → web/sdk/types.gen.ts  + openapi-fetch client
   └─ openapi-python-client    → agent_sdk/* (attrs models + httpx sync/async client)
```

## TS: openapi-typescript + openapi-fetch (or Hey API)

```bash
npx openapi-typescript web/sdk/openapi.json -o web/sdk/types.gen.ts   # or the live http://127.0.0.1:4096/openapi.json
```

- Produces exhaustive, **types-only** output (`paths`, `components['schemas']`) — zero runtime. Pair with the separate tiny `openapi-fetch` package for the typed client (Context7 `/websites/openapi-ts_dev`):
  ```ts
  import createClient from "openapi-fetch";
  import type { paths } from "./types.gen";
  const client = createClient<paths>({ baseUrl });
  const { data, error } = await client.POST("/session/create", { body: { /* typed */ } });  // data on 2XX, error on 4XX/5XX
  ```
- Alternative: Hey API (`npx @hey-api/openapi-ts -i web/sdk/openapi.json -o web/sdk/gen`) emits `types.gen.ts` + `sdk.gen.ts` + `client.gen.ts` with a Fetch client by default (Context7 `/websites/heyapi_dev`). Pick one generator; don't mix.
- Point it at the **live `/openapi.json`** (from a running `agent_api`, or a saved spec committed in `web/sdk/openapi.json`). Committing the spec makes builds hermetic + diffable.

## Python: openapi-python-client

```bash
uvx openapi-python-client generate --path web/sdk/openapi.json --output-path src/agent_sdk --overwrite --meta none
# --overwrite: fails without it if the dir exists · --meta none: generate only the package (no pyproject/README scaffold)
# (use --url http://127.0.0.1:4096/openapi.json instead of --path to read a live server)
```

- Generates `Client`/`AuthenticatedClient` (httpx), **attrs**-based models (`to_dict`/`from_dict` — not Pydantic), and per-endpoint modules under `api/<tag>/` exposing `sync`, `sync_detailed`, `asyncio`, `asyncio_detailed`. Prefer it over hand-rolling `requests`. Context7: `/openapi-generators/openapi-python-client`.
- Mark `src/agent_sdk` generated: add a header + exclude from manual review (regenerate, don't hand-edit).

## Ergonomic layer (hand-written, thin)

Generated types are the floor, not the API you want consumers to type. Add a thin hand-written layer in `web/sdk/src/*.ts` / `agent_sdk/*`:
- `server.ts` — spawn `opencode serve --hostname --port`, watch stdout for "server listening on http://...", return `{client, url}` (opencode `createOpencode` pattern; see `cli` skill).
- `event` helpers — `subscribeEvents(baseUrl, onEvent, onError)` that autos-reconnects and resumes via Last-Event-ID.
- Keep ALL of it thin; do not duplicate generated types.

## CI drift guard (`.github/workflows/sdk.yml`)

```yaml
- run: scripts/gen-sdk.ts        # regenerate into web/sdk + src/agent_sdk
- run: git diff --exit-code -- web/sdk src/agent_sdk   # fail if dirty
```

Intent: if a contributor changed `agent_schema` but didn't regenerate, CI fails with a clear message — regenerate locally (or the workflow regenerates and fails when it would change files).

## Rules

- **Never hand-edit generated files.** Any edit → regenerate from schema (single source of truth).
- Bump `agent_sdk`/`web/sdk` version when the API surface changes (SemVer; plan.md Part J).
- Test generated clients: small e2e against a running `agent_api` (real HTTP/SSE) ensures codegen + server agree (plan.md Part K e2e).