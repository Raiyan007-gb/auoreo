"""STT via a configured local command (spec §24.5 / §25.1 `local_command`).

`i8.stt.local_command = {template, model, timeout_s=30, allowlist}`. The WAV is written to a
fresh temp dir; `{input}`, `{model}` and `{output_dir}` are substituted per argv element (no
shell, so no injection through the template). argv[0]'s basename must be in `allowlist`
(empty = deny). The process runs through the selected sandbox backend when the voice service
bound one (`bind(services)`), else as a plain subprocess, always with the scrubbed
`core.secrets.child_env` (no API keys). The transcript is stdout, or `<input>.txt` /
`<output_dir>/*.txt` when stdout is empty (whisper.cpp / openai-whisper style). Each run
records `{cmd_sha, duration_s, exit_code}` in `last_run` (and the audit log when bound).
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecSpec, run_local
from auoreo.backends.stt import SttBackend, Transcript, check_upload
from auoreo.core.secrets import child_env
from auoreo.core.settings_i8 import LocalCommandSettings, i8_of
from auoreo.voice.audio import AudioError, VoiceUnavailable

REMEDY = (
    'set i8.stt.local_command.template (e.g. ["whisper-cli", "{input}", "--model", "{model}"]) '
    "and add the executable's name to i8.stt.local_command.allowlist"
)


def _prepare(tmp: str, wav: bytes) -> tuple[Path, Path]:
    src, out_dir = Path(tmp, "input.wav"), Path(tmp, "out")
    src.write_bytes(wav)
    out_dir.mkdir()
    return src, out_dir


def _read_output(src: Path, out_dir: Path) -> str:
    files = [src.with_suffix(".txt"), src.with_name("input.wav.txt"), *sorted(out_dir.glob("*.txt"))]
    return next((f.read_text("utf-8", "replace").strip() for f in files if f.is_file()), "")


def render(template: Sequence[str], values: dict[str, str]) -> list[str]:
    out: list[str] = []
    for part in template:
        for k, v in values.items():
            part = part.replace("{" + k + "}", v)
        out.append(part)
    return out


class Backend(SttBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._svc: Any = None
        self.last_run: dict[str, Any] = {}

    def bind(self, services: Any) -> None:
        self._svc = services

    @property
    def cfg(self) -> LocalCommandSettings:
        return i8_of(self.settings).stt.local_command

    def _refusal(self) -> str | None:
        cfg = self.cfg
        if not cfg.template:
            return "i8.stt.local_command.template is not set"
        exe = Path(cfg.template[0]).name
        if exe not in cfg.allowlist:
            return f"{exe!r} is not in i8.stt.local_command.allowlist (deny by default)"
        if shutil.which(cfg.template[0]) is None:
            return f"{cfg.template[0]} is not on PATH"
        return None

    async def check(self) -> BackendStatus:
        why = self._refusal()
        if why:
            return BackendStatus(False, why, remedy=REMEDY)
        return BackendStatus(
            True, f"local command {Path(self.cfg.template[0]).name} (model {self.cfg.model})"
        )

    async def _exec(self, spec: ExecSpec) -> Any:
        if self._svc is not None:
            try:
                sandbox = self._svc.backend("sandbox")
            except (KeyError, LookupError):
                sandbox = None
            if sandbox is not None:
                return await sandbox.run(spec)
        return await run_local(spec, backend="none", enforced=False)

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        check_upload(wav)
        if why := self._refusal():
            raise VoiceUnavailable(f"stt/local_command: {why}; {REMEDY}")
        cfg = self.cfg
        with tempfile.TemporaryDirectory(prefix="auoreo-stt-") as tmp:
            src, out_dir = _prepare(tmp, wav)
            argv = render(cfg.template, {"input": str(src), "model": cfg.model, "output_dir": str(out_dir)})
            spec = ExecSpec(
                argv=argv,
                cwd=Path(tmp),
                env=child_env([], None),
                timeout_s=cfg.timeout_s,
                mode="workspace-write",
                net="none",
            )
            started = time.monotonic()
            res = await self._exec(spec)
            text = res.stdout.strip()
            if not text:
                text = _read_output(src, out_dir)
        self.last_run = {
            "cmd_sha": hashlib.sha256("\0".join(cfg.template).encode()).hexdigest()[:16],
            "duration_s": round(time.monotonic() - started, 3),
            "exit_code": res.exit_code,
            "enforced": bool(res.enforced),
        }
        if self._svc is not None:
            await self._svc.audit.record(
                actor="server", action="stt.local_command", session_id=None, detail=dict(self.last_run)
            )
        if res.timed_out:
            raise AudioError(f"stt/local_command timed out after {cfg.timeout_s:g}s")
        if res.exit_code != 0:
            raise AudioError(f"stt/local_command exited {res.exit_code}: {res.stderr.strip()[-200:]}")
        return Transcript(text, [], language, None)
