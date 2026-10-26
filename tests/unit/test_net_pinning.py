"""G4b: web_fetch DNS pinning — resolve once, validate, connect to the pinned IP."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools import net
from auoreo.tools.context import ToolContext, ToolError

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns() -> Iterator[dict[str, list[list[str]]]]:
    """Scripted resolver: each lookup of a host pops the next answer (rebinding)."""
    answers: dict[str, list[list[str]]] = {}
    calls: list[str] = []

    async def fake(host: str, port: int) -> list[str]:
        calls.append(host)
        seq = answers[host]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    net.RESOLVER[0] = fake
    answers["__calls__"] = calls  # type: ignore[assignment]
    try:
        yield answers
    finally:
        net.RESOLVER[0] = net.system_resolve


def recorder() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://evil.test/x"})
        return httpx.Response(200, text="ok")

    return httpx.MockTransport(handler), seen


async def test_connects_to_the_pinned_ip_once_resolved(dns: dict) -> None:
    dns["rebind.test"] = [[PUBLIC], ["127.0.0.1"]]  # second answer would be internal
    inner, seen = recorder()
    async with httpx.AsyncClient(transport=net.PinnedTransport(inner), trust_env=False) as client:
        for _ in range(3):
            assert (await client.get("https://rebind.test/a?b=1")).text == "ok"
    assert dns["__calls__"] == ["rebind.test"]  # resolved exactly once
    assert {str(r.url) for r in seen} == {f"https://{PUBLIC}/a?b=1"}
    assert all(r.headers["host"] == "rebind.test" for r in seen)
    assert all(r.extensions["sni_hostname"] == "rebind.test" for r in seen)


async def test_non_default_port_keeps_host_header(dns: dict) -> None:
    dns["svc.test"] = [[PUBLIC]]
    inner, seen = recorder()
    async with httpx.AsyncClient(transport=net.PinnedTransport(inner), trust_env=False) as client:
        await client.get("http://svc.test:8080/")
    assert str(seen[0].url) == f"http://{PUBLIC}:8080/" and seen[0].headers["host"] == "svc.test:8080"
    assert "sni_hostname" not in seen[0].extensions  # plain http: no SNI


@pytest.mark.parametrize(
    "answer",
    [
        ["10.0.0.5"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["100.64.1.1"],
        [PUBLIC, "192.168.1.1"],
        ["::ffff:127.0.0.1"],
    ],
)
async def test_blocked_answers_never_connect(dns: dict, answer: list[str]) -> None:
    dns["bad.test"] = [answer]
    inner, seen = recorder()
    async with httpx.AsyncClient(transport=net.PinnedTransport(inner), trust_env=False) as client:
        with pytest.raises(ToolError, match="SSRF guard"):
            await client.get("http://bad.test/")
    assert seen == []


async def test_ip_literals_are_validated_without_dns(dns: dict) -> None:
    inner, seen = recorder()
    async with httpx.AsyncClient(transport=net.PinnedTransport(inner), trust_env=False) as client:
        with pytest.raises(ToolError, match="SSRF guard"):
            await client.get("http://127.0.0.1/")
        with pytest.raises(ToolError, match="SSRF guard"):
            await client.get("http://[::1]/")
        assert (await client.get(f"http://{PUBLIC}/")).status_code == 200
    assert dns["__calls__"] == [] and seen[0].headers["host"] == PUBLIC


async def test_safe_request_revalidates_redirect_hops(dns: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    dns["good.test"] = [[PUBLIC]]
    dns["evil.test"] = [["10.1.2.3"]]
    inner, seen = recorder()
    real = net.PinnedTransport

    def patched(inner_arg: object = None, *, allow_private: bool = False) -> net.PinnedTransport:
        return real(inner, allow_private=allow_private)

    monkeypatch.setattr(net, "PinnedTransport", patched)
    with pytest.raises(ToolError, match=r"blocked address 10\.1\.2\.3 for host 'evil.test'"):
        await net.safe_request("GET", "http://good.test/redirect")
    assert [r.headers["host"] for r in seen] == ["good.test"]


async def test_shape_checks() -> None:
    for url, msg in [
        ("ftp://x.test/", "only http/https"),
        ("http:///nohost", "no host"),
        ("http://u:p@x.test/", "credentials"),
    ]:
        with pytest.raises(ToolError, match=msg):
            net.check_shape(url)


@pytest.fixture
async def local_server() -> AsyncIterator[tuple[int, list[bytes]]]:
    heads: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        heads.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: 5\r\n\r\nhello")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], heads
    finally:
        server.close()
        await server.wait_closed()


async def test_real_connection_goes_to_the_pinned_address(
    local_server: tuple[int, list[bytes]], dns: dict
) -> None:
    port, heads = local_server
    dns["app.internal.test"] = [["127.0.0.1"]]
    res = await net.safe_request("GET", f"http://app.internal.test:{port}/p", allow_private=True)
    assert res.status == 200 and res.body == b"hello"
    assert f"host: app.internal.test:{port}".encode() in heads[0].lower()
    with pytest.raises(ToolError, match="SSRF guard"):  # without the opt-in the same name is refused
        await net.safe_request("GET", f"http://app.internal.test:{port}/p")


async def test_web_fetch_caps_body_and_blocks_rebinding(
    services: Services, workdir: Path, dns: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    dns["big.test"] = [[PUBLIC]]
    dns["rebound.test"] = [["127.0.0.1"]]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 200_000, headers={"content-type": "text/plain"})

    real = net.PinnedTransport
    monkeypatch.setattr(
        net, "PinnedTransport", lambda inner=None, *, allow_private=False: real(httpx.MockTransport(handler))
    )
    # I9: web_fetch is `net: proxy` → the client talks to the egress proxy (mocked here)
    monkeypatch.setattr(net, "proxy_transport", lambda url: httpx.MockTransport(handler))
    services.settings.tools.fetch_max_bytes = 64 * 1024
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    rules = PermissionEngine([Rule(permission="webfetch", pattern="*", action="allow")])
    run = services.coordinator.runner.executor.run
    out = await run(
        ToolCall("c1", "web_fetch", json.dumps({"url": "http://big.test/"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=rules,
    )
    assert not out.result.is_error and "[truncated]" in out.result.content
    assert out.result.meta["truncated"] is True
    out = await run(
        ToolCall("c2", "web_fetch", json.dumps({"url": "http://rebound.test/"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=rules,
    )
    assert out.result.is_error and "SSRF guard" in out.result.content
