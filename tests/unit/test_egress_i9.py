"""I9 (spec §25.10 L1017/L1021): per-tool egress — the loopback CONNECT proxy, the executor's
net stage (`none | proxy | direct`), one-shot direct grants with a 24 h justification store, and
the sandbox wiring (Landlock CONNECT_TCP to the proxy port, bwrap/docker/seatbelt)."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from auoreo.backends import landlock
from auoreo.backends.sandbox import ExecSpec, proxy_confinement
from auoreo.core import justifications
from auoreo.core.audit import AuditRow
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import McpServerManifest, ToolManifest
from auoreo.tools import net
from auoreo.tools.context import CURRENT_EXEC, ExecPolicy, ToolContext, ToolError
from auoreo.tools.egress import EgressProxy, denial_for, proxy_of, split_hostport
from tests.conftest import make_tool


@pytest.fixture
def dns() -> Iterator[dict[str, list[str]]]:
    answers: dict[str, list[str]] = {}

    async def fake(host: str, port: int) -> list[str]:
        return answers[host]

    net.RESOLVER[0] = fake
    try:
        yield answers
    finally:
        net.RESOLVER[0] = net.system_resolve


@pytest.fixture
async def origin() -> AsyncIterator[tuple[int, list[bytes]]]:
    """A loopback HTTP server that echoes the request line."""
    heads: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        heads.append(head)
        line = head.split(b"\r\n", 1)[0]
        body = b"origin saw " + line
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: %d\r\n\r\n%s" % (len(body), body)
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], heads
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
async def proxy(home: Path) -> AsyncIterator[EgressProxy]:
    st = Settings(home=home, gateway="fake")
    st.i9.egress.allow_private_hosts = ["127.0.0.1", "app.test"]
    p = EgressProxy(st)
    await p.start()
    try:
        yield p
    finally:
        await p.close()


def _auth(token: str) -> str:
    return "Basic " + base64.b64encode(f"auoreo:{token}".encode()).decode()


async def _raw(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    return data


# ------------------------------------------------------------------ the proxy
def test_split_hostport() -> None:
    assert split_hostport("example.com:443") == ("example.com", 443)
    assert split_hostport("[::1]:8443") == ("::1", 8443)
    with pytest.raises(ValueError):
        split_hostport("x:0")


async def test_plain_http_through_the_proxy_is_logged(proxy: EgressProxy, origin: tuple) -> None:
    port, heads = origin
    token = proxy.mint("t:1")
    async with httpx.AsyncClient(transport=net.proxy_transport(proxy.url(token)), trust_env=False) as c:
        res = await c.get(f"http://127.0.0.1:{port}/a?b=1")
    assert res.status_code == 200 and res.text == "origin saw GET /a?b=1 HTTP/1.1"
    assert b"proxy-authorization" not in heads[0].lower()  # hop-by-hop credentials are stripped
    entry = proxy.revoke(token)
    assert entry is not None and entry.conns == [{"host": "127.0.0.1", "port": port, "allowed": True}]


async def test_connect_tunnel_pins_the_validated_address(
    proxy: EgressProxy, origin: tuple, dns: dict
) -> None:
    port, _ = origin
    dns["app.test"] = ["127.0.0.1"]  # resolved by the proxy itself, once, then connected by IP
    token = proxy.mint("t:2")
    req = f"CONNECT app.test:{port} HTTP/1.1\r\nHost: app.test\r\nProxy-Authorization: {_auth(token)}\r\n\r\n"
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(req.encode())
    await writer.drain()
    assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
    writer.write(b"GET /tunnel HTTP/1.1\r\nHost: app.test\r\n\r\n")
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), 5)
    writer.close()
    assert b"origin saw GET /tunnel" in data


async def test_missing_or_revoked_token_is_407(proxy: EgressProxy) -> None:
    assert (await _raw(proxy.port, b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 407")
    token = proxy.mint("t:3")
    proxy.revoke(token)
    req = f"CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: {_auth(token)}\r\n\r\n".encode()
    assert (await _raw(proxy.port, req)).startswith(b"HTTP/1.1 407")
    assert proxy.stats["unauthorized"] == 2


@pytest.mark.parametrize(
    ("target", "setup", "reason"),
    [
        ("169.254.169.254:80", None, "SSRF guard"),
        ("10.0.0.7:443", None, "SSRF guard"),
        ("evil.test:443", ("deny_hosts", ["*.test"]), "deny_hosts"),
        ("other.example:443", ("allow_hosts", ["api.example"]), "allow_hosts"),
        ("api.example:22", ("allowed_ports", [443]), "allowed_ports"),
    ],
)
async def test_refusals_are_403_with_a_reason(
    proxy: EgressProxy, dns: dict, target: str, setup: tuple | None, reason: str
) -> None:
    dns.update(
        {"evil.test": ["93.184.216.34"], "other.example": ["93.184.216.34"], "api.example": ["93.184.216.34"]}
    )
    if setup is not None:
        setattr(proxy.cfg, setup[0], setup[1])
    token = proxy.mint("t:4")
    data = await _raw(
        proxy.port, f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: {_auth(token)}\r\n\r\n".encode()
    )
    assert data.startswith(b"HTTP/1.1 403") and b"X-Auoreo-Egress: denied" in data
    assert reason in data.decode()
    assert reason in (denial_for(proxy.url(token)) or "")


async def test_configured_searxng_host_is_private_ok(home: Path) -> None:
    st = Settings(home=home, gateway="fake", searxng_url="http://127.0.0.1:8888")
    p = EgressProxy(st)
    assert p.private_ok("127.0.0.1", 8888) and not p.private_ok("127.0.0.1", 22)


# ------------------------------------------------------------------ net helpers
async def test_net_none_policy_refuses_clients() -> None:
    token = CURRENT_EXEC.set(ExecPolicy(net="none"))
    try:
        with pytest.raises(ToolError, match="net: none"):
            net.egress_client()
    finally:
        CURRENT_EXEC.reset(token)


async def test_proxied_safe_request_maps_connect_refusals(proxy: EgressProxy, dns: dict) -> None:
    dns["blocked.test"] = ["93.184.216.34"]
    proxy.cfg.deny_hosts = ["blocked.test"]
    tok = proxy.mint("t:5")
    cv = CURRENT_EXEC.set(ExecPolicy(net="proxy", proxy_url=proxy.url(tok)))
    try:
        with pytest.raises(ToolError, match=r"egress proxy refused .*deny_hosts"):
            await net.safe_request("GET", "https://blocked.test/")
        with pytest.raises(ToolError, match="SSRF guard"):  # early client-side check, clear message
            await net.safe_request("GET", "http://169.254.169.254/latest/meta-data")
    finally:
        CURRENT_EXEC.reset(cv)


# ------------------------------------------------------------------ executor stage
def _ctx(services: Services, workdir: Path, *, interactive: bool = False) -> ToolContext:
    return ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, interactive=interactive)


async def _audit(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        return [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == action]


async def test_web_fetch_goes_through_the_server_proxy(
    services: Services, workdir: Path, origin: tuple
) -> None:
    port, heads = origin
    proxy = proxy_of(services)
    assert proxy is not None and proxy.running  # started by the I9 extension starter
    proxy.cfg.allow_private_hosts = ["127.0.0.1"]
    rules = PermissionEngine([Rule(permission="webfetch", pattern="*", action="allow")])
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "web_fetch", json.dumps({"url": f"http://127.0.0.1:{port}/page"})),
        tools=services.harness.tools,
        ctx=_ctx(services, workdir),
        engine=rules,
    )
    assert not out.result.is_error, out.result.content
    assert "origin saw GET /page" in out.result.content
    row = (await _audit(services, "tool.call"))[-1]
    netd = row.detail["net"]
    assert netd["mode"] == "proxy" and netd["host"] == "127.0.0.1" and netd["port"] == port
    assert {"host": "127.0.0.1", "port": port, "allowed": True} in netd["conns"]
    assert proxy.status()["tokens"] == 0  # the per-call token was revoked


DIRECT = (
    "net: direct\ndefault_action: allow\nparameters:\n  type: object\n  properties: {url: {type: string}}\n"
)


async def test_direct_needs_justification_and_a_one_shot_grant(services: Services, workdir: Path) -> None:
    make_tool(workdir / ".auoreo", "raw_probe", "return 'probed'", DIRECT)
    services.harness.reload()
    tool = services.harness.tools["raw_probe"]
    assert "net_justification" in tool.manifest.parameters["properties"]
    run = services.coordinator.runner.executor.run
    engine = PermissionEngine([])
    out = await run(
        ToolCall("c1", "raw_probe", '{"url": "https://x.example/"}'),
        tools=services.harness.tools,
        ctx=_ctx(services, workdir),
        engine=engine,
    )
    assert out.result.is_error and "net_justification" in out.result.content
    why = "probe the vendor status page that the proxy allow-list does not cover"
    args = json.dumps({"url": "https://x.example/", "net_justification": why})
    engine = PermissionEngine([Rule(permission="net_direct", pattern="raw_probe:*", action="allow")])
    out = await run(
        ToolCall("c2", "raw_probe", args),
        tools=services.harness.tools,
        ctx=_ctx(services, workdir),
        engine=engine,
    )
    assert out.result.content == "probed"
    grant = (await _audit(services, "net.direct_grant"))[-1]
    gid = grant.detail["grantId"]
    assert grant.detail["justificationHash"] == justifications.justification_hash(why)
    assert why not in json.dumps(grant.detail)  # plaintext never reaches the audit row
    stored = justifications.lookup(services.settings.home, gid)
    assert stored is not None and stored["text"] == why and stored["kind"] == "net_direct"
    call_row = (await _audit(services, "tool.call"))[-1]
    assert call_row.detail["net"] == {"mode": "direct", "host": "x.example", "port": 443, "grant_id": gid}
    out = await run(
        ToolCall("c3", "raw_probe", args),
        tools=services.harness.tools,
        ctx=_ctx(services, workdir),
        engine=PermissionEngine([]),
    )
    assert out.result.is_error  # non-interactive, no rule: the ask is not auto-granted


async def test_yolo_never_pre_grants_direct(services: Services, workdir: Path) -> None:
    make_tool(workdir / ".auoreo", "raw_probe2", "return 'probed'", DIRECT)
    services.harness.reload()
    args = json.dumps(
        {"url": "https://x.example/", "net_justification": "fetch the vendor status page directly"}
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "raw_probe2", args),
        tools=services.harness.tools,
        ctx=_ctx(services, workdir),
        engine=PermissionEngine([], mode="yolo"),
    )
    assert out.result.is_error and "probed" not in out.result.content


def test_justification_store_ttl(home: Path) -> None:
    digest = justifications.store(
        home, "g1", "because reasons that matter", kind="net_direct", tool="t", ttl_h=1
    )
    assert justifications.lookup(home, "g1")["hash"] == digest  # type: ignore[index]
    assert justifications.purge(home, now=time.time() + 2 * 3600) == 1
    assert justifications.lookup(home, "g1") is None
    assert (home / "egress" / "justifications.json").stat().st_mode & 0o777 == 0o600


def test_manifests_carry_net() -> None:
    assert (
        McpServerManifest(name="remote", transport="streamable-http", url="https://m.example").net == "proxy"
    )
    plain = ToolManifest(name="p", description="d")
    assert "net_justification" not in plain.parameters.get("properties", {})


# ------------------------------------------------------------------ sandbox wiring
def test_sandbox_proxy_wiring(tmp_path: Path, services: Services) -> None:
    from tests.unit.test_sandbox import backend_cls

    bw = sys.modules[backend_cls(services, "bwrap").__module__]
    with_port = bw.build_argv(
        ExecSpec(argv=["x"], cwd=tmp_path, net="proxy", proxy_port=4000), "bwrap", "/usr/bin/python3"
    )
    assert "--unshare-net" not in with_port
    cfg = json.loads(with_port[with_port.index("-c") + 2])
    assert cfg["net"] == "proxy" and cfg["proxy_port"] == 4000
    assert "--unshare-net" in bw.build_argv(ExecSpec(argv=["x"], cwd=tmp_path, net="proxy"), "bwrap")
    sb = sys.modules[backend_cls(services, "seatbelt").__module__]
    prof = sb.build_profile(ExecSpec(argv=["x"], cwd=tmp_path, net="proxy", proxy_port=4000))
    assert '(remote ip "localhost:4000")' in prof
    dk = sys.modules[backend_cls(services, "docker").__module__]
    kw = dk.run_kwargs(ExecSpec(argv=["x"], cwd=tmp_path, net="proxy"), "img", "1g", [])
    assert kw["network_mode"] == "none"
    assert "Landlock CONNECT_TCP" in proxy_confinement("proxy", 4000, 4)
    assert "not enforced" in proxy_confinement("proxy", 4000, 3)
    assert "no egress" in proxy_confinement("proxy", None, 7)


def test_exec_spec_picks_up_the_calls_proxy_port(tmp_path: Path) -> None:
    token = CURRENT_EXEC.set(ExecPolicy(net="proxy", proxy_url="http://auoreo:t@127.0.0.1:4567"))
    try:
        assert ExecSpec(argv=["x"], cwd=tmp_path, net="proxy").proxy_port == 4567
    finally:
        CURRENT_EXEC.reset(token)
    assert ExecSpec(argv=["x"], cwd=tmp_path).proxy_port is None


@pytest.mark.skipif(landlock.abi() < 4 or landlock.system_python() is None, reason="needs Landlock ABI >= 4")
def test_landlock_limits_tcp_to_the_proxy_port(tmp_path: Path) -> None:
    with socket.socket() as allowed, socket.socket() as other:
        for s in (allowed, other):
            s.bind(("127.0.0.1", 0))
            s.listen(1)
        a, o = allowed.getsockname()[1], other.getsockname()[1]
        probe = (
            "import socket,sys\n"
            "def t(p):\n"
            "    s=socket.socket()\n"
            "    try:\n        s.connect(('127.0.0.1',p)); return 'ok'\n"
            "    except OSError as e:\n        return 'denied:%d' % e.errno\n"
            f"print(t({a}), t({o}))\n"
        )
        argv = landlock.wrap(
            ["/usr/bin/python3", "-c", probe],
            cwd=tmp_path,
            mode="workspace-write",
            net="proxy",
            python=landlock.system_python(),
            proxy_port=a,
        )
        res = subprocess.run(argv, capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    first, second = res.stdout.split()
    assert first == "ok" and second.startswith("denied")
