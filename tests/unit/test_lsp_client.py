"""LSP client: framing, UTF-16 positions, a fake stdio server driving the `lsp` tool,
timeouts, missing-server errors, git-ignore filtering, and a real `ty server` run."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext
from auoreo.tools.lsp_client import (
    column_from_utf16,
    encode_message,
    find_root,
    manager_for,
    read_message,
    utf16_col,
)

FAKE_SERVER = r"""
import json, sys, time

def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        k, _, v = line.decode().partition(":")
        if k.lower() == "content-length":
            length = int(v)
    return json.loads(sys.stdin.buffer.read(length))

def send(obj):
    body = json.dumps(obj).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()

log = open(sys.argv[1], "a")
opened = {}
while True:
    msg = read()
    log.write(json.dumps(msg) + "\n"); log.flush()
    m, mid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if m == "initialize":
        assert p["capabilities"]["general"]["positionEncodings"] == ["utf-16"]
        send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {
            "positionEncoding": "utf-16", "definitionProvider": True, "referencesProvider": True,
            "hoverProvider": True, "documentSymbolProvider": True}}})
        # a server -> client request the client must answer
        send({"jsonrpc": "2.0", "id": 900, "method": "workspace/configuration",
              "params": {"items": [{"section": "x"}]}})
    elif m == "textDocument/didOpen":
        opened[p["textDocument"]["uri"]] = p["textDocument"]["text"]
    elif m == "textDocument/definition":
        uri, pos = p["textDocument"]["uri"], p["position"]
        # echo the requested UTF-16 position back as a LocationLink on line 1
        send({"jsonrpc": "2.0", "id": mid, "result": [{"targetUri": uri,
              "targetRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
              "targetSelectionRange": {"start": {"line": 0, "character": pos["character"]},
                                       "end": {"line": 0, "character": pos["character"] + 1}}}]})
    elif m == "textDocument/references":
        uri = p["textDocument"]["uri"]
        base = uri.rsplit("/", 1)[0]
        rng = {"start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 7}}
        send({"jsonrpc": "2.0", "id": mid, "result": [
            {"uri": uri, "range": rng},
            {"uri": base + "/ignored/gen.py", "range": rng}]})
    elif m == "textDocument/hover":
        if "slow" in p["textDocument"]["uri"]:
            time.sleep(3)
        send({"jsonrpc": "2.0", "id": mid, "result": {"contents": {"kind": "markdown",
              "value": "def greet(name: str) -> str"}}})
    elif m == "textDocument/documentSymbol":
        r = lambda a, b: {"start": {"line": a, "character": 0}, "end": {"line": b, "character": 0}}
        send({"jsonrpc": "2.0", "id": mid, "result": [
            {"name": "Greeter", "kind": 5, "range": r(3, 6), "selectionRange": r(3, 3), "children": [
                {"name": "hi", "kind": 6, "range": r(4, 5), "selectionRange": r(4, 4)}]}]})
    elif m == "shutdown":
        send({"jsonrpc": "2.0", "id": mid, "result": None})
    elif m == "exit":
        sys.exit(0)
"""

SOURCE = (
    "s = '😀😀'; greet = 1\ndef greet(name):\n    return name\n"
    "class Greeter:\n    def hi(self):\n        pass\n"
)


@pytest.fixture
def fake_server(tmp_path: Path, services: Services) -> Path:
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_SERVER)
    log = tmp_path / "lsp.log"
    services.settings.lsp_servers = {"python": [sys.executable, str(script), str(log)]}
    return log


async def lsp(services: Services, workdir: Path, args: dict, session: str = "s1") -> tuple[str, bool]:
    ctx = ToolContext(session_id=session, cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "lsp", json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    return out.result.content, out.result.is_error


# ------------------------------------------------------------------ pure helpers
@pytest.mark.parametrize(
    ("line", "column", "units"),
    [
        ("abc", 1, 0),
        ("abc", 3, 2),
        ("😀x", 2, 2),  # emoji = 2 UTF-16 code units
        ("😀😀x", 3, 4),
        ("é😀b", 3, 3),  # é is one unit (BMP)
        ("中文x", 3, 2),
        ("abc", 10, 3),  # past the end clamps to the line
    ],
)
def test_utf16_conversion(line: str, column: int, units: int) -> None:
    assert utf16_col(line, column) == units
    if column <= len(line) + 1:
        assert column_from_utf16(line, units) == column


def test_column_from_utf16_clamps() -> None:
    assert column_from_utf16("ab", 99) == 3
    assert column_from_utf16("", 0) == 1


async def test_framing_round_trip() -> None:
    reader = asyncio.StreamReader()
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"text": "héllo 😀"}}
    reader.feed_data(encode_message(msg))
    reader.feed_data(
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n" + encode_message({"a": 1})
    )
    reader.feed_eof()
    assert await read_message(reader) == msg
    assert await read_message(reader) == {"a": 1}
    assert await read_message(reader) is None
    framed = encode_message({"x": "😀"})
    head, body = framed.split(b"\r\n\r\n", 1)
    assert head == f"Content-Length: {len(body)}".encode()  # bytes, not characters


def test_find_root(tmp_path: Path) -> None:
    (tmp_path / "pkg" / "src").mkdir(parents=True)
    (tmp_path / "pkg" / "pyproject.toml").write_text("")
    f = tmp_path / "pkg" / "src" / "a.py"
    f.write_text("")
    assert find_root(f, "python", tmp_path) == tmp_path / "pkg"
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "deno.json").write_text("{}")
    assert find_root(tmp_path / "web", "typescript", tmp_path) is None
    assert find_root(tmp_path / "pkg", "typescript", tmp_path) == tmp_path  # no lockfile → stop


# ------------------------------------------------------------------ fake server via the tool
def test_manifest(services: Services) -> None:
    m = services.harness.tools["lsp"].manifest
    assert m.exposure == "deferred" and m.is_read_only and m.is_concurrency_safe
    assert m.permission_name == "read" and m.default_action == "allow"
    assert len(m.parameters["properties"]["op"]["enum"]) == 9


async def test_fake_server_ops(services: Services, workdir: Path, fake_server: Path) -> None:
    (workdir / "app.py").write_text(SOURCE, encoding="utf-8")
    # column 14 is the `g` of `greet` after two emoji → UTF-16 offset 15
    text, err = await lsp(services, workdir, {"op": "definition", "path": "app.py", "line": 1, "column": 14})
    assert not err and text.startswith("app.py:1:14  s = '😀😀'; greet = 1"), text
    sent = [json.loads(ln) for ln in fake_server.read_text().splitlines()]  # noqa: ASYNC240
    req = next(m for m in sent if m.get("method") == "textDocument/definition")
    assert req["params"]["position"] == {"line": 0, "character": 15}
    assert any(m.get("method") == "textDocument/didOpen" for m in sent)
    assert any(m.get("method") == "initialized" for m in sent)
    assert any(m.get("id") == 900 and m.get("result") == [None] for m in sent)  # config answered

    text, err = await lsp(services, workdir, {"op": "references", "path": "app.py", "line": 2, "column": 5})
    assert not err and "app.py:2:5  def greet(name):" in text and "ignored/gen.py:2:5" in text

    text, err = await lsp(services, workdir, {"op": "hover", "path": "app.py", "line": 2, "column": 5})
    assert not err and text == "def greet(name: str) -> str"

    text, err = await lsp(services, workdir, {"op": "document_symbols", "path": "app.py"})
    assert not err and text.splitlines() == ["class Greeter  app.py:4:1-7", "  method hi  app.py:5:1-6"]

    text, err = await lsp(
        services, workdir, {"op": "implementation", "path": "app.py", "line": 2, "column": 5}
    )
    assert err and "does not support implementation" in text
    text, err = await lsp(services, workdir, {"op": "definition", "path": "app.py"})
    assert err and "needs line and column" in text
    # one server per (session, language, root): a second session gets its own process
    mgr = manager_for(services)
    assert len(mgr.clients) == 1
    await lsp(services, workdir, {"op": "hover", "path": "app.py", "line": 2, "column": 5}, session="s2")
    assert len(mgr.clients) == 2
    await mgr.close_session("s2")
    assert len(mgr.clients) == 1


async def test_git_ignored_results_are_filtered(services: Services, workdir: Path, fake_server: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git missing")
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)  # noqa: ASYNC221
    (workdir / ".gitignore").write_text("ignored/\n")
    (workdir / "ignored").mkdir()
    (workdir / "ignored" / "gen.py").write_text("x\n    foo\n")
    (workdir / "app.py").write_text(SOURCE, encoding="utf-8")
    text, err = await lsp(services, workdir, {"op": "references", "path": "app.py", "line": 2, "column": 5})
    assert not err and "app.py:2:5" in text and "ignored/" not in text


async def test_timeout(services: Services, workdir: Path, fake_server: Path) -> None:
    services.settings.lsp_timeout_ms = 300
    (workdir / "slow.py").write_text(SOURCE, encoding="utf-8")
    text, err = await lsp(services, workdir, {"op": "hover", "path": "slow.py", "line": 2, "column": 5})
    assert err and "timed out after 300 ms" in text


async def test_missing_server_errors(services: Services, workdir: Path) -> None:
    (workdir / "a.py").write_text("x = 1\n")
    services.settings.lsp_servers = {"python": ["/nonexistent/ty-server"]}
    text, err = await lsp(services, workdir, {"op": "document_symbols", "path": "a.py"})
    assert err and "uv tool install ty" in text
    (workdir / "a.rb").write_text("x = 1\n")
    text, err = await lsp(services, workdir, {"op": "document_symbols", "path": "a.rb"})
    assert err and "no language server for '.rb'" in text
    if shutil.which("typescript-language-server") is None:
        (workdir / "a.ts").write_text("export const x = 1\n")
        text, err = await lsp(services, workdir, {"op": "document_symbols", "path": "a.ts"})
        assert err and "npm i -g typescript-language-server typescript" in text


async def test_session_archive_closes_servers(services: Services, workdir: Path, fake_server: Path) -> None:
    (workdir / "app.py").write_text(SOURCE, encoding="utf-8")
    await lsp(services, workdir, {"op": "hover", "path": "app.py", "line": 2, "column": 5})
    mgr = manager_for(services)
    (client,) = mgr.clients.values()
    await services.emit("s1", "session.archived", {})
    for _ in range(100):
        if not mgr.clients:
            break
        await asyncio.sleep(0.02)
    assert not mgr.clients and not client.alive


# ------------------------------------------------------------------ real server
def _ty_available() -> bool:
    if shutil.which("uvx") is None:
        return False
    try:
        r = subprocess.run(["uvx", "--offline", "ty", "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


@pytest.mark.skipif(not _ty_available(), reason="`uvx ty` is not available (offline / not cached)")
async def test_real_ty_server(services: Services, workdir: Path) -> None:
    (workdir / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0"\n')
    (workdir / "lib.py").write_text("def greet(name: str) -> str:\n    return name\n")
    (workdir / "main.py").write_text("from lib import greet\n\nprint(greet('x'))\nundefined_name\n")
    services.settings.lsp_timeout_ms = 30000
    text, err = await lsp(services, workdir, {"op": "definition", "path": "main.py", "line": 3, "column": 8})
    assert not err and text.startswith("lib.py:1:5  def greet(name: str) -> str:"), text
    text, err = await lsp(services, workdir, {"op": "references", "path": "lib.py", "line": 1, "column": 5})
    assert not err and "main.py:3:7" in text, text
    text, err = await lsp(services, workdir, {"op": "hover", "path": "main.py", "line": 3, "column": 8})
    assert not err and "str" in text
    text, err = await lsp(services, workdir, {"op": "diagnostics", "path": "main.py"})
    assert not err and "main.py:4:1" in text and "undefined_name" in text, text
    text, err = await lsp(services, workdir, {"op": "workspace_symbols", "query": "greet"})
    assert not err and "lib.py:1" in text, text
