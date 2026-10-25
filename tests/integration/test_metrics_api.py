"""G5c: `/metrics` Prometheus fallback (spec §20.1): works with OTel off, bounded labels, auth."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core import metrics_text
from auoreo.core import observability as obs
from auoreo.core.services import Services
from auoreo.core.settings import Settings

PW = "metrics-test-password"


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    obs.reset()
    metrics_text.reset()
    yield
    metrics_text.reset()
    obs.reset()


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    settings.server_password = PW
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


def _samples(text: str) -> dict[str, float]:
    return {
        line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if line and not line.startswith("#")
    }


def test_render_exposition_format() -> None:
    metrics_text.add("tokens_total", 5, {"type": "prompt", "model": 'a"b'})
    metrics_text.add("tokens_total", 2.5, {"model": 'a"b', "type": "prompt"})
    text = metrics_text.render({"tokens_total": "counter", "active_sse_connections": "updown"})
    assert "# TYPE tokens_total counter" in text and "# TYPE active_sse_connections gauge" in text
    assert 'tokens_total{model="a\\"b",type="prompt"} 7.5' in text
    assert "active_sse_connections 0" in text and text.endswith("\n")


def test_series_per_metric_are_capped() -> None:
    for i in range(metrics_text.MAX_SERIES + 25):
        obs.count("tokens_total", 1, model=f"m{i}", type="prompt")
    text = metrics_text.render({"tokens_total": "counter"})
    rows = [line for line in text.splitlines() if line.startswith("tokens_total{")]
    assert len(rows) == metrics_text.MAX_SERIES + 1
    assert metrics_text.value("tokens_total", overflow="true") == 25


def test_count_mirrors_allowlisted_metrics_only_with_otel_off(home: object) -> None:
    obs.count("run_completed_total", status="completed", session_id="ses_secret")
    obs.count("not_a_metric_total")
    obs.record_usage("gpt-x", 10, 3, 0.25)
    assert not obs.enabled()
    assert metrics_text.value("run_completed_total", status="completed") == 1
    assert metrics_text.value("tokens_total", model="gpt-x", type="prompt") == 10
    assert metrics_text.value("cost_usd_total", model="gpt-x") == 0.25
    assert "ses_secret" not in metrics_text.render({"run_completed_total": "counter"})


async def test_metrics_endpoint_requires_auth_and_reports_a_run(
    client: httpx.AsyncClient, services: Services
) -> None:
    assert (await client.get("/metrics")).status_code == 401
    auth = {"authorization": f"Bearer {PW}"}
    r = await client.post("/api/chat", json={"message": "hello"}, headers=auth)
    assert r.status_code == 202
    await services.coordinator.wait(r.json()["session_id"], 10)
    await services.coordinator.background_idle(10)
    r = await client.get("/metrics", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    samples = _samples(r.text)
    assert samples['run_completed_total{status="completed"}'] >= 1
    assert samples["events_published_total"] >= 3
    for name in obs.METRICS:
        assert f"# TYPE {name} " in r.text
