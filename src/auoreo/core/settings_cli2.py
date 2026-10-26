"""H4: knobs for the second CLI wave (spec §18): `loop`, `heartbeat`, `worktree gc`,
`debug upload`, `config edit`. Attached to `Settings` as `cli2: Cli2Settings`
(`{"cli2": {"heartbeat_every_s": 900}}` in settings.json)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Cli2Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    loop_max_iterations: int = Field(default=1000, ge=1)
    """Hard cap for `auoreo loop` when no `--limit` is given (Ctrl-C stops it earlier)."""
    heartbeat_every_s: float = Field(default=1800.0, gt=0)
    """Interval of `auoreo heartbeat` (proactive check-in) when `--every` is not given."""
    heartbeat_file: str = "HEARTBEAT.md"
    """Checklist read on each beat: `<cwd>/.auoreo/<file>` first, then `<home>/<file>`."""
    heartbeat_ok_token: str = "HEARTBEAT_OK"
    """A reply that is exactly this token (after trimming) means "nothing to report": it is
    not printed, so a quiet heartbeat stays quiet."""
    worktree_stale_days: float = Field(default=7.0, ge=0)
    """`auoreo worktree gc`: a clean worktree untouched for this long is stale."""
    debug_upload_url: str | None = None
    """The ONLY endpoint `auoreo debug upload` sends a (force-redacted) bundle to; unset = refuse."""
    debug_upload_secret: str | None = None
    """Secret NAME (env / keyring / secrets.db) holding a bearer token for that endpoint."""
    editor: str | None = None
    """Editor for `auoreo config edit` (else $VISUAL, $EDITOR, then `vi` / `notepad`)."""
