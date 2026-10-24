---
name: pytest
description: Write and run this repo's tests with pytest + pytest-asyncio — fixtures, parametrize, fake-LLM patterns, httpx/SSE integration tests. Use when adding tests in this repo.
---

# pytest — testing (plan.md Part K testing pyramid)

Docs: https://docs.pytest.org · Context7: `/pytest-dev/pytest` (85) + `/pytest-dev/pytest-asyncio` (85) + `/colin-b/pytest_httpx` (90) + `/encode/httpx` (ASGITransport).

## Config (pyproject.toml)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"                       # auto-apply @pytest.mark.asyncio to async tests
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

- `asyncio_default_fixture_loop_scope` is set explicitly because pytest-asyncio will change its unset default (currently "same as the fixture's scope") to `function`. `asyncio_default_test_loop_scope` is the matching knob for tests (default `function`).
- pytest 9 also accepts a native-TOML `[tool.pytest]` table (`addopts = ["-ra", "-q"]` as a real list). `[tool.pytest.ini_options]` is still supported and is what pytest-asyncio documents. Don't use both tables at once.

## Basics

```python
# tests/unit/test_tool_def.py
import pytest

def test_tool_schema_validation():
    assert ToolDefinition.parse({"name": "web_search", ...}) == ...
```

## Async tests (auto mode → no decorator needed)

```python
async def test_gateway_streams(mock_gateway):
    chunks = [c async for c in mock_gateway.stream_chat("openai/gpt-4o", [{"role": "user", "content": "hi"}])]
    assert any(chunks)
```

## Fixtures

```python
# tests/conftest.py
import pytest

@pytest.fixture
def tmp_db(tmp_path):
    from agent_core.database import create_engine_for
    engine = create_engine_for(f"sqlite+pysqlite:///{tmp_path}/test.db")
    yield engine
    engine.dispose()

@pytest.fixture
async def fake_llm():          # async fixture (auto mode)
    return FakeLitellmGateway()   # inject via agent_gateway interface (no real keys)
```

- For a broader-scoped async fixture (e.g. one app per module), use `@pytest_asyncio.fixture(scope="module", loop_scope="module")`. `loop_scope` must be at least as wide as `scope`, and tests that use it need `@pytest.mark.asyncio(loop_scope="module")`. Don't override the old `event_loop` fixture; pytest-asyncio removed it in favor of `loop_scope`.

## Fake-LLM / mock strategies (avoid mocks where an implementation is cheap)

- Build a `FakeLitellmGateway` implementing the `agent_gateway` interface → deterministic responses/streams. This is the single most important test seam (plan.md Part K: "unit no network, integration SQLite + fake LLM").
- For HTTP-level tests use `pytest-httpx`: its `httpx_mock` fixture registers responses with `httpx_mock.add_response(...)`. Use it instead of patching `requests`. By default a registered response that is never requested fails the test at teardown. Opt out per test with `@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)` or per response with `is_optional=True`.
- Only mock the seam at package boundaries; don't mock internals.

## Integration / SSE tests (real HTTP)

Use FastAPI's `TestClient`/`httpx.AsyncClient` with `ASGITransport` for router + SSE:

```python
from httpx import AsyncClient, ASGITransport

async def test_sse_streams_events(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/event") as resp:
            lines = [line async for line in resp.aiter_lines()]
        assert any("session.started" in ... for ... in lines)
```

> **Note:** `ASGITransport` does NOT run the app's lifespan (startup/shutdown) events. If `app`
> has startup work (e.g. engine init, event-service boot), wrap it with `asgi-lifespan`'s
> `LifespanManager` around the client so fixtures relying on startup state behave like production.


- Verify headers (`text/event-stream`, `Cache-Control`, `X-Accel-Buffering`).
- Replay test: call with `Last-Event-ID` header, assert continuation from `seq+1`.

## Running

```bash
uv run pytest                      # all
uv run pytest tests/unit/test_tool_def.py::test_tool_schema_validation   # single test
uv run pytest tests/integration -k "sse" -vv                            # filtered
```

- CI runs the pyramid: unit → integration (SQLite + fake LLM) → e2e (mock LLM server, real HTTP/SSE) → release acceptance (install each channel + clean run, plan.md Part J).
- Keep tests hermetic: no live LLM/network; temp SQLite db; no reliance on CWD.