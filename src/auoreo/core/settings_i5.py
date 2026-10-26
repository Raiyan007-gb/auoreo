"""I5: web UI + CLI parity knobs — `auoreo run -f` binary attachments, doctor thresholds,
the approval input editor and the stream-json stdout guard.

Attached to `Settings` as `i5: I5Settings`; set it in `settings.json`
(`{"i5": {"attachments": {"max_inline_binary_bytes": 262144}}}`) or as a JSON env var
(`AUOREO_I5='{...}'`). Secrets never live here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AttachmentSettings(_Cfg):
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    """Per-file cap for `auoreo run -f` (spec §18: 10 MiB)."""
    max_images: int = Field(default=8, ge=0, le=64)
    """Images sent as `image_url` parts per turn; further images are refused (exit 2)."""
    max_inline_binary_bytes: int = Field(default=256 * 1024, ge=0)
    """A non-image binary up to this size travels base64-encoded inside an `<attachment>`
    block; larger ones are named but not inlined."""
    max_total_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    """All attachments of one run together (images + inlined binaries + text)."""


class DoctorSettings(_Cfg):
    disk_warn_free_gb: float = Field(default=2.0, ge=0)
    """`disk` row warns below this much free space on the home's filesystem."""
    disk_error_free_gb: float = Field(default=0.25, ge=0)
    """`disk` row errors below this much free space."""
    worktree_stale_days: float = Field(default=14.0, ge=0)
    """`worktrees` row: a worktree untouched this long counts as stale (`doctor --fix` prunes it)."""


class ApprovalEditSettings(_Cfg):
    enabled: bool = True
    """Approval cards may answer `allow{updated_input}` (spec §5.2): the human edits the tool
    arguments before approving. The edit is re-validated against the tool schema and the
    floors, and audited as `tool.input_edited`."""
    max_input_bytes: int = Field(default=64 * 1024, ge=256)
    """Largest edited input accepted (JSON bytes); also caps the `input` shown on the card."""


class I5Settings(_Cfg):
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    doctor: DoctorSettings = Field(default_factory=DoctorSettings)
    approval_edit: ApprovalEditSettings = Field(default_factory=ApprovalEditSettings)
