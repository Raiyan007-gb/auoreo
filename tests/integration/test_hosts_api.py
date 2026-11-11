"""M7 hosts-c D8: `/api/hosts` (list, edit, delete, check) shows fingerprints and state only."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core import host_keys, hosts
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from auoreo.core.session_jwt import tokens_for
from tests.unit.test_env_ssh import audit_rows

PW = "pw-hosts-123456"
AUTH = {"Authorization": f"Bearer {PW}"}
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINsODwY14lVJHbIWemgeTyJYQTn4CwG5ST0SZm8pBl78"
BODY = {"host": "10.0.0.5", "port": 22, "user": "ubuntu", "key_name": "SSH_KEY_LAB", "tags": ["readonly"]}


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    services.settings.server_password = PW
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


def web(services: Services, on: bool = True) -> None:
    services.settings.m7.ssh.web = on


async def test_404_while_the_flag_is_off(services: Services, client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/hosts", headers=AUTH)).status_code == 404
    assert (await client.put("/api/hosts/lab", json=BODY, headers=AUTH)).status_code == 404
    assert not (services.settings.home / "hosts").exists()


async def test_crud_audit_and_state(services: Services, client: httpx.AsyncClient) -> None:
    web(services)
    home = services.settings.home
    r = await client.put("/api/hosts/lab", json=BODY, headers=AUTH)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["trusted"] is False and row["key_present"] is False and row["usable_by_agents"] is False
    assert hosts.read_hosts(home)[0]["lab"].tags == ("readonly",)
    hosts.trust(home, "10.0.0.5", 22, KEY_A)
    kp = host_keys.generate()
    SecretStore(home).set("SSH_KEY_LAB", kp.private)
    host_keys.record_key(home, "SSH_KEY_LAB", fingerprint=kp.fingerprint)
    body = (await client.get("/api/hosts", headers=AUTH)).json()
    lab = body["hosts"][0]
    assert lab["trusted"] and lab["key_present"] and lab["key_fingerprint"] == kp.fingerprint
    assert lab["host_key_fingerprints"] == [hosts.fingerprint_of(KEY_A)] and body["enabled"] is False
    # an address change keeps no trust: the new host:port is untrusted until `ssh test`
    r = await client.put("/api/hosts/lab", json={**BODY, "host": "10.0.0.6"}, headers=AUTH)
    assert r.json()["trusted"] is False and not hosts.known_keys(home, "10.0.0.6", 22)
    saved = await audit_rows(services, "host.saved")
    assert [s.actor for s in saved] == ["api", "api"] and saved[1].detail["changed"] == ["host"]
    assert (await client.delete("/api/hosts/lab", headers=AUTH)).status_code == 204
    assert (await client.delete("/api/hosts/lab", headers=AUTH)).status_code == 404
    assert SecretStore(home).get("SSH_KEY_LAB") == kp.private  # never the secret
    assert (await audit_rows(services, "host.deleted"))[0].actor == "api"
    blob = json.dumps(body) + "".join(json.dumps(r.detail) for r in saved)
    assert "PRIVATE" not in blob and kp.private.split("\n")[1] not in blob


@pytest.mark.parametrize(
    "bad",
    [
        {**BODY, "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----"},
        {**BODY, "key_name": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"},
        {**BODY, "key_name": "ssh-ed25519 AAAAC3Nz"},
        {**BODY, "host": "169.254.169.254"},
        {**BODY, "host": "user@evil"},
        {**BODY, "jump": "10.0.0.1"},
        {**BODY, "tags": ["Bad Tag"]},
        {**BODY, "fingerprint": "SHA256:x"},
    ],
)
async def test_422_for_key_text_unknown_fields_and_bad_values(
    services: Services, client: httpx.AsyncClient, bad: dict[str, object]
) -> None:
    web(services)
    r = await client.put("/api/hosts/lab", json=bad, headers=AUTH)
    assert r.status_code == 422, r.text
    assert "BEGIN" not in r.text and "AAAAC3Nz" not in r.text and "evil" not in r.text  # no echo
    assert not (services.settings.home / "hosts" / "lab.yaml").exists()


@pytest.mark.parametrize("name", ["con", "..", "Lab", "a.b"])
async def test_bad_names_refused(services: Services, client: httpx.AsyncClient, name: str) -> None:
    web(services)
    r = await client.put(f"/api/hosts/{name}", json=BODY, headers=AUTH)
    assert r.status_code in (404, 405, 422)
    assert hosts.read_hosts(services.settings.home) == ({}, [])


async def test_capability_tokens_and_no_auth_refused(services: Services, client: httpx.AsyncClient) -> None:
    web(services)
    token, _ = tokens_for(services).mint(ip=None, kind="cap", session_id="ses_x")
    cap = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/hosts", headers=cap)).status_code in (401, 403)
    assert (await client.put("/api/hosts/lab", json=BODY, headers=cap)).status_code in (401, 403)
    assert (await client.get("/api/hosts")).status_code == 401
    assert not (services.settings.home / "hosts" / "lab.yaml").exists()


async def test_writes_refused_over_the_public_tunnel(services: Services, client: httpx.AsyncClient) -> None:
    web(services)
    (services.settings.home / "tunnel.json").write_text(json.dumps({"url": "https://abc.trycloudflare.com"}))
    over = {**AUTH, "Host": "abc.trycloudflare.com"}
    assert (await client.get("/api/hosts", headers=over)).status_code == 200  # reads stay allowed
    r = await client.put("/api/hosts/lab", json=BODY, headers=over)
    assert r.status_code == 403 and "tunnel" in r.text
    hosts.write_host(services.settings.home, "lab", {"host": "h", "user": "u", "key_secret": "secret:K"})
    assert (await client.delete("/api/hosts/lab", headers=over)).status_code == 403
    assert (
        await client.delete("/api/hosts/lab", headers={**AUTH, "X-Forwarded-Host": "abc.trycloudflare.com"})
    ).status_code == 403
    assert hosts.read_hosts(services.settings.home)[0]["lab"]


async def test_check_never_trusts_an_unknown_host_key(services: Services, client: httpx.AsyncClient) -> None:
    web(services)
    hosts.write_host(
        services.settings.home, "lab", {"host": "127.0.0.1", "port": 9, "user": "u", "key_secret": "secret:K"}
    )
    r = await client.post("/api/hosts/lab/check", headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is False and "auoreo ssh test lab" in r.json()["detail"]
    assert not hosts.known_keys(services.settings.home, "127.0.0.1", 9)
    assert (await client.post("/api/hosts/nope/check", headers=AUTH)).status_code == 404
