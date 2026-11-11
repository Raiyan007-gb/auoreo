"""M7 W4 Wave 2: the `git_merge` approval card (plain text, fixed line order, <= 2000 chars).

Commit subjects, file names and the agent's note come from the branch or the model and are
untrusted: each is put on one line, stripped of control characters, truncated and redacted.
The card never carries a diff body, so no file content (and no secret in it) reaches the
`permission.requested` event or the audit log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from auoreo.core.redaction import redact
from auoreo.runtime.merge import Change

CAP = 2000
LINE = 160
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def clean(text: str, limit: int = LINE) -> str:
    """One line of untrusted text: no control or bidi characters, truncated, redacted."""
    one = _CONTROL.sub(" ", text).strip()
    one = one if len(one) <= limit else one[: limit - 1] + "…"
    return redact(one)


@dataclass
class CardFacts:
    branch: str
    sha: str
    subject: str
    commits: int
    changes: list[Change]
    target: str
    head: str
    base: str
    task_ref: str | None
    worker: str | None
    tip_note: str  # "matches" | "differs (was …)" | "no record"
    objects: int
    sandbox: str  # "enforced" | "not enforced"
    shared: dict[str, int] = field(default_factory=dict)
    definitions: list[str] = field(default_factory=list)
    note: str | None = None


def _stat(c: Change) -> str:
    if c.added is None or c.deleted is None:
        return f"{clean(c.path, 80)} (binary)"
    return f"{clean(c.path, 80)} +{c.added} -{c.deleted}"


def render(f: CardFacts) -> str:
    added = sum(c.added or 0 for c in f.changes)
    deleted = sum(c.deleted or 0 for c in f.changes)
    plural = "commit" if f.commits == 1 else "commits"
    lines = [
        f"merge   {f.branch} -> {f.target}   (merge commit, no fast-forward)",
        f'commit  {f.sha}  "{clean(f.subject, 80)}"   {f.commits} {plural}, {len(f.changes)} files, '
        f"+{added} -{deleted}",
        f"onto    {f.head[:12]}  {f.target}, clean      started from {f.base[:12]} (recorded) : ancestor ok",
        f"task    {f.task_ref or 'none'}   worker session {f.worker or 'unknown'}   "
        f"finished tip {f.tip_note}",
        f"checks  conflicts: none   objects re-hashed: {f.objects}/{f.objects} ok   "
        f"sandbox for workers: {f.sandbox}",
    ]
    for name, count in sorted(f.shared.items()):
        lines.append(
            f"warn    {count} {'commit is' if count == 1 else 'commits are'} also on {clean(name, 80)}"
        )
    lines += [f"warn    changes {clean(p, 120)}" for p in f.definitions[:10]]
    if len(f.definitions) > 10:
        lines.append(f"warn    … and {len(f.definitions) - 10} more definition files")
    if f.note:
        lines.append(f"note    (agent, not verified) {clean(f.note, 300)}")
    shown = [_stat(c) for c in f.changes[:8]]
    more = f" | … (+{len(f.changes) - 8} more)" if len(f.changes) > 8 else ""
    lines.append(f"files   {' | '.join(shown)}{more}")
    text = "\n".join(lines)
    return text if len(text) <= CAP else text[: CAP - 1] + "…"
