"""I7 (spec §25.14 L1062): bundled github/google oauth.yaml, the refresh helper (in-memory TTL
cache, refresh-token rotation, client_secret from the secret store), the `oauth.token` plugin
host call, `authorization/settled` (API + offline after `auoreo login`) and the doctor rows."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli_app
from auoreo.core import oauth_login as oauth
from auoreo.core.diagnostics_i7 import discord_intent_row, oauth_rows
from auoreo.core.secrets import SecretStore
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.plugins.protocol import RpcError
from auoreo.plugins.requests import HOST_REQUESTS

REPO = Path(__file__).resolve().parents[2]
DEMO = """
auth_url: https://auth.example/authorize
token_url: https://auth.example/token
client_id: demo-client
client_secret: secret:oauth/demo_client_secret
scopes: [read]
auth_params: {access_type: offline}
"""


class Secrets:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    def get(self, name: str, *, by: str | None = None) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


class TokenServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.rotate = False

    def __call__(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        self.calls.append(data | {"url": url})
        n = len(self.calls)
        body: dict[str, Any] = {"access_token": f"at-{n}", "expires_in": 3600}
        if self.rotate:
            body["refresh_token"] = f"rt-rotated-{n}"
        return body


def roots_with_demo(tmp_path: Path) -> list[Path]:
    (tmp_path / "connectors" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "connectors" / "demo" / "oauth.yaml").write_text(DEMO)
    return [tmp_path]


def test_bundled_connectors_are_placeholders_with_documented_scopes() -> None:
    gh = oauth.load_config(REPO / "connectors" / "github" / "oauth.yaml")
    assert gh.device_code_url == "https://github.com/login/device/code" and gh.flow_order[0] == "device"
    assert gh.client_id.startswith(oauth.PLACEHOLDER) and gh.client_secret is None
    go = oauth.load_config(REPO / "connectors" / "google" / "oauth.yaml")
    assert go.flow_order[0] == "loopback" and go.pkce == "S256" and go.device_code_url is None
    assert (
        "https://www.googleapis.com/auth/calendar.events" in go.scopes
        and "https://mail.google.com/" in go.scopes
    )
    assert (
        go.client_secret == "secret:oauth/google_client_secret" and go.auth_params["access_type"] == "offline"
    )
    url = oauth.authorize_url(go, "http://127.0.0.1:5/callback", "st", "ch")
    assert "access_type=offline" in url and "prompt=consent" in url and "calendar.events" in url
    io = oauth.Io(say=print, ask=lambda _l: "", open_url=lambda _u: False)
    with pytest.raises(oauth.OAuthError, match="placeholder"):
        oauth.login(gh, io)
    with pytest.raises(ValueError):  # a literal client secret is refused: only secret: refs
        oauth.OAuthConfig.model_validate(
            {"auth_url": "a", "token_url": "t", "client_id": "c", "client_secret": "x"}
        )


async def test_access_token_cache_ttl_and_client_secret(tmp_path: Path) -> None:
    now = [1000.0]
    server = TokenServer()
    secrets = Secrets({"oauth/demo": "rt-1", "oauth/demo_client_secret": "cs-1"})
    tokens = oauth.OAuthTokens(lambda: roots_with_demo(tmp_path), secrets, post=server, clock=lambda: now[0])
    assert await tokens.access_token("demo") == "at-1"
    assert await tokens.access_token("demo") == "at-1" and len(server.calls) == 1  # cached
    call = server.calls[0]
    assert call == {
        "grant_type": "refresh_token",
        "refresh_token": "rt-1",
        "client_id": "demo-client",
        "client_secret": "cs-1",
        "url": "https://auth.example/token",
    }
    now[0] += 3600 - oauth.REFRESH_MARGIN_S - 1
    assert await tokens.access_token("demo") == "at-1"
    now[0] += 2  # inside the refresh margin → refresh
    assert await tokens.access_token("demo") == "at-2" and tokens.refreshes == 2
    secrets.set("oauth/demo", "rt-new-login")  # a new `auoreo login` invalidates the cache
    assert await tokens.access_token("demo") == "at-3"
    tokens.forget("demo")
    assert not tokens.cached("demo")
    # concurrent callers share one refresh
    got = await asyncio.gather(*(tokens.access_token("demo") for _ in range(5)))
    assert set(got) == {"at-4"} and len(server.calls) == 4


async def test_rotated_refresh_token_is_written_back(tmp_path: Path) -> None:
    server = TokenServer()
    server.rotate = True
    secrets = Secrets({"oauth/demo": "rt-1", "oauth/demo_client_secret": "cs"})
    tokens = oauth.OAuthTokens(lambda: roots_with_demo(tmp_path), secrets, post=server)
    assert await tokens.access_token("demo") == "at-1"
    assert secrets.data["oauth/demo"] == "rt-rotated-1"
    assert await tokens.access_token("demo") == "at-1"  # the rotated token's fingerprint is cached


async def test_access_token_honest_errors(tmp_path: Path) -> None:
    tokens = oauth.OAuthTokens(lambda: roots_with_demo(tmp_path), Secrets({}), post=TokenServer())
    with pytest.raises(oauth.OAuthError, match="auoreo login demo"):
        await tokens.access_token("demo")
    tokens = oauth.OAuthTokens(
        lambda: roots_with_demo(tmp_path), Secrets({"oauth/demo": "rt"}), post=TokenServer()
    )
    with pytest.raises(oauth.OAuthError, match="demo_client_secret"):
        await tokens.access_token("demo")
    tokens = oauth.OAuthTokens(lambda: [tmp_path / "none"], Secrets({"oauth/x": "rt"}), post=TokenServer())
    with pytest.raises(oauth.OAuthError, match="no connectors/x/oauth.yaml"):
        await tokens.access_token("x")
    bad = oauth.OAuthTokens(
        lambda: roots_with_demo(tmp_path / "b"),
        Secrets({"oauth/demo": "rt", "oauth/demo_client_secret": "c"}),
        post=lambda url, data: {"error": "invalid_grant"},
    )
    with pytest.raises(oauth.OAuthError, match="invalid_grant"):
        await bad.access_token("demo")


async def test_plugin_oauth_token_host_call_is_gated(services: Services, home: Path) -> None:
    roots_with_demo(home)
    services.secrets.set("oauth/demo", "rt-x")
    services.secrets.set("oauth/demo_client_secret", "cs")
    tokens = oauth.tokens_for(services)
    tokens.post = TokenServer()
    host = SimpleNamespace(s=services)
    allowed = SimpleNamespace(name="mail", manifest=SimpleNamespace(consumes=["oauth:demo"]))
    res = await HOST_REQUESTS["oauth.token"](host, allowed, {"connector": "demo"})  # type: ignore[arg-type]
    assert res == {"access_token": "at-1"} and "rt-x" not in str(res)
    denied = SimpleNamespace(name="evil", manifest=SimpleNamespace(consumes=["secrets:x"]))
    with pytest.raises(RpcError, match="not declared"):
        await HOST_REQUESTS["oauth.token"](host, denied, {"connector": "demo"})  # type: ignore[arg-type]
    services.secrets.delete("oauth/demo")
    tokens.forget()
    with pytest.raises(RpcError, match="not signed in"):
        await HOST_REQUESTS["oauth.token"](host, allowed, {"connector": "demo"})  # type: ignore[arg-type]
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    async with services.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "plugin.oauth"]
    assert [(r.actor, r.decision) for r in rows] == [
        ("plugin:mail", "allow"),
        ("plugin:evil", "deny"),
        ("plugin:mail", "allow"),
    ]
    assert all(r.detail == {"connector": "demo"} for r in rows)


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_settled_endpoint_emits_event_and_drops_cache(
    client: httpx.AsyncClient, services: Services
) -> None:
    r = await client.post("/api/oauth/demo/settled", json={"flow": "device"})
    assert r.status_code == 409
    services.secrets.set("oauth/demo", "rt")
    tokens = oauth.tokens_for(services)
    tokens._cache["demo"] = oauth._Cached("stale", 1e18, "x")
    r = await client.post("/api/oauth/demo/settled", json={"flow": "device"})
    assert r.status_code == 200 and r.json() == {"connector": "demo", "settled": True, "flow": "device"}
    assert not tokens.cached("demo")
    events = await services.events.replay(0)
    assert [e.data for e in events if e.type == "authorization/settled"] == [
        {"connector": "demo", "flow": "device"}
    ]
    assert (await client.post("/api/oauth/Bad!/settled", json={})).status_code == 422


def test_cli_login_records_settled_event_offline(
    home: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")  # nothing listens: offline append
    monkeypatch.chdir(workdir)
    roots_with_demo(home)
    SecretStore(home).set("oauth/demo_client_secret", "cs-9")
    seen: list[dict[str, str]] = []

    def post(url: str, data: dict[str, str]) -> dict[str, Any]:
        seen.append(data)
        return {"access_token": "at", "refresh_token": "rt-9"}

    monkeypatch.setattr(oauth, "http_post", post)
    res = CliRunner().invoke(cli_app, ["login", "demo", "--flow", "manual"], input="code\n")
    assert res.exit_code == 0, res.output
    assert seen[0]["client_secret"] == "cs-9" and "cs-9" not in res.output

    async def settled() -> list[dict[str, Any]]:
        from auoreo.core.bus import EventBus
        from auoreo.core.db import Database
        from auoreo.core.event_store import EventStore

        db = Database(Settings(home=home).db_path)
        await db.init()
        try:
            return [
                e.data
                for e in await EventStore(db, EventBus()).replay(0)
                if e.type == "authorization/settled"
            ]
        finally:
            await db.close()

    assert asyncio.run(settled()) == [{"connector": "demo", "flow": "manual"}]


def test_doctor_oauth_rows(home: Path, tmp_path: Path) -> None:
    roots = [*roots_with_demo(tmp_path), REPO]
    store = SecretStore(home)
    rows = {r.check: r for r in oauth_rows(Settings(home=home), roots, store)}
    assert rows["oauth:github"].level == "ok" and "placeholder" in rows["oauth:github"].detail
    assert rows["oauth:demo"].level == "warn" and rows["oauth:demo"].remedy == "auoreo login demo"
    store.set("oauth/demo", "rt")
    rows = {r.check: r for r in oauth_rows(Settings(home=home), roots, store)}
    assert rows["oauth:demo"].level == "ok" and "rt" not in rows["oauth:demo"].detail.split()


async def test_doctor_discord_intent_row(home: Path, tmp_path: Path) -> None:
    plug = tmp_path / "plugins" / "discord"
    plug.mkdir(parents=True)
    (plug / "plugin.yaml").write_text("name: discord\nenabled: true\n")
    st, store = Settings(home=home), SecretStore(home)
    assert (await discord_intent_row(st, [tmp_path], store))[
        0
    ].remedy == "auoreo secrets set discord/bot_token"
    store.set("discord/bot_token", "tok-123")
    flags = {"v": 0}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "1", "flags": flags["v"]})

    (row,) = await discord_intent_row(st, [tmp_path], store, httpx.MockTransport(handler))
    assert (
        row.level == "error"
        and "MESSAGE CONTENT" in row.detail
        and "Privileged Gateway Intents" in row.remedy
    )
    assert seen[0].headers["authorization"] == "Bot tok-123" and "tok-123" not in row.detail
    flags["v"] = 1 << 19
    (row,) = await discord_intent_row(st, [tmp_path], store, httpx.MockTransport(handler))
    assert row.level == "ok"
    rejected = httpx.MockTransport(lambda r: httpx.Response(401))
    assert (await discord_intent_row(st, [tmp_path], store, rejected))[0].level == "error"

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert (await discord_intent_row(st, [tmp_path], store, httpx.MockTransport(offline)))[0].level == "warn"
    (plug / "plugin.yaml").write_text("name: discord\nenabled: false\n")
    assert await discord_intent_row(st, [tmp_path], store) == []
