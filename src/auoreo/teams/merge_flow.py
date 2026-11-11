"""M7 W4 Wave 2: `git_merge`, the reviewed way back from a worker branch (`m7.orch.merge`).

The approval lives here, not in the executor: the executor's own `ask` can be granted by a
rule, a mode (`yolo`), a `PermissionRequest` hook, the smart-mode guardian or a remembered
`always`, and it shows only the JSON arguments. This flow computes the facts first, then asks
the human through `ApprovalBroker.request(one_shot=True)` (an `always` reply counts as `once`),
and binds the answer to (repository, source sha, target branch and HEAD, `--no-ff`):

1. refuse unless the flag is on, the call is interactive, the session runs in the server cwd
   and the services and approval broker exist (fail closed);
2. under the per-repository worktree lock: target preflight (on a branch, clean, nothing in
   progress); worker objects imported; the branch tip equals `sha`; `sha` not yet merged; the
   recorded base is an ancestor (D3); the branch's worktree is clean and its task not claimed;
3. definitions (`m7.orch.merge_definitions`), object re-hash (D5), dry run (conflicts → one
   `blocked` kanban task, `merge.conflicts`), portability (D7);
4. the card (with D2 recorded tip, D4 sibling-shared commits, sandbox state), the human;
5. after `once`: tip, HEAD and cleanliness again, then the merge of the sha (hooks off).

Every refusal emits `merge.denied{reason}` and changes nothing.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.runtime import merge as mg
from auoreo.runtime import worktree_git as wg
from auoreo.runtime import worktrees as wt
from auoreo.runtime.cwd import worktree_session
from auoreo.teams import merge_card, merge_records
from auoreo.tools.context import ToolContext, ToolError, ToolResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

PERMISSION = "git_merge"
HUMAN_ONLY = frozenset({PERMISSION})
"""Permissions whose cards only a person answers (the CLI's `--auto` never replies to them)."""


def refuse_early(ctx: ToolContext) -> Services:
    svc: Services | None = ctx.services.get("services")
    if svc is None or getattr(svc, "approvals", None) is None:
        raise ToolError("git_merge needs the server's approval channel; refused")
    if not svc.settings.m7.orch.merge:
        raise ToolError("git_merge is disabled (m7.orch.merge)")
    if not ctx.interactive:
        raise ToolError("git_merge always needs a human approval and this run is non-interactive")
    if worktree_session(ctx):
        raise ToolError("git_merge runs only in a session of the server checkout, not in a worktree")
    return svc


@dataclass
class Flow:
    svc: Services
    ctx: ToolContext
    branch: str
    sha: str
    note: str | None = None
    rec: merge_records.Record | None = None
    top: Path = field(default_factory=Path)

    @property
    def task_ref(self) -> str | None:
        return self.rec.task_ref if self.rec else None

    def _base(self) -> dict[str, Any]:
        out: dict[str, Any] = {"branch": self.branch, "sha": self.sha}
        if self.task_ref:
            out["task_ref"] = self.task_ref
        return out

    async def _emit(self, kind: str, data: dict[str, Any], *, aggregate: str | None = None) -> None:
        await self.svc.emit(aggregate or self.ctx.session_id, kind, data)

    async def _audit(self, action: str, data: dict[str, Any], decision: str = "", reason: str = "") -> None:
        await self.svc.audit.record(
            actor="human" if action in ("merge.approved", "merge.rejected") else "agent",
            action=action,
            session_id=self.ctx.session_id,
            decision=decision,
            reason=reason,
            detail=data,
        )

    async def denied(self, reason: str, message: str) -> ToolResult:
        data = {**self._base(), "reason": reason}
        await self._emit("merge.denied", data)
        await self._audit("merge.denied", data, "deny", reason)
        return ToolResult(f"git_merge refused ({reason}): {message}. Nothing was changed.", is_error=True)

    # ------------------------------------------------------------ steps
    async def run(self) -> ToolResult:
        try:
            self.top = await mg.toplevel(self.svc.cwd)
            if not await mg.git_version_ok(self.top):
                raise mg.MergeRefused(
                    "git_too_old", "git_merge needs git 2.38 or newer (merge-tree --write-tree)"
                )
            self.rec = await merge_records.created_for(self.svc, self.branch)
            if self.rec is None:
                raise mg.MergeRefused(
                    "unrecorded",
                    f"there is no record of the harness creating {self.branch}; merge it by hand",
                )
            async with wt._LOCKS.setdefault(str(self.top), asyncio.Lock()):
                return await self._locked()
        except mg.MergeRefused as exc:
            return await self.denied(exc.reason, str(exc))

    async def _locked(self) -> ToolResult:
        assert self.rec is not None
        tgt = await mg.target(self.top)
        await self._import_all()
        await self._source_checks(tgt)
        changes = await mg.changes(self.top, tgt.head, self.sha)
        defs = mg.definitions([c.path for c in changes])
        if defs and self.svc.settings.m7.orch.merge_definitions == "refuse":
            raise mg.MergeRefused(
                "definitions",
                f"the merge changes definitions or repository behaviour ({', '.join(defs[:10])}); "
                "merge it by hand after review, or set m7.orch.merge_definitions=warn",
            )
        objects = await mg.rehash(self.top, tgt.head, self.sha, self.svc.settings.m7.orch.merge_max_objects)
        dry = await mg.dry_run(self.top, tgt.head, self.sha)
        if not dry.clean:
            return await self._conflicts(tgt, dry.conflicts)
        if problems := await mg.unportable(self.top, dry.tree, changes):
            raise mg.MergeRefused("unportable", "; ".join(problems[:10]))
        facts = await self._facts(tgt, changes, defs, objects)
        return await self._ask_and_merge(tgt, merge_card.render(facts))

    async def _import_all(self) -> None:
        """Worker commits may live only in their private stores: import them (verified by
        `index-pack`) before any branch read. The source's import must succeed."""
        listing = await wt._ok("worktree", "list", "--porcelain", cwd=self.top) or ""
        for e in wt._entries(listing):
            if not e.get("branch", "").startswith("refs/heads/auoreo/"):
                continue
            ok = await wg.import_objects(self.top, Path(e["worktree"]))
            if not ok and e["branch"] == f"refs/heads/{self.branch}":
                raise mg.MergeRefused("import_failed", "the worker's commits could not be imported")

    async def _source_checks(self, tgt: mg.Target) -> None:
        assert self.rec is not None
        tip = await mg.tip(self.top, self.branch)
        if tip is None:
            raise mg.MergeRefused("branch_missing", f"{self.branch} does not exist")
        if tip != self.sha:
            raise mg.MergeRefused(
                "sha_mismatch",
                f"{self.branch} points at {tip}, not the reviewed {self.sha}; review the new tip",
            )
        if await mg.is_ancestor(self.top, self.sha, tgt.head):
            raise mg.MergeRefused("already_merged", f"{self.sha[:12]} is already in {tgt.branch}")
        base = self.rec.base
        if not re.fullmatch(r"[0-9a-f]{7,64}", base):
            raise mg.MergeRefused("foreign_base", f"recorded base '{base[:40]}' is not a commit id")
        if not await mg.is_ancestor(self.top, base, self.sha):
            raise mg.MergeRefused(
                "foreign_base",
                f"{self.sha[:12]} does not descend from the branch's recorded base {base[:12]}",
            )
        listing = await wt._ok("worktree", "list", "--porcelain", cwd=self.top) or ""
        for e in wt._entries(listing):
            if e.get("branch") == f"refs/heads/{self.branch}":
                status = await wg.tree_ok(
                    self.top, Path(e["worktree"]), "status", "--porcelain", "--ignore-submodules=all"
                )
                if status != "":
                    raise mg.MergeRefused(
                        "source_dirty", f"the worktree of {self.branch} has uncommitted changes"
                    )
        if (kanban := self.rec.kanban) is not None and (teams := self.svc.ext.get("teams")) is not None:
            try:
                row = await teams.kanban.get(kanban[1])
            except ValueError:
                return
            if row.status == "claimed":
                raise mg.MergeRefused("task_running", f"kanban task {kanban[1]} is still claimed by a worker")

    async def _facts(
        self, tgt: mg.Target, changes: list[mg.Change], defs: list[str], objects: int
    ) -> merge_card.CardFacts:
        assert self.rec is not None
        tips = await merge_records.recorded_tips(self.svc, self.branch)
        if not tips:
            tip_note = "no record"
        else:
            tip_note = "matches" if tips[-1] == self.sha else f"differs (was {tips[-1][:12]})"
        if self.sha not in tips:
            payload = await merge_records.review_payload(self.top, self.rec, self.sha)
            await self._emit("review.requested", payload, aggregate=self.rec.session_id or None)
        boot = self.svc.ext.get("sandbox_boot")
        return merge_card.CardFacts(
            branch=self.branch,
            sha=self.sha,
            subject=await mg.subject(self.top, self.sha),
            commits=len(await mg.commits(self.top, tgt.head, self.sha)),
            changes=changes,
            target=tgt.branch,
            head=tgt.head,
            base=self.rec.base,
            task_ref=self.task_ref,
            worker=self.rec.session_id,
            tip_note=tip_note,
            objects=objects,
            sandbox="enforced" if boot is not None and boot.enforced else "not enforced",
            shared=await mg.shared_with(self.top, tgt.head, self.sha, self.branch),
            definitions=defs,
            note=self.note,
        )

    async def _ask_and_merge(self, tgt: mg.Target, card: str) -> ToolResult:
        pattern = f"{self.branch}@{self.sha}->{tgt.branch}@{tgt.head}"
        outcome = await self.svc.approvals.request(
            self.ctx.session_id,
            PERMISSION,
            PERMISSION,
            pattern,
            card,
            cwd=str(self.top),
            risk="high",
            one_shot=True,
        )
        decision = {
            **self._base(),
            "target": tgt.branch,
            "target_sha": tgt.head,
            "request_id": outcome.request_id,
        }
        if not outcome.granted:
            await self._emit("review.rejected", decision)
            await self._audit("merge.rejected", decision, outcome.reply or "timeout")
            why = "the approval timed out" if outcome.timed_out else "the user rejected the merge"
            extra = f" with feedback: {outcome.message}" if outcome.message else ""
            return ToolResult(f"git_merge: {why}{extra}. Nothing was changed.", is_error=True)
        await self._emit("review.approved", decision)
        await self._audit("merge.approved", decision, "once")
        try:
            again = await mg.target(self.top)
        except mg.MergeRefused as exc:
            raise mg.MergeRefused("moved_after_approval", f"after the approval: {exc}") from exc
        tip = await mg.tip(self.top, self.branch)
        if (again.branch, again.head, tip) != (tgt.branch, tgt.head, self.sha):
            raise mg.MergeRefused(
                "moved_after_approval",
                f"the branch or the target moved after the card was shown ({self.branch} at {tip}, "
                f"{again.branch} at {again.head[:12]}); call git_merge again to review the new state",
            )
        return await self._merge(tgt)

    async def _merge(self, tgt: mg.Target) -> ToolResult:
        message = f"Merge {self.branch} ({self.sha[:12]})"
        if self.task_ref:
            message += f"\n\nAuoreo-Task: {self.task_ref}"
        try:
            merged = await mg.merge(self.top, self.sha, message)
        except mg.MergeFailedError as exc:
            data = {**self._base(), "message": str(exc)[:500]}
            await self._emit("merge.failed", data)
            await self._audit("merge.failed", data, "error")
            return ToolResult(f"git_merge failed and was aborted: {exc}", is_error=True)
        data = {**self._base(), "target": tgt.branch, "target_sha": tgt.head, "merge_sha": merged}
        await self._emit("merge.completed", data)
        await self._audit("merge.completed", data, "allow")
        return ToolResult(
            f"merged {self.branch} at {self.sha[:12]} into {tgt.branch}: merge commit {merged}",
            meta={"merge_sha": merged, "target": tgt.branch},
        )

    async def _conflicts(self, tgt: mg.Target, files: list[str]) -> ToolResult:
        assert self.rec is not None
        task = await merge_records.conflict_task(
            self.svc, self.rec, sha=self.sha, head=tgt.head, target=tgt.branch, files=files
        )
        data = {
            **self._base(),
            "target": tgt.branch,
            "target_sha": tgt.head,
            "files": [merge_card.clean(f, 300) for f in files[:50]],
            "conflict_task": task,
        }
        await self._emit("merge.conflicts", data)
        where = f"; kanban task #{task} (blocked) tracks the resolution" if task is not None else ""
        shown = ", ".join(merge_card.clean(f, 120) for f in files[:20])
        return ToolResult(
            f"git_merge: {self.branch} conflicts with {tgt.branch} in {shown}{where}. The checkout was not "
            "touched; resolve in the branch's worktree, then call git_merge with the new tip.",
            is_error=True,
        )


async def git_merge(ctx: ToolContext, branch: str, sha: str, note: str | None = None) -> ToolResult:
    svc = refuse_early(ctx)
    return await Flow(svc, ctx, branch, sha, note).run()


def available() -> bool | str:
    """`check_fn` of the tool: hidden while `m7.orch.merge` is off (the handler checks again)."""
    from auoreo.core.settings import get_settings

    return True if get_settings().m7.orch.merge else "m7.orch.merge is off"
