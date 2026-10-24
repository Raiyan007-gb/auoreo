---
name: uv
description: Manage this Python monorepo with uv — workspaces, deps, lockfile, build, publish to PyPI, tool install. Use for all dependency/env/build commands in this repo.
---

# uv — Python project + package manager

Docs: https://docs.astral.sh/uv · Context7: `/astral-sh/uv` (repo). uv replaces pip, pip-tools, pipx, poetry, pyenv, twine, virtualenv in this repo.

## Everyday commands

```bash
uv init example                       # scaffold project (uv build backend)
uv add ruff                           # add dep to current project (creates .venv, resolves)
uv add --dev pytest pytest-asyncio    # dev dependency
uv run ruff check src/                # run a command inside the project env
uv lock                               # (re)generate uv.lock
uv sync                               # install from lockfile
uv tool install agent-cli             # install a published tool globally (pipx equivalent)
uvx ruff check src/                   # run a tool without installing it
```

## Workspaces (this repo)

`src/*` packages are one workspace. Root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = ["src/agent_schema", "src/agent_core", "src/agent_runtime", "src/agent_gateway",
           "src/agent_memory", "src/agent_tools", "src/agent_mcp", "src/agent_api",
           "src/agent_cli", "src/agent_sdk"]

[tool.uv.sources]
agent-core = { workspace = true }
```

And in `src/agent_api/pyproject.toml`:

```toml
[project]
name = "agent-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["agent-core", "fastapi", "sse-starlette", ...]

[tool.uv.sources]
agent-core = { workspace = true }
```

Benefits: local packages resolve from the workspace (no PyPI round-trip), single lockfile, `uv sync` builds all members. This is how the 10 `agent_*` packages stay in lockstep (plan.md Part J PyPI channel publishes them separately, but dev is one workspace).

## Build + publish (plan.md Part J — PyPI channel)

```bash
uv build                 # → dist/agent_cli-0.1.0-py3-none-any.whl + .tar.gz
uv publish               # upload to PyPI (uses trusted publishing/OIDC when configured)
```

- `uv build` produces both sdist + wheel in `dist/`. CI (`publish.yml`) builds all packages then uploads via trusted publishing (`id-token: write`), no API tokens.
- **Generate PEP 740 attestations before publishing** (uv's official trusted-publishing flow signs your artifacts so PyPI can verify provenance):

```yaml
# publish job (permissions: id-token: write)
- uses: astral-sh/setup-uv@v9           # enable-cache: false in release
- run: uv build
- uses: astral-sh/attest-action@v0      # signs dist/* → PEP 740 attestations
- run: uv publish
```

  Smoke-test the built artifacts first: `uv run --isolated --no-project --with dist/*.whl tests/smoke_test.py` (and the `.tar.gz`) — this is uv's recommended release pattern.
- Version: keep in sync with the git tag (setuptools_scm-style or a `version.py` script that reads the tag — see plan.md Part J version job).
- Test first with `TestPyPI` via `uv publish --publish-url https://test.pypi.org/legacy/` if needed.

## Repo rules

- Pin exact versions in `uv.lock` (reproducible); `uv.lock` is committed.
- Never `pip install` directly in this repo; always `uv`.
- `uv run` is the canonical way to run any Python command in the repo (tests, scripts, CLI).
- For `agent_cli` as a distributable tool: `uv tool install ./src/agent_cli` for local testing mirrors the end-user `uv tool install agent-cli` experience.