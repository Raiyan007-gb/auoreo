"""G1a webhook hardening: generic V2 + skew, rotation (#prev), gitlab, svix, challenges,
deliver_only, the GitHub alias, 403 on a missing secret and strict startup."""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.automation.deliveries import Outbox
from auoreo.automation.webhook_verify import (
    doctor_rows,
    rotate,
    sign_svix,
    sign_v2,
    signed_headers,
    timestamp_ok,
    verify_svix,
)
from auoreo.core.db import AuditRow, DeliveryRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from tests.conftest import write

SECRET = "generic-secret-0123456789"
SVIX_SECRET = "whsec_" + base64.b64encode(b"k" * 32).decode()
ROUTES = {
    "gen": 'name: gen\ndescription: generic\nprovider: generic\nsecret: "secret:webhook/gen"\nprompt: "g"\n',
    "gl": (
        'name: gl\ndescription: gitlab\nprovider: gitlab\nsecret: "secret:webhook/gl"\n'
        'events: ["Merge Request Hook"]\nprompt: "mr {{event.object_attributes.title}}"\n'
    ),
    "sv": (
        'name: sv\ndescription: svix\nprovider: svix\nsecret: "secret:webhook/sv"\n'
        'prompt: "s {{event.type}}"\n'
    ),
    "gh": 'name: gh\ndescription: github\nprovider: github\nsecret: "secret:webhook/gh"\nprompt: "x"\n',
    "slack": (
        'name: slack\ndescription: challenge\nprovider: generic\nsecret: "secret:webhook/slack"\n'
        'prompt: "x"\nchallenge: true\n'
    ),
    "notify": (
        'name: notify\ndescription: deliver only\nprovider: generic\nsecret: "secret:webhook/notify"\n'
        'prompt: "build {{event.status}}"\ndeliver: [log]\ndeliver_only: true\n'
    ),
    "broken": (
        "name: broken\ndescription: deliver only, bad target\nprovider: generic\n"
        'secret: "secret:webhook/broken"\nprompt: "x"\ndeliver: [nosuchprovider]\ndeliver_only: true\n'
    ),
    "nosecret": (
        'name: nosecret\ndescription: x\nprovider: generic\nsecret: "secret:webhook/nosecret"\nprompt: "x"\n'
    ),
}


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", automation_start_delay_s=60)


def _write_routes(workdir: Path) -> None:
    for name, text in ROUTES.items():
        write(workdir / ".auoreo" / "webhooks" / f"{name}.yaml", text)


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    _write_routes(workdir)
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    for name in ("gen", "gl", "gh", "slack", "notify", "broken"):
        svc.secrets.set(f"webhook/{name}", SECRET)
    svc.secrets.set("webhook/sv", SVIX_SECRET)
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


def gen_headers(
    body: bytes, *, ts: int | None = None, secret: str = SECRET, did: str | None = None
) -> dict[str, str]:
    t = int(time.time()) if ts is None else ts
    return {
        "Content-Type": "application/json",
        "X-Signature-256": sign_v2(secret, body, t),
        "X-Timestamp": str(t),
        "X-Delivery-Id": did or str(uuid.uuid4()),
    }


async def audit_actions(svc: Services) -> list[str]:
    async with svc.db.session() as s:
        return [a.action for a in (await s.execute(select(AuditRow).order_by(AuditRow.seq))).scalars()]


# ------------------------------------------------------------------ pure helpers
def test_timestamp_parsing_is_strict() -> None:
    now = 1_700_000_000.0
    assert timestamp_ok("1700000000", now) and timestamp_ok("1700000300", now)
    for bad in (None, "", "abc", "1.7e9", "-5", "1700000301", "1699999699"):
        assert not timestamp_ok(bad, now), bad


def test_svix_accepts_any_v1_entry_and_rejects_others() -> None:
    body, ts, mid = b'{"type":"x"}', int(time.time()), "msg_1"
    good = sign_svix(SVIX_SECRET, mid, ts, body)
    headers = {"svix-id": mid, "svix-timestamp": str(ts), "svix-signature": f"v1,AAAA {good} v2,zzz"}
    assert verify_svix(SVIX_SECRET, headers, body, time.time()) is None
    assert verify_svix(SVIX_SECRET, headers | {"svix-signature": "v1,AAAA"}, body, time.time())
    assert verify_svix(SVIX_SECRET, headers | {"svix-timestamp": str(ts - 301)}, body, time.time())
    assert verify_svix(SVIX_SECRET, headers | {"svix-id": "other"}, body, time.time())


def test_rotate_moves_old_secret_to_prev() -> None:
    store: dict[str, str] = {"webhook/r": "old"}

    class Store:
        get = store.get

        @staticmethod
        def set(k: str, v: str) -> None:
            store[k] = v

    fresh = rotate(Store(), "webhook/r", "generic", now=1000)
    assert store == {"webhook/r": fresh, "webhook/r#prev": "old", "webhook/r#rotated_at": "1000"}
    assert rotate(Store(), "webhook/r", "svix").startswith("whsec_")


# ------------------------------------------------------------------ generic V2
async def test_generic_v2_accept_replay_and_skew(client: httpx.AsyncClient) -> None:
    body = json.dumps({"event": "deploy"}).encode()
    h = gen_headers(body, did="dep-1")
    assert (await client.post("/hooks/gen", content=body, headers=h)).status_code == 202
    replay = await client.post("/hooks/gen", content=body, headers=h)  # captured request re-sent
    assert replay.status_code == 200 and replay.json() == {"status": "duplicate"}
    now = int(time.time())
    for skew in (-301, 301):
        r = await client.post("/hooks/gen", content=body, headers=gen_headers(body, ts=now + skew))
        assert r.status_code == 401 and "timestamp" in r.json()["detail"]
    forged = gen_headers(body) | {"X-Timestamp": str(now + 5)}  # signature bound to the timestamp
    assert (await client.post("/hooks/gen", content=body, headers=forged)).status_code == 401
    unparseable = gen_headers(body) | {"X-Timestamp": "soon"}
    assert (await client.post("/hooks/gen", content=body, headers=unparseable)).status_code == 401


async def test_missing_secret_is_403_and_startup_warns(client: httpx.AsyncClient, services: Services) -> None:
    body = b"{}"
    r = await client.post("/hooks/nosecret", content=body, headers=gen_headers(body))
    assert r.status_code == 403
    assert "webhook.secret.missing" in await audit_actions(services)  # startup warning row
    rows = doctor_rows(services.settings, services)
    assert [(c, lvl) for c, lvl, _, _ in rows] == [("webhook:nosecret", "error")]


async def test_strict_secrets_refuse_to_start(settings: Settings, services: Services) -> None:
    settings.webhooks.strict_secrets = True
    app = create_app(settings, services=services)
    with pytest.raises(RuntimeError, match="nosecret"):
        async with LifespanManager(app):
            pass


# ------------------------------------------------------------------ rotation
async def test_rotation_prev_accepted_within_grace(client: httpx.AsyncClient, services: Services) -> None:
    old = SECRET
    fresh = rotate(services.secrets, "webhook/gen", "generic")
    body = json.dumps({"event": "deploy", "n": 1}).encode()
    assert (
        await client.post("/hooks/gen", content=body, headers=gen_headers(body, secret=fresh))
    ).status_code == 202
    body2 = json.dumps({"event": "deploy", "n": 2}).encode()
    assert (
        await client.post("/hooks/gen", content=body2, headers=gen_headers(body2, secret=old))
    ).status_code == 202
    assert (await audit_actions(services)).count("webhook.secret.rotated_used") == 1
    services.secrets.set("webhook/gen#rotated_at", str(int(time.time()) - 86_401))  # grace over
    body3 = json.dumps({"event": "deploy", "n": 3}).encode()
    assert (
        await client.post("/hooks/gen", content=body3, headers=gen_headers(body3, secret=old))
    ).status_code == 401


# ------------------------------------------------------------------ gitlab + svix
async def test_gitlab_token_and_event(client: httpx.AsyncClient, services: Services) -> None:
    body = json.dumps({"object_kind": "merge_request", "object_attributes": {"title": "Fix"}}).encode()
    h = signed_headers("gitlab", SECRET, body, event="Merge Request Hook", delivery_id="gl-1")
    r = await client.post("/hooks/gl", content=body, headers=h)
    assert r.status_code == 202 and r.json()["event"] == "Merge Request Hook"
    row = await Outbox(services).get(r.json()["delivery_id"])
    assert row is not None and row.external_id == "gl-1" and "mr Fix" in row.payload["prompt"]
    bad = h | {"X-Gitlab-Token": SECRET + "x", "webhook-id": "gl-2"}
    assert (await client.post("/hooks/gl", content=body, headers=bad)).status_code == 401
    push = h | {"X-Gitlab-Event": "Push Hook", "webhook-id": "gl-3"}
    assert (await client.post("/hooks/gl", content=body, headers=push)).json() == {"status": "ignored"}


async def test_svix_route(client: httpx.AsyncClient) -> None:
    body = json.dumps({"type": "invoice.paid"}).encode()
    h = signed_headers("svix", SVIX_SECRET, body, event="", delivery_id="msg_abc")
    r = await client.post("/hooks/sv", content=body, headers=h)
    assert r.status_code == 202 and r.json()["event"] == "invoice.paid"
    assert (await client.post("/hooks/sv", content=body, headers=h)).json() == {"status": "duplicate"}
    stale = signed_headers("svix", SVIX_SECRET, body, event="", delivery_id="msg_old", now=time.time() - 600)
    assert (await client.post("/hooks/sv", content=body, headers=stale)).status_code == 401


# ------------------------------------------------------------------ challenge / alias / deliver_only
async def test_challenges_only_on_opted_in_routes(client: httpx.AsyncClient) -> None:
    r = await client.get("/hooks/slack", params={"challenge": "abc123"})
    assert r.status_code == 200 and r.text == "abc123" and r.headers["content-type"].startswith("text/plain")
    r = await client.get("/hooks/gen", params={"challenge": "abc123"})
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert (await client.get("/hooks/nope", params={"challenge": "x"})).status_code == 404
    slack = json.dumps({"type": "url_verification", "challenge": "c-9"}).encode()
    r = await client.post("/hooks/slack", content=slack, headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {"challenge": "c-9"}
    r = await client.post("/hooks/gen", content=slack, headers={"Content-Type": "application/json"})
    assert r.status_code == 401  # not opted in: the challenge is just an unsigned body


async def test_github_alias(client: httpx.AsyncClient) -> None:
    body = b'{"zen": "hi"}'
    h = signed_headers("github", SECRET, body, event="ping", delivery_id=str(uuid.uuid4()))
    assert (await client.post("/hooks/github/gh", content=body, headers=h)).status_code == 202
    h2 = gen_headers(body)
    r = await client.post("/hooks/github/gen", content=body, headers=h2)
    assert r.status_code == 404 and r.json() == {"detail": "not found"}  # same body as unknown route


async def test_deliver_only_routes(client: httpx.AsyncClient, services: Services, home: Path) -> None:
    body = json.dumps({"event": "build", "status": "green"}).encode()
    r = await client.post("/hooks/notify", content=body, headers=gen_headers(body, did="b-1"))
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    line = json.loads((home / "deliveries.log").read_text().splitlines()[-1])
    assert line["text"] == "build green" and line["source"] == "notify"
    row = await Outbox(services).get(r.json()["delivery_id"])
    assert row is not None and row.status == "delivered" and row.session_id is None  # no agent ran
    r = await client.post("/hooks/broken", content=body, headers=gen_headers(body, did="b-2"))
    assert r.status_code == 502 and r.json()["status"] == "failed"
    async with services.db.session() as s:
        statuses = {d.source: d.status for d in (await s.execute(select(DeliveryRow))).scalars()}
    assert statuses == {"notify": "delivered", "broken": "failed"}


def test_cli_rotate_and_v2_test(
    home: Path, workdir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from auoreo.cli.app import app
    from auoreo.core.secrets import SecretStore

    _write_routes(workdir)
    SecretStore(home).set("webhook/gen", SECRET)
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    runner = CliRunner()
    r = runner.invoke(app, ["webhook", "rotate", "gen"])
    assert r.exit_code == 0, r.output
    store = SecretStore(home)
    assert store.get("webhook/gen") == r.stdout.strip() != SECRET and store.get("webhook/gen#prev") == SECRET
    payload = write(tmp_path / "p.json", json.dumps({"event": "deploy"}))
    t = runner.invoke(app, ["webhook", "test", "gen", "--file", str(payload), "--event", "deploy"])
    assert t.exit_code == 0 and t.output.startswith('202 {"status": "accepted"'), t.output
