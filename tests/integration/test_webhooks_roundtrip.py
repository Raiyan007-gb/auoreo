"""M5 exit criterion: a GitHub webhook runs the agent and comments on the pull request, end to
end over ASGI with the real outbox worker, the fake gateway and a mocked GitHub API."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock

from auoreo.api.app import create_app
from auoreo.automation.deliveries import Outbox
from auoreo.automation.webhooks import sign
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from tests.conftest import write

SECRET = "whsec_roundtrip"
TOKEN = "ghp_roundtrip_token_0000"
ROUTE = """name: ghc
description: comment on opened pull requests
provider: github
secret: "secret:webhook/ghc"
events: [pull_request]
filters:
  - {field: action, equals: opened}
prompt: "Review PR {{event.pull_request.number}}: {{event.pull_request.title}}"
deliver: [github_comment]
"""


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(
        home=home, gateway="fake", approval_timeout_s=5, automation_start_delay_s=0, automation_poll_s=0.2
    )


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    write(workdir / ".auoreo" / "webhooks" / "ghc.yaml", ROUTE)
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    svc.secrets.set("webhook/ghc", SECRET)
    svc.secrets.set("github/token", TOKEN)
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_github_webhook_runs_the_agent_and_comments_on_the_pull_request(
    client: httpx.AsyncClient, services: Services, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/octo/repo/issues/42/comments",
        status_code=201,
        json={"id": 7, "html_url": "https://github.com/octo/repo/pull/42#issuecomment-7"},
    )
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {"title": "Add retries", "number": 42, "draft": False},
        "repository": {"full_name": "octo/repo"},
    }
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sign(SECRET, body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": str(uuid.uuid4()),
    }
    r = await client.post("/hooks/ghc", content=body, headers=headers)
    assert r.status_code == 202, r.text

    row = await Outbox(services).wait_terminal(r.json()["delivery_id"], 15)
    assert row is not None and row.status == "delivered", row and row.error
    assert row.result["provider"] == "github_comment"
    assert row.payload["delivery_target"] == "octo/repo#42"

    posted = httpx_mock.get_requests()
    assert len(posted) == 1 and posted[0].headers["Authorization"] == f"Bearer {TOKEN}"
    comment = json.loads(posted[0].content)["body"]
    assert comment == row.result["text"] and comment.startswith("echo: ")
    assert "Review PR 42: Add retries" in comment
