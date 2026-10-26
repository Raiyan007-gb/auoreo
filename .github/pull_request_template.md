## What and why

<!-- One paragraph: the change, the spec lines it implements (e.g. §25.10 L1017), and what is
deliberately left out. -->

## Evidence

| Gate | Command | Result |
|---|---|---|
| lint | `uv run ruff check . && uv run ruff format --check .` | |
| types | `uv run mypy src` (+ `uvx ty check src`, informational) | |
| tests | `./scripts/run_tests.sh` (or `--per-file`) — passed / failed / skipped | |
| contract | `uv run python scripts/check_openapi.py --check` + `sdk.yml` | |
| harness | `uv run auoreo harness validate` + `scripts/verify_tool_catalog.py --check` | |
| web | `cd web && npm run typecheck && npm run lint && npm run build && npm run e2e` | |
| before / after | `uv run python scripts/evidence.py` pair, screenshots or a snapshot diff | |

## Context7 evidence (required when a versioned dependency is touched, spec §26)

| Library | LibraryID@Version | Doc URL | Accessed | Lines justified |
|---|---|---|---|---|
| | | | | |

Every commit that codes against a versioned API carries one trailer per source:

```
Docs: <URL> | <libraryID>@<version> | accessed YYYY-MM-DD
```

No usable Context7 hit after 3 tries: official docs via web fetch only, label the PR
`docs-fallback`.

## Checklist

- [ ] new capability is a folder (agent/tool/skill/workflow/prompt/backend), not engine code
- [ ] files ≤ 500 lines (`api/app.py` ≤ 300), no `elif` chains of 4+
- [ ] schema change → Alembic revision + `SCHEMA_VERSION` bump; API change → regenerated SDKs
- [ ] secrets only from env / keyring / `secrets.db`; nothing secret in fixtures (cassettes redacted)
- [ ] session-replay snapshots refreshed only on purpose (`AUOREO_SNAPSHOT=update`)
