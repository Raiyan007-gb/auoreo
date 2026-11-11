"""G5a: the machinery behind `auoreo run` flags (spec §18), kept out of `commands/run.py`.

- attachments (`-f`): text files are inlined as `<attachment>` blocks, images become image
  parts of the user message, small binaries travel base64-encoded, larger ones are named
  (I5); each file is capped at 10 MiB.
- session choice: `--session`, `--continue` (most recent), `--fork <id>` (run in a fork).
- `--allow/--deny <perm[:pattern]>`: session-layer permission rules (floors still win).
- `--auto`: asks are approved (never floors, never explicit denies).
- `--interactive`: a prompt loop; permission asks are answered at the terminal.
- `--print`: nothing streams; only the final answer text goes to stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.cli.common import err_console
from auoreo.cli.stdout_guard import StdoutGuard, write_line
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.harness.manifests import Rule
from auoreo.runtime.attachments import AttachmentError, Composed, compose
from auoreo.runtime.inbox import RunOptions

MAX_ATTACHMENT = 10 * 1024 * 1024
TERMINAL = {"run.completed", "run.failed", "run.blocked", "run.aborted", "run.max_steps"}
EXIT_WORDS = {"/exit", "/quit", ":q"}
REPLIES = {"y": "once", "yes": "once", "a": "always", "always": "always", "n": "reject", "no": "reject"}


class RunUsageError(ValueError):
    """A flag combination or input `run` refuses (exit 2)."""


# ------------------------------------------------------------------ inputs
def compose_attachments(prompt: str, files: list[Path], cfg: Any = None) -> Composed:
    """--- I5: text inlined, images as `image_url` parts, small binaries base64 (caps from
    `settings.i5.attachments`; `runtime/attachments.py`)."""
    try:
        return compose(prompt, files, cfg)
    except AttachmentError as exc:
        raise RunUsageError(str(exc)) from exc


def compose_prompt(prompt: str, files: list[Path]) -> str:
    return compose_attachments(prompt, files).text


def parse_rule(spec: str, action: str) -> Rule:
    """`bash:git *` → Rule(bash, "git *"); `read` → Rule(read, "*")."""
    perm, _, pattern = spec.partition(":")
    if not perm.strip():
        raise RunUsageError(f"bad rule '{spec}' (use <permission>[:<pattern>], e.g. 'bash:git *')")
    return Rule(permission=perm.strip(), pattern=pattern.strip() or "*", action=action)


@dataclass
class RunPlan:
    prompt: str
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None
    session: str | None = None
    fork: str | None = None
    cont: bool = False
    title: str | None = None
    fmt: str = "text"  # text | json | stream-json | print
    interactive: bool = False
    auto: bool = False
    rules: list[Rule] = field(default_factory=list)
    options: RunOptions | None = None
    share: bool = False  # --- H4: mint a read-only share link after the run

    @property
    def local_only(self) -> bool:
        """Flags only an in-process run can honour (not carried by `POST /api/chat`)."""
        return bool(self.rules or self.auto or self.options or self.interactive)


# ------------------------------------------------------------------ in-process
async def resolve_session(svc: Services, plan: RunPlan) -> str:
    from auoreo.core import sessions_ops as ops

    if plan.fork:
        return (await ops.fork_session(svc, plan.fork)).id
    if plan.cont:
        rows = await svc.list_sessions(1)
        if not rows:
            raise RunUsageError("--continue: there is no previous session")
        return rows[0].id
    if plan.session:
        if await svc.get_session(plan.session) is None:
            raise RunUsageError(f"unknown session '{plan.session}'")
        return plan.session
    row = await svc.create_session(
        title=plan.title, agent=plan.agent, workflow=plan.workflow, model=plan.model
    )
    return row.id


async def _install_rules(svc: Services, sid: str, rules: list[Rule]) -> None:
    """Pre-seed the session's engine: `AgentRunner._engine` keeps `session_rules`."""
    if not rules:
        return
    from auoreo.core.permissions import PermissionEngine

    rt = await svc.coordinator.runtime(sid)
    if rt.engine is None:
        rt.engine = PermissionEngine([], session_rules=list(rules))
    else:
        rt.engine.session_rules = [*rt.engine.session_rules, *rules]


async def _answer_permission(svc: Services, data: dict[str, Any], auto: bool) -> None:
    from auoreo.core.approvals import reply_with_grants
    from auoreo.teams.merge_flow import HUMAN_ONLY

    rid = str(data.get("request_id"))
    if auto and data.get("permission") not in HUMAN_ONLY:  # `--auto` never answers a merge card
        reply = "once"
    else:
        err_console.print(
            f"\n[yellow]? {data.get('tool')} wants {data.get('permission')} {data.get('pattern')}[/yellow]"
            f"\n  {str(data.get('detail', ''))[:300]}"
        )
        answer = await asyncio.to_thread(_ask, "  allow? [y]es once / [a]lways / [n]o: ")
        reply = REPLIES.get((answer or "").strip().lower(), "reject")  # EOF / anything else: reject
    await reply_with_grants(svc.approvals, svc.coordinator.engine_for, rid, reply)  # type: ignore[arg-type]


def _ask(prompt: str) -> str | None:
    """One line from stdin; None at end of input."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    return line.rstrip("\n") if line else None


async def _one_turn(svc: Services, sid: str, text: str, plan: RunPlan, pretty: Any) -> tuple[str, str | None]:
    sub = svc.bus.subscribe(None)
    try:
        run_id = await svc.coordinator.submit(
            sid,
            text,
            agent=plan.agent,
            workflow=plan.workflow,
            model=plan.model,
            interactive=plan.interactive,
            options=plan.options,
        )
        status = "completed"
        final: dict[str, Any] = {}
        while run_id is not None:  # None: an engine-handled slash command (no turn)
            ev = await sub.queue.get()
            mine = ev.aggregate_id == sid
            if plan.fmt == "stream-json":
                write_line(json.dumps(ev.model_dump(mode="json")))  # --- I5: real stdout only
            elif plan.fmt == "text" and mine:
                pretty(ev.type, ev.data)
            if mine and ev.type == "permission.requested" and plan.interactive:
                await _answer_permission(svc, ev.data, plan.auto)
            if mine and ev.type in TERMINAL and ev.data.get("run_id") == run_id:
                status = ev.type.split(".", 1)[1]
                final = ev.data
                break
    finally:
        svc.bus.unsubscribe(sub)
    await svc.coordinator.wait(sid, 5)
    await svc.coordinator.background_idle(10)
    result = svc.coordinator.result(run_id) if run_id else None
    text_out = result.text if result else str(final.get("text", ""))
    error = result.error if result else final.get("error")
    _report(plan, sid, run_id, status, text_out, error)
    return status, run_id


def _report(plan: RunPlan, sid: str, run_id: str | None, status: str, text: str, error: Any) -> None:
    if plan.fmt == "json":
        out = {"session_id": sid, "run_id": run_id, "status": status, "text": text, "error": error}
        print(json.dumps(out, indent=2), flush=True)
    elif plan.fmt == "print":
        print(text, flush=True)
        if status not in {"completed", "max_steps"}:
            err_console.print(f"[red]{status}: {error}[/red]")
    elif plan.fmt == "text":
        err_console.print(f"[dim]session {sid} · {status}[/dim]")


async def run_in_process(st: Settings, plan: RunPlan, pretty: Any) -> int:
    if plan.fmt != "stream-json":
        return await _run_in_process(st, plan, pretty)
    with StdoutGuard():  # --- I5: only JSON lines on stdout; everything else → stderr
        return await _run_in_process(st, plan, pretty)


async def _run_in_process(st: Settings, plan: RunPlan, pretty: Any) -> int:
    if plan.auto:  # unattended asks approve; floors and explicit denies still apply
        st.approvals.unattended.single_query = "approve"
    svc = await Services.create(st, cwd=Path.cwd(), with_mcp=True)
    try:
        sid = await resolve_session(svc, plan)
        await _install_rules(svc, sid, plan.rules)
        if plan.options and plan.options.variant:
            from auoreo.runtime.run_overrides import find_variant

            find_variant(svc.harness, plan.options.variant)  # fail before the turn starts
        text: str | None = plan.prompt
        status = "completed"
        while True:
            if text and text.strip():
                status, _ = await _one_turn(svc, sid, text, plan, pretty)
            if not plan.interactive:
                break
            text = await asyncio.to_thread(_ask, "> ")
            if text is None or text.strip() in EXIT_WORDS:
                break
        if plan.share:
            await _share_local(svc, st, sid)
        return 0 if status in {"completed", "max_steps"} else 1
    finally:
        await svc.close()


async def _share_local(svc: Services, st: Settings, sid: str) -> None:
    from auoreo.cli.target import local_url
    from auoreo.core.sessions_more import create_share

    token = await create_share(svc, sid)
    url = (st.cli.share_base_url or local_url(st)).rstrip("/") + f"/api/shared/{token}"
    err_console.print(f"share: {url}  [dim](read-only; anyone holding it can read this session)[/dim]")


# ------------------------------------------------------------------ --attach <url>
def run_attached(url: str, plan: RunPlan, pretty: Any) -> int:
    """Run against a live server: same events and output formats as in-process."""
    import httpx

    from auoreo.cli.target import client_for
    from auoreo.remote.cli_client import run_over_client

    try:
        with client_for(url, timeout=None) as c:
            sid = _remote_session(c, plan)
            code = run_over_client(
                c,
                url,
                plan.prompt,
                agent=plan.agent,
                workflow=plan.workflow,
                model=plan.model,
                session=sid,
                fmt=plan.fmt,
                pretty=pretty,
                note=lambda msg: err_console.print(f"[dim]{msg}[/dim]"),
                final_text=lambda text: print(text, flush=True),
            )
            if plan.share and sid:
                link = c.post(f"/api/sessions/{sid}/share").raise_for_status().json()
                err_console.print(f"share: {link['url']}  [dim](read-only)[/dim]")
            return code
    except httpx.HTTPError as exc:
        err_console.print(f"[red]--attach {url}: {exc}[/red]")
        return 1


def _remote_session(c: Any, plan: RunPlan) -> str | None:
    if plan.fork:
        return str(c.post(f"/api/sessions/{plan.fork}/fork").raise_for_status().json()["id"])
    if plan.cont:
        rows = c.get("/api/sessions", params={"limit": 1}).raise_for_status().json()
        if not rows:
            raise RunUsageError("--continue: there is no previous session on that server")
        return str(rows[0]["id"])
    if plan.session or not (plan.title or plan.share):  # --share needs a known session id
        return plan.session
    body = {"title": plan.title, "agent": plan.agent, "workflow": plan.workflow, "model": plan.model}
    created = c.post("/api/sessions", json={k: v for k, v in body.items() if v is not None})
    return str(created.raise_for_status().json()["id"])
