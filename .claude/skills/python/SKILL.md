---
name: python
description: Python 3.12+ conventions for this repo — modern typing, async/await, packaging layout, tooling (uv/ruff/mypy/pytest). Use when writing any agent_* Python package in this repo.
---

# Python 3.12+ — project conventions

This repo is a Python 3.12+ monorepo under `src/` with `agent_*` package names (plan.md Part D). Follow these conventions so the codebase stays uniform.

Context7: `/astral-sh/ruff` (lint/format) + `/astral-sh/uv` (running tools) + `/pytest-dev/pytest-asyncio`.

## Modern Python idioms (Python 3.12+)

- Use `X | None` (PEP 604), `list[X]`/`dict[str, X]` (PEP 585), `typing.Protocol`, `dataclass`/Pydantic models, and match statements. Use PEP 695 syntax (3.12+) for generics and aliases: `class Store[T]: ...`, `def first[T](xs: list[T]) -> T`, and `type EventId = str` instead of `TypeVar`/`TypeAlias` boilerplate (ruff `UP040`/`UP046`/`UP047` flag the old forms). No `typing.Optional`, `typing.List`, or `from __future__ import annotations` where the interpreter already supports it.
- Prefer `async def` + `await` for I/O (FastAPI handlers, SSE generators, gateway calls). The whole server is async — never block the event loop with sync DB/HTTP calls inside handlers.
- Use `asyncio.Queue` for per-client event fan-out (plan.md Part G; see `fastapi` skill SSE section).
- Return early, keep functions focused; use `Enum`/`Literal` for tag-like fields (event types, ToolState status).

## Typing + linting (plan.md Part K)

```bash
uvx ruff check src/          # lint (fast, replaces flake8+isort+pyupgrade once "I"/"UP" are in [tool.ruff.lint] select)
uvx ruff format --check src/ # formatting
uv run mypy src/             # strict-ish type checking (needs the project env → uv run, not uvx)
```

- Every public API uses **type annotations**; mypy is a CI gate. `# type: ignore` needs a comment explaining why.
- Prefer explicit return types on exported functions.
- Ruff can run isolated via `uvx` and infers `target-version` from `requires-python`. mypy (and pytest) have to see the installed `src/` packages and their deps, so run them with `uv run` (uv docs: use `uv run`, not `uvx`, for tools that need the project installed).

## Async testing

pytest + pytest-asyncio (`asyncio_mode = "auto"` in `pyproject.toml`):

```python
# tests/unit/test_gateway.py
import pytest

async def test_stream_chat_mock(mock_gateway):   # auto-marked async by pytest-asyncio
    chunks = [c async for c in mock_gateway.stream_chat("openai/gpt-4o", [{"role": "user", "content": "hi"}])]
    assert "".join(chunks)
```

- Integration tests use SQLite + a fake LLM (never real API keys in CI) and real HTTP/SSE against `agent_api` (plan.md Part K testing pyramid).
- Fixtures in `tests/conftest.py`; avoid mocks when a real implementation is cheap (opencode rule: "avoid mocks as much as possible").

## Packaging rules

- Each package (`agent_schema`, `agent_core`, `agent_runtime`, `agent_gateway`, `agent_memory`, `agent_tools`, `agent_mcp`, `agent_api`, `agent_cli`, `agent_sdk`) is a `uv`-managed package with its own `pyproject.toml`, but they **reference each other as workspace members**, not PyPI versions (see `uv` skill).
- Keep LangGraph/LiteLLM/SQLAlchemy imports confined to their owning package (`agent_runtime`, `agent_gateway`, `agent_core/database.py`) — plan.md Part C invariant. Cross-package, depend only on `agent_schema` types.
- Never import secrets from code; read from env/keyring via `agent_core/auth` or Pydantic settings.

## Gotchas

- Windows dev machine: use `uv run` / WSL-friendly paths; SQLite via `sqlite+pysqlite:///...` works cross-platform. Do not rely on POSIX-only tools in tests.
- Event/type drift: when you change a model in `agent_schema`, regenerate the SDK and run `sdk.yml` guard (plan.md Part K).