"""Per-path write locks (spec §9.4: `asyncio.Semaphore(1)` per path for edit/write/patch).

`touched_paths(tool, args, ctx)` names the files a writer will change: the handler
module's optional `touched_paths(args) -> list[str]`, else the file headers of a `patch`
argument (`*** Add/Update/Delete File:`, `*** Move to:`, unified `---`/`+++`), else a
`path` pattern argument. `PathLocks.hold(paths)` takes every lock in sorted order, so two
multi-file patches can never deadlock on each other.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import weakref
from asyncio import Semaphore
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)
_UNIFIED = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


def patch_paths(patch: str) -> list[str]:
    found = [a or b for a, b in _PATCH_FILE.findall(patch)]
    if not found:
        found = [p for p in _UNIFIED.findall(patch) if p != "/dev/null"]
    return found


def _module_hook(tool: ToolDef) -> Any:
    if tool.path is None:
        return None
    from auoreo.harness.loader import LoadError

    try:
        return getattr(inspect.getmodule(tool.handler()), "touched_paths", None)
    except LoadError:
        return None


def touched_paths(tool: ToolDef, args: dict[str, Any], ctx: ToolContext) -> list[str]:
    m = tool.manifest
    if m.is_read_only:
        return []
    raw: list[str] = []
    hook = _module_hook(tool)
    if callable(hook):
        with contextlib.suppress(Exception):
            raw = [str(p) for p in hook(args) or []]
    elif isinstance(args.get("patch"), str):
        raw = patch_paths(args["patch"])
    elif m.pattern_arg == "path" and isinstance(args.get("path"), str):
        raw = [args["path"]]
    return sorted({str(ctx.resolve_path(p)) for p in raw if p})


class PathLocks:
    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, Semaphore] = weakref.WeakValueDictionary()

    def _get(self, key: str) -> Semaphore:
        sem = self._locks.get(key)
        if sem is None:
            sem = Semaphore(1)
            self._locks[key] = sem
        return sem

    def locked(self, key: str) -> bool:
        sem = self._locks.get(key)
        return sem is not None and sem.locked()

    @contextlib.asynccontextmanager
    async def hold(self, paths: list[str]) -> AsyncIterator[None]:
        held: list[Semaphore] = []
        try:
            for key in sorted(set(paths)):
                sem = self._get(key)
                await sem.acquire()
                held.append(sem)
            yield
        finally:
            for sem in reversed(held):
                sem.release()
