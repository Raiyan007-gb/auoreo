"""G4b: apply_patch V4A extras (stacked @@ anchors, End of File, fuzz, heredoc wrapper,
ordered hunks, appends) and LSP diagnostics-back (fail-open)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.tools.lsp_client import manager_for
from tests.unit.test_tools_m2 import call, ctx_for

SRC = "class A:\n    def run(self):\n        return 1\n\nclass B:\n    def run(self):\n        return 1\n"


def read(p: Path) -> str:
    return p.read_text()


def reset(p: Path) -> None:
    p.write_text(SRC)


def resolved(p: Path) -> Path:
    return p.resolve()


def patch(body: str, path: str = "m.py") -> str:
    return f"*** Begin Patch\n*** Update File: {path}\n{body}*** End Patch"


@pytest.fixture
def mod(workdir: Path) -> Path:
    f = workdir / "m.py"
    f.write_text(SRC)
    return f


async def test_stacked_headers_disambiguate(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    body = "@@ class B:\n@@     def run(self):\n-        return 1\n+        return 2\n"
    text, err = await call(services, ctx, "apply_patch", {"patch": patch(body)})
    assert not err, text
    assert read(mod) == SRC[: SRC.rindex("return 1")] + "return 2\n"


async def test_ambiguous_without_header_is_refused(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    text, err = await call(
        services, ctx, "apply_patch", {"patch": patch("-        return 1\n+        return 2\n")}
    )
    assert err and "matches 2 places" in text and read(mod) == SRC


async def test_hunks_apply_in_order(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    body = (
        "@@ class A:\n-        return 1\n+        return 10\n"
        "@@ class B:\n-        return 1\n+        return 20\n"
    )
    text, err = await call(services, ctx, "apply_patch", {"patch": patch(body)})
    assert not err, text
    assert "return 10" in read(mod) and "return 20" in read(mod)
    assert read(mod).index("return 10") < read(mod).index("return 20")


async def test_end_of_file_and_append(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    body = " class B:\n     def run(self):\n-        return 1\n+        return 3\n*** End of File\n"
    text, err = await call(services, ctx, "apply_patch", {"patch": patch(body)})
    assert not err, text
    assert read(mod).endswith("        return 3\n") and read(mod).count("return 1") == 1
    text, err = await call(services, ctx, "apply_patch", {"patch": patch("+\n+CONST = 5\n")})
    assert not err and read(mod).endswith("return 3\n\nCONST = 5\n")


async def test_whitespace_fuzz_and_blank_context(services: Services, workdir: Path) -> None:
    f = workdir / "w.py"
    f.write_text("def f():   \n    x = 1\n\n    return x\n")
    ctx = ctx_for(services, workdir)
    body = (
        " def f():\n-    x = 1\n+    x = 2\n\n     return x\n"  # trailing spaces differ; blank context line
    )
    text, err = await call(services, ctx, "apply_patch", {"patch": patch(body, "w.py")})
    assert not err and "trailing whitespace ignored" in text
    assert read(f) == "def f():\n    x = 2\n\n    return x\n"  # matched lines take the patch's text


async def test_unicode_punctuation_fuzz(services: Services, workdir: Path) -> None:
    f = workdir / "u.txt"
    f.write_text("say \u201chello\u201d \u2014 ok\nnext\n")
    ctx = ctx_for(services, workdir)
    text, err = await call(
        services, ctx, "apply_patch", {"patch": patch('-say "hello" - ok\n+bye\n', "u.txt")}
    )
    assert not err and "unicode punctuation" in text and read(f) == "bye\nnext\n"


async def test_heredoc_wrapper_and_trailing_blank(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    inner = patch("@@ class A:\n-        return 1\n+        return 7\n\n")
    text, err = await call(services, ctx, "apply_patch", {"patch": f"apply_patch <<'EOF'\n{inner}\nEOF\n"})
    assert not err, text
    assert "return 7" in read(mod)


async def test_add_after_delete_in_one_patch(services: Services, workdir: Path, mod: Path) -> None:
    ctx = ctx_for(services, workdir)
    p = "*** Begin Patch\n*** Delete File: m.py\n*** Add File: m.py\n+fresh\n*** End Patch"
    text, err = await call(services, ctx, "apply_patch", {"patch": p})
    assert not err and read(mod) == "fresh\n"


FAKE_LSP = r"""
import json, sys

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

def publish(uri, text):
    diags = [{"range": {"start": {"line": i, "character": 0}, "end": {"line": i, "character": 3}},
              "severity": 1, "message": "BAD is undefined", "code": "E1"}
             for i, line in enumerate(text.splitlines()) if "BAD" in line]
    diags.append({"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                  "severity": 3, "message": "just info"})
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "diagnostics": diags}})

while True:
    msg = read()
    m, mid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"capabilities": {"positionEncoding": "utf-16"}}})
    elif m == "textDocument/didOpen":
        publish(p["textDocument"]["uri"], p["textDocument"]["text"])
    elif m == "textDocument/didChange":
        publish(p["textDocument"]["uri"], p["contentChanges"][-1]["text"])
    elif m == "shutdown":
        send({"jsonrpc": "2.0", "id": mid, "result": None})
    elif m == "exit":
        sys.exit(0)
"""


@pytest.fixture
def fake_lsp(tmp_path: Path, services: Services, workdir: Path) -> None:
    script = tmp_path / "fake_lsp.py"
    script.write_text(FAKE_LSP)
    services.settings.lsp_servers = {"python": [sys.executable, str(script)]}
    (workdir / "pyproject.toml").write_text("[project]\nname='x'\n")


BAD_PATCH = patch("@@ class A:\n-        return 1\n+        return BAD\n")


async def test_diagnostics_back_from_a_running_server(
    services: Services, workdir: Path, mod: Path, fake_lsp: None
) -> None:
    ctx = ctx_for(services, workdir)
    text, err = await call(services, ctx, "apply_patch", {"patch": BAD_PATCH})
    assert not err and "LSP diagnostics" not in text  # `running` mode: no server started yet
    reset(mod)
    await manager_for(services).client_for("s1", "python", resolved(workdir), {})  # e.g. via the lsp tool
    text, err = await call(services, ctx, "apply_patch", {"patch": BAD_PATCH})
    assert not err, text
    assert "LSP diagnostics after the patch:\nm.py:3:1 error: BAD is undefined [E1]" in text
    assert "just info" not in text  # only errors and warnings
    await manager_for(services).close_session("s1")


async def test_diagnostics_start_mode_and_off(
    services: Services, workdir: Path, mod: Path, fake_lsp: None
) -> None:
    services.settings.tools.apply_patch_lsp = "start"
    ctx = ctx_for(services, workdir)
    text, err = await call(services, ctx, "apply_patch", {"patch": BAD_PATCH})
    assert not err and "BAD is undefined" in text
    await manager_for(services).close_session("s1")
    services.settings.tools.apply_patch_lsp = "off"
    reset(mod)
    text, err = await call(services, ctx, "apply_patch", {"patch": BAD_PATCH})
    assert not err and "LSP diagnostics" not in text


async def test_diagnostics_fail_open(services: Services, workdir: Path, mod: Path) -> None:
    services.settings.tools.apply_patch_lsp = "start"
    services.settings.lsp_servers = {"python": ["/nonexistent/lsp-server"]}
    (workdir / "pyproject.toml").write_text("[project]\nname='x'\n")
    ctx = ctx_for(services, workdir)
    text, err = await call(services, ctx, "apply_patch", {"patch": BAD_PATCH})
    assert not err and "return BAD" in read(mod) and "LSP diagnostics" not in text
