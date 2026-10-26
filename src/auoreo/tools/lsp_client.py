"""Read-only LSP 3.17 stdio client for the `lsp` tool (spec §2.6, §24.2, §25.15).

JSON-RPC 2.0 with `Content-Length` framing over a child's stdin/stdout. The client offers
`general.positionEncodings=["utf-16"]`; the tool API is 1-based line/column in code points
and is converted to/from 0-based UTF-16 code units here. One server per
(session, language, workspace root), cached on `Services` and closed when the session is
archived/deleted, after `IDLE_S` unused, or when the server shuts down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.tools.shell_sessions import SessionScoped, attached

IDLE_S = 10 * 60.0
INIT_TIMEOUT_S = 60.0

# language → candidate argv lists, first available wins (settings.lsp_servers overrides)
LSP_SERVERS: dict[str, list[list[str]]] = {
    "python": [
        ["ty", "server"],
        ["uvx", "--offline", "ty", "server"],  # cached tool; the scrubbed env has no proxy vars
        ["uvx", "ty", "server"],
        ["basedpyright-langserver", "--stdio"],
        ["uvx", "--from", "basedpyright", "basedpyright-langserver", "--stdio"],
    ],
    "typescript": [["typescript-language-server", "--stdio"]],
    "javascript": [["typescript-language-server", "--stdio"]],
    "rust": [["rust-analyzer"], ["rustup", "run", "stable", "rust-analyzer"]],
    "go": [["gopls"]],
}
REMEDY: dict[str, str] = {
    "python": "install a Python language server: `uv tool install ty` (or `uv tool install basedpyright`)",
    "typescript": "install it: `npm i -g typescript-language-server typescript`",
    "javascript": "install it: `npm i -g typescript-language-server typescript`",
    "rust": "install rust-analyzer: `rustup component add rust-analyzer` (or a release binary on PATH)",
    "go": "install gopls: `go install golang.org/x/tools/gopls@latest` (and put $GOPATH/bin on PATH)",
}
EXT_LANG: dict[str, tuple[str, str]] = {  # ext → (server language, LSP languageId)
    ".py": ("python", "python"),
    ".pyi": ("python", "python"),
    ".ts": ("typescript", "typescript"),
    ".mts": ("typescript", "typescript"),
    ".cts": ("typescript", "typescript"),
    ".tsx": ("typescript", "typescriptreact"),
    ".js": ("javascript", "javascript"),
    ".mjs": ("javascript", "javascript"),
    ".cjs": ("javascript", "javascript"),
    ".jsx": ("javascript", "javascriptreact"),
    ".rs": ("rust", "rust"),
    ".go": ("go", "go"),
}
_PY_MARKERS = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "pyrightconfig.json"]
_TS_LOCKS = ["package-lock.json", "bun.lockb", "bun.lock", "pnpm-lock.yaml", "yarn.lock"]
ROOT_MARKERS: dict[str, tuple[list[str], list[str]]] = {  # language → (include, exclude)
    "python": (_PY_MARKERS, []),
    "typescript": (_TS_LOCKS, ["deno.json", "deno.jsonc"]),
    "javascript": (_TS_LOCKS, ["deno.json", "deno.jsonc"]),
    "rust": (["Cargo.toml", "rust-project.json"], []),
    "go": (["go.work", "go.mod"], []),
}


class LspError(Exception):
    """Model-visible LSP failure."""


class LspUnavailable(LspError):
    """No usable server for this language (message names the remedy)."""


# ------------------------------------------------------------------ positions
def utf16_col(line: str, column: int) -> int:
    """1-based code-point column → 0-based UTF-16 code-unit offset."""
    prefix = line[: max(column - 1, 0)]
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in prefix)


def column_from_utf16(line: str, units: int) -> int:
    """0-based UTF-16 offset → 1-based code-point column (clamped to the line)."""
    seen = 0
    for i, ch in enumerate(line):
        if seen >= units:
            return i + 1
        seen += 2 if ord(ch) > 0xFFFF else 1
    return len(line) + 1


def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else Path(uri)


# ------------------------------------------------------------------ framing
def encode_message(obj: dict[str, Any]) -> bytes:
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """One framed message, or None at EOF."""
    length = -1
    while True:
        line = await reader.readline()
        if not line:
            return None
        text = line.decode("ascii", errors="replace").strip()
        if not text:
            if length >= 0:
                break
            continue
        name, _, value = text.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    body = await reader.readexactly(length)
    msg = json.loads(body.decode("utf-8"))
    return msg if isinstance(msg, dict) else None


def find_root(path: Path, language: str, stop: Path) -> Path | None:
    """NearestRoot(include, exclude): the closest ancestor holding an include marker, None
    when an exclude marker is closer (e.g. deno.json for typescript), else `stop`."""
    include, exclude = ROOT_MARKERS.get(language, ([], []))
    cur = path if path.is_dir() else path.parent
    while True:
        if any((cur / m).exists() for m in exclude):
            return None
        if any((cur / m).exists() for m in include):
            return cur
        if cur == stop or cur.parent == cur:
            return stop
        cur = cur.parent


# ------------------------------------------------------------------ client
def _client_capabilities() -> dict[str, Any]:
    links = {"linkSupport": True}
    return {
        "general": {"positionEncodings": ["utf-16"]},
        "textDocument": {
            "synchronization": {"didSave": False, "dynamicRegistration": False},
            "definition": links,
            "typeDefinition": links,
            "implementation": links,
            "references": {},
            "hover": {"contentFormat": ["plaintext", "markdown"]},
            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
            "callHierarchy": {},
            "publishDiagnostics": {"relatedInformation": False},
            "diagnostic": {"dynamicRegistration": False},
        },
        "workspace": {"symbol": {}, "configuration": True, "workspaceFolders": True},
        "window": {"workDoneProgress": False},
    }


@dataclass
class LspClient:
    argv: list[str]
    root: Path
    language: str
    timeout_s: float = 5.0
    env: dict[str, str] | None = None
    proc: asyncio.subprocess.Process | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    last_used: float = field(default_factory=time.monotonic)
    _next_id: int = 0
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict)
    _opened: dict[str, tuple[int, float]] = field(default_factory=dict)  # uri → (version, mtime)
    _diag_events: dict[str, asyncio.Event] = field(default_factory=dict)
    _reader: asyncio.Task[None] | None = None
    _stderr: bytearray = field(default_factory=bytearray)
    _stderr_task: asyncio.Task[None] | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=str(self.root),
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise LspUnavailable(f"cannot start {' '.join(self.argv)}: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop(), name=f"lsp:{self.language}")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name=f"lsp-err:{self.language}")
        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "auoreo"},
            "rootUri": path_to_uri(self.root),
            "rootPath": str(self.root),
            "workspaceFolders": [{"uri": path_to_uri(self.root), "name": self.root.name or "root"}],
            "capabilities": _client_capabilities(),
        }
        try:
            result = await self.request("initialize", params, timeout_s=max(self.timeout_s, INIT_TIMEOUT_S))
        except LspError as exc:
            tail = self._stderr[-600:].decode("utf-8", errors="replace").strip()
            await self.close()
            raise LspUnavailable(
                f"{' '.join(self.argv)} failed to initialize: {exc}. {tail}".strip()
            ) from exc
        self.capabilities = (result or {}).get("capabilities", {}) or {}
        enc = self.capabilities.get("positionEncoding", "utf-16")
        if enc != "utf-16":
            await self.close()
            raise LspUnavailable(f"{self.argv[0]} picked position encoding {enc!r}; only utf-16 is supported")
        await self.notify("initialized", {})

    async def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(4096):
            self._stderr += chunk
            del self._stderr[:-8192]

    async def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while (msg := await read_message(self.proc.stdout)) is not None:
                await self._dispatch(msg)
        except (asyncio.IncompleteReadError, ValueError, json.JSONDecodeError):
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(LspError(f"{self.argv[0]} exited"))
            self._pending.clear()

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if "id" in msg and "method" in msg:  # server → client request
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": self._answer(msg)})
            return
        if "id" in msg:
            fut = self._pending.pop(msg["id"], None)
            if fut is None or fut.done():
                return
            if "error" in msg:
                err = msg["error"] or {}
                fut.set_exception(LspError(f"{err.get('message', 'error')} (code {err.get('code')})"))
            else:
                fut.set_result(msg.get("result"))
            return
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri = str(params.get("uri", ""))
            self.diagnostics[uri] = list(params.get("diagnostics") or [])
            self._diag_events.setdefault(uri, asyncio.Event()).set()

    @staticmethod
    def _answer(msg: dict[str, Any]) -> Any:
        if msg.get("method") == "workspace/configuration":
            items = (msg.get("params") or {}).get("items") or []
            return [None for _ in items]
        if msg.get("method") == "workspace/workspaceFolders":
            return None
        return None  # registerCapability, workDoneProgress/create, showMessageRequest …

    async def _send(self, obj: dict[str, Any]) -> None:
        if not self.alive:
            raise LspError(f"{self.argv[0]} is not running")
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(encode_message(obj))
        try:
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise LspError(f"{self.argv[0]} closed its stdin") from exc

    async def request(self, method: str, params: Any, *, timeout_s: float | None = None) -> Any:
        self.last_used = time.monotonic()
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        wait = self.timeout_s if timeout_s is None else timeout_s
        try:
            return await asyncio.wait_for(fut, wait)
        except TimeoutError as exc:
            self._pending.pop(rid, None)
            with contextlib.suppress(LspError):
                await self.notify("$/cancelRequest", {"id": rid})
            raise LspError(f"{method} timed out after {int(wait * 1000)} ms") from exc

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def open(self, path: Path, language_id: str) -> str:
        """didOpen (or a full didChange when the file changed on disk); returns the URI."""
        uri = path_to_uri(path)
        mtime = (await asyncio.to_thread(path.stat)).st_mtime
        known = self._opened.get(uri)
        if known is not None and known[1] == mtime:
            return uri
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        if known is None:
            item = {"uri": uri, "languageId": language_id, "version": 1, "text": text}
            await self.notify("textDocument/didOpen", {"textDocument": item})
            self._opened[uri] = (1, mtime)
        else:
            version = known[0] + 1
            doc = {"uri": uri, "version": version}
            await self.notify(
                "textDocument/didChange", {"textDocument": doc, "contentChanges": [{"text": text}]}
            )
            self._opened[uri] = (version, mtime)
        return uri

    async def wait_diagnostics(self, uri: str, timeout_s: float) -> list[dict[str, Any]]:
        if uri not in self.diagnostics:
            ev = self._diag_events.setdefault(uri, asyncio.Event())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout_s)
        return self.diagnostics.get(uri, [])

    async def close(self) -> None:
        if self.alive:
            with contextlib.suppress(LspError, OSError):
                await self.request("shutdown", None, timeout_s=2.0)
                await self.notify("exit", None)
            assert self.proc is not None
            try:
                await asyncio.wait_for(self.proc.wait(), 2.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
                await self.proc.wait()
        for task in (self._reader, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


# ------------------------------------------------------------------ manager
def candidates(language: str, overrides: dict[str, list[str]], root: Path) -> list[list[str]]:
    if language in overrides and overrides[language]:
        return [list(overrides[language])]
    out = []
    for argv in LSP_SERVERS.get(language, []):
        local = root / "node_modules" / ".bin" / argv[0]
        if local.is_file():
            out.append([str(local), *argv[1:]])
        elif shutil.which(argv[0]):
            out.append(argv)
    return out


class LspManager(SessionScoped):
    def __init__(self, services: Any, timeout_ms: int = 5000, overrides: dict[str, list[str]] | None = None):
        super().__init__(services)
        self.timeout_s = timeout_ms / 1000
        self.overrides = overrides or {}
        self.clients: dict[tuple[str, str, str], LspClient] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def client_for(self, session_id: str, language: str, root: Path, env: dict[str, str]) -> LspClient:
        self.ensure_tasks()
        key = (session_id, language, str(root))
        async with self._locks.setdefault(key, asyncio.Lock()):
            client = self.clients.get(key)
            if client is not None and client.alive:
                return client
            argvs = candidates(language, self.overrides, root)
            if not argvs:
                raise LspUnavailable(f"no {language} language server found on PATH; {REMEDY[language]}")
            errors = []
            for argv in argvs:
                client = LspClient(argv, root, language, self.timeout_s, env)
                try:
                    await client.start()
                except LspUnavailable as exc:
                    errors.append(str(exc))
                    continue
                self.clients[key] = client
                return client
            raise LspUnavailable(f"{'; '.join(errors)[:1500]}. Remedy: {REMEDY[language]}")

    async def close_session(self, session_id: str) -> None:
        for key in [k for k in self.clients if k[0] == session_id]:
            await self.clients.pop(key).close()

    async def reap_idle(self) -> None:
        now = time.monotonic()
        for key, client in list(self.clients.items()):
            if now - client.last_used > IDLE_S or not client.alive:
                await self.clients.pop(key).close()

    async def close_all(self) -> None:
        for key in list(self.clients):
            await self.clients.pop(key).close()


def manager_for(services: Any) -> LspManager:
    settings = getattr(services, "settings", None)
    timeout = int(getattr(settings, "lsp_timeout_ms", 5000) or 5000)
    overrides = dict(getattr(settings, "lsp_servers", {}) or {})
    mgr = attached(services, "lsp", lambda svc: LspManager(svc, timeout, overrides))
    mgr.timeout_s, mgr.overrides = timeout / 1000, overrides  # settings may change at runtime
    for client in mgr.clients.values():
        client.timeout_s = mgr.timeout_s
    return mgr
