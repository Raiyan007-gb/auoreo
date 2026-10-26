"""H2 webhook ingress: `in`/`in_file` + `all/any/not` filters, `{{__raw__}}`, `script:` transform,
`transform` hooks and the `allowed_cidrs` IP allowlist with trusted proxies (spec §15, §19.11, §2.11)."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pydantic import ValidationError
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.automation.templating import RAW_CAP, filter_matches
from auoreo.automation.webhook_extras import client_ip
from auoreo.automation.webhooks import sign
from auoreo.core.db import DeliveryRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.manifests import WebhookFilter, WebhookManifest
from tests.conftest import write

SECRET = "whsec_h2_0123456789"
BASE = 'provider: github\nsecret: "secret:webhook/h2"\ndeliver: [log]\nrate_limit_per_minute: 1000\n'
ROUTES = {
    "filt": BASE
    + """description: filters
filters:
  - any:
      - {field: action, in: [opened, reopened]}
      - {field: sender.login, in_file: allow/users.txt}
  - not: {field: pull_request.draft, equals: "true"}
prompt: "raw={{__raw__}}"
""",
    "cidr": BASE + 'description: ip allowlist\nallowed_cidrs: ["10.0.0.0/8"]\nprompt: "ok"\n',
    "local": BASE + 'description: loopback allowlist\nallowed_cidrs: ["127.0.0.0/8"]\nprompt: "ok"\n',
    "scr": BASE
    + 'description: script\nscript: xform.py\nprompt: "tag={{event.tag}} hooked={{event.hooked}}"\n',
    "slow": BASE + 'description: slow script\nscript: slow.py\nscript_timeout_s: 0.5\nprompt: "x"\n',
}
XFORM = """import json, sys
p = json.load(sys.stdin)
if p.get("drop"):
    print("null")
else:
    p["tag"] = "from-script"
    print(json.dumps(p))
"""
HOOK = """import json, sys
body = json.load(sys.stdin)
p = body["payload"]
p["hooked"] = body["kind"]
print(json.dumps({"payload": p}))
"""


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, automation_start_delay_s=0)


@pytest.fixture
async def services(
    settings: Settings, workdir: Path, gateway: FakeGateway, home: Path
) -> AsyncIterator[Services]:
    for name, text in ROUTES.items():
        write(workdir / ".auoreo" / "webhooks" / f"{name}.yaml", f"name: {name}\n{text}")
    write(home / "webhooks" / "allow" / "users.txt", "# trusted senders\nalice\n\nbob\n")
    write(home / "scripts" / "xform.py", XFORM)
    write(home / "scripts" / "slow.py", "import time\ntime.sleep(5)\n")
    write(workdir / ".auoreo" / "scripts" / "hook.py", HOOK)
    write(
        workdir / ".auoreo" / "hooks" / "tag.yaml",
        f"name: tag\ndescription: transform\nevent: transform\nmatcher: 'webhook:scr'\n"
        f"command: ['{sys.executable}', './scripts/hook.py']\n",
    )
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    svc.secrets.set("webhook/h2", SECRET)
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


async def post(
    client: httpx.AsyncClient, route: str, payload: dict[str, Any], **headers: str
) -> httpx.Response:
    body = json.dumps(payload).encode()
    hdrs = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sign(SECRET, body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": uuid.uuid4().hex,
        **headers,
    }
    return await client.post(f"/hooks/{route}", content=body, headers=hdrs)


async def prompt_of(services: Services, delivery_id: int) -> str:
    async with services.db.session() as s:
        row = await s.get(DeliveryRow, delivery_id)
    assert row is not None
    return str(row.payload["prompt"])


def pr(action: str, sender: str = "zed", draft: bool = False, **extra: Any) -> dict[str, Any]:
    return {"action": action, "sender": {"login": sender}, "pull_request": {"draft": draft}, **extra}


async def test_filter_combinators_in_and_in_file(client: httpx.AsyncClient, services: Services) -> None:
    assert (await post(client, "filt", pr("opened"))).status_code == 202
    assert (await post(client, "filt", pr("closed", sender="bob"))).status_code == 202  # in_file
    r = await post(client, "filt", pr("closed", sender="mallory"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    r = await post(client, "filt", pr("opened", draft=True))  # not: draft
    assert r.status_code == 200 and r.json()["status"] == "ignored"


async def test_raw_template_is_capped(client: httpx.AsyncClient, services: Services) -> None:
    small = pr("opened", note="hello")
    r = await post(client, "filt", small)
    assert r.status_code == 202
    assert f"raw={json.dumps(small)}" in await prompt_of(services, r.json()["delivery_id"])
    big = pr("opened", note="x" * 6000)
    r = await post(client, "filt", big)
    text = await prompt_of(services, r.json()["delivery_id"])
    raw = text.split("raw=", 1)[1].split("\n</untrusted-data>", 1)[0]
    assert raw.endswith("…") and len(raw) == RAW_CAP + 1


async def test_script_transform_and_transform_hook(client: httpx.AsyncClient, services: Services) -> None:
    r = await post(client, "scr", {"action": "opened"})
    assert r.status_code == 202, r.text
    text = await prompt_of(services, r.json()["delivery_id"])
    assert "tag=from-script" in text and "hooked=webhook:scr" in text
    r = await post(client, "scr", {"action": "opened", "drop": True})
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    r = await post(client, "slow", {"action": "opened"})
    assert r.status_code == 502 and r.json()["detail"] == "transform failed"
    async with services.db.session() as s:
        sources = {d.source for d in (await s.execute(select(DeliveryRow))).scalars()}
    assert "slow" not in sources


async def test_ip_allowlist_honours_trusted_proxies(client: httpx.AsyncClient, services: Services) -> None:
    r = await post(client, "cidr", {"action": "x"})
    assert r.status_code == 403  # the ASGI client is 127.0.0.1
    assert (await post(client, "local", {"action": "x"})).status_code == 202
    r = await post(client, "cidr", {"action": "x"}, **{"X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 403  # the peer is not a trusted proxy: the header is ignored
    services.settings.teams2.webhook_trusted_proxies = ["127.0.0.1/32"]
    r = await post(client, "cidr", {"action": "x"}, **{"X-Forwarded-For": "10.1.2.3"})
    assert r.status_code == 202


def test_client_ip_and_manifest_validation() -> None:
    trusted = ["192.168.0.0/16"]
    assert client_ip("203.0.113.9", {"x-forwarded-for": "10.0.0.1"}, trusted) == "203.0.113.9"
    hops = {"x-forwarded-for": "1.2.3.4, 10.9.9.9, 192.168.1.1"}
    assert client_ip("192.168.1.2", hops, trusted) == "10.9.9.9"
    with pytest.raises(ValidationError, match="invalid CIDR"):
        WebhookManifest.model_validate(
            {"name": "x", "description": "d", "secret": "s", "prompt": "p", "allowed_cidrs": ["nope"]}
        )
    with pytest.raises(ValidationError, match="without '..'"):
        WebhookManifest.model_validate(
            {"name": "x", "description": "d", "secret": "s", "prompt": "p", "script": "../x"}
        )
    with pytest.raises(ValidationError, match="one of all / any / not"):
        WebhookFilter.model_validate(
            {"all": [{"field": "a", "exists": True}], "not": {"field": "b", "exists": True}}
        )
    with pytest.raises(ValidationError, match="needs `field`"):
        WebhookFilter.model_validate({"equals": "x"})
    f = WebhookFilter.model_validate(
        {"all": [{"field": "a", "in": ["1", "2"]}, {"not": {"field": "b", "exists": True}}]}
    )
    assert (
        filter_matches(f, {"a": 2})
        and not filter_matches(f, {"a": 2, "b": 0})
        and not filter_matches(f, {"a": 3})
    )
    escape = WebhookFilter.model_validate({"field": "a", "in_file": "x.txt"})
    assert not filter_matches(escape, {"a": "1"}, None)  # no base dir → no allowed values
