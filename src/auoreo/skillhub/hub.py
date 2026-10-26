"""Skills hub (G6): search taps, install into quarantine, approve, remove — pinned by sha256 in
`~/.auoreo/skills.lock`, every action appended to `~/.auoreo/logs/skills_hub.jsonl`.

Lifecycle: `install` downloads into `~/.auoreo/skills/.quarantine/<name>/` (dot folders are
never discovered, so a quarantined skill is invisible to agents), lints it and records
`{source, tap, version, sha256, status: quarantined}`. `approve` re-hashes the quarantined
copy, refuses on mismatch or lint errors, and moves it to `~/.auoreo/skills/<name>/`
(the runtime root, highest precedence). `remove` only touches hub-managed skills. `verify`
re-hashes approved skills against the lock. MCP servers that expose `skill://<name>` or
`<scheme>://skills/<name>` resources are listed as a read-only source (`mcp_skills`).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from auoreo.core.settings_g6 import SkillsHubSettings
from auoreo.memory.skill_lint import lint_file
from auoreo.skillhub.fetch import (
    SKILL_FILE,
    HubError,
    HubSkill,
    download,
    git_checkout,
    git_index,
    http_index,
    is_git,
    tree_hash,
    unpack,
)

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".ts", ".rb", ".pl", ".ps1", ".bash")
MCP_SKILL_URI = re.compile(r"^(?:skills?://|[a-z][a-z0-9+.-]*://skills/)(?P<name>[A-Za-z0-9_.-]+)/?$")


class SkillsHub:
    def __init__(self, home: Path, cfg: SkillsHubSettings | None = None) -> None:
        self.home = home
        self.cfg = cfg or SkillsHubSettings()

    # ------------------------------------------------------------- paths
    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def quarantine_dir(self) -> Path:
        return self.skills_dir / ".quarantine"

    @property
    def lock_path(self) -> Path:
        return self.home / "skills.lock"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache" / "skills-hub"

    # -------------------------------------------------------------- lock
    def lock(self) -> dict[str, dict[str, Any]]:
        if not self.lock_path.is_file():
            return {}
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HubError(f"{self.lock_path} is unreadable; fix or delete it") from None
        skills = data.get("skills") if isinstance(data, dict) else None
        return skills if isinstance(skills, dict) else {}

    def _write_lock(self, skills: dict[str, dict[str, Any]]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.lock_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "skills": skills}, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.lock_path)

    def _audit(self, action: str, name: str, **extra: Any) -> None:
        log = self.home / "logs" / "skills_hub.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "action": action, "name": name, **extra}) + "\n")

    # ------------------------------------------------------------ search
    async def index(self, taps: list[str] | None = None) -> tuple[list[HubSkill], list[str]]:
        """Every tap's skills (first tap wins on a name clash) and per-tap errors."""
        out: dict[str, HubSkill] = {}
        errors: list[str] = []
        for tap in taps if taps is not None else self.cfg.taps:
            try:
                found = (
                    git_index(tap, self.cache_dir, self.cfg.index_ttl_s)
                    if is_git(tap)
                    else await http_index(tap, self.cache_dir, self.cfg.index_ttl_s)
                )
            except HubError as exc:
                errors.append(str(exc))
                continue
            for s in found:
                out.setdefault(s.name, s)
        return list(out.values()), errors

    async def search(self, query: str = "") -> tuple[list[HubSkill], list[str]]:
        skills, errors = await self.index()
        q = query.lower().strip()
        hits = [s for s in skills if not q or q in s.name.lower() or q in s.description.lower()]
        return sorted(hits, key=lambda s: s.name), errors

    # ----------------------------------------------------------- install
    async def install(self, name: str, *, tap: str | None = None, force: bool = False) -> dict[str, Any]:
        if not NAME_RE.fullmatch(name):
            raise HubError(f"invalid skill name '{name}' (kebab-case)")
        skills, errors = await self.index([tap] if tap else None)
        entry = next((s for s in skills if s.name == name), None)
        if entry is None:
            hint = f" ({'; '.join(errors)})" if errors else ""
            raise HubError(f"skill '{name}' is not in any tap{hint}")
        lock = self.lock()
        if (self.skills_dir / name).exists() and name not in lock:
            raise HubError(f"~/.auoreo/skills/{name} already exists and is not hub-managed")
        if name in lock and not force:
            raise HubError(
                f"'{name}' is already installed ({lock[name]['status']}); use --force to reinstall"
            )
        target = self.quarantine_dir / name
        with tempfile.TemporaryDirectory(prefix="auoreo-hub-") as tmp:
            staged = Path(tmp) / name
            await self._fetch(entry, staged)
            digest = tree_hash(staged)
            lint = lint_file(staged / SKILL_FILE)
            if target.exists():
                shutil.rmtree(target)
            self.quarantine_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), target)
        if name in lock and (self.skills_dir / name).exists():
            shutil.rmtree(self.skills_dir / name)  # --force: the old approved copy goes back to review
        files = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
        record = {
            "source": "hub",
            "tap": entry.tap,
            "version": entry.version,
            "url": entry.url,
            "sha256": digest,
            "status": "quarantined",
            "installed_at": time.time(),
            "approved_at": None,
            "files": files,
            "has_scripts": any(f.endswith(SCRIPT_SUFFIXES) for f in files),
            "lint": lint,
        }
        lock[name] = record
        self._write_lock(lock)
        self._audit("install", name, sha256=digest, tap=entry.tap, version=entry.version)
        return {"name": name, **record}

    async def _fetch(self, entry: HubSkill, staged: Path) -> None:
        if entry.path is not None:  # git tap: copy the folder from the (cached) checkout
            root, _ = git_checkout(entry.tap, self.cache_dir, self.cfg.index_ttl_s)
            src = root / entry.path
            if any(p.is_symlink() for p in src.rglob("*")):
                raise HubError(f"symlinks are not allowed in a skill ({entry.name})")
            size = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
            if size > self.cfg.max_skill_bytes:
                raise HubError(f"skill '{entry.name}' is {size} bytes (cap {self.cfg.max_skill_bytes})")
            shutil.copytree(src, staged, ignore=shutil.ignore_patterns(".git"))
            return
        assert entry.url is not None
        data = await download(entry.url, self.cfg.max_skill_bytes)
        if entry.sha256 is not None:
            got = hashlib.sha256(data).hexdigest()
            if got != entry.sha256:
                raise HubError(
                    f"sha256 mismatch for '{entry.name}': index pins {entry.sha256[:12]}…, got {got[:12]}…"
                )
        unpack(data, entry.url, staged, self.cfg.max_skill_bytes)

    # ---------------------------------------------------- approve/remove
    def approve(self, name: str) -> dict[str, Any]:
        lock = self.lock()
        rec = lock.get(name)
        if rec is None or rec.get("status") != "quarantined":
            raise HubError(f"'{name}' is not quarantined")
        src = self.quarantine_dir / name
        if not src.is_dir():
            raise HubError(f"quarantined copy of '{name}' is missing; reinstall it")
        digest = tree_hash(src)
        if digest != rec["sha256"]:
            raise HubError(f"'{name}' changed in quarantine (sha256 {digest[:12]}… ≠ lock); reinstall it")
        errors = lint_file(src / SKILL_FILE)
        if errors:
            raise HubError(f"'{name}' fails lint: {'; '.join(errors)}")
        dest = self.skills_dir / name
        if dest.exists():
            raise HubError(f"~/.auoreo/skills/{name} already exists")
        shutil.move(str(src), dest)
        rec.update(status="approved", approved_at=time.time(), lint=[])
        self._write_lock(lock)
        self._audit("approve", name, sha256=digest)
        return {"name": name, **rec}

    def remove(self, name: str) -> dict[str, Any]:
        lock = self.lock()
        rec = lock.pop(name, None)
        if rec is None:
            raise HubError(f"'{name}' is not hub-managed (not in skills.lock)")
        for d in (self.quarantine_dir / name, self.skills_dir / name):
            if d.is_dir():
                shutil.rmtree(d)
        self._write_lock(lock)
        self._audit("remove", name, sha256=rec.get("sha256"))
        return {"name": name, **rec}

    def verify(self) -> dict[str, str]:
        """name → `ok` | `modified` | `missing` for every lock entry."""
        out: dict[str, str] = {}
        for name, rec in self.lock().items():
            folder = (self.skills_dir if rec.get("status") == "approved" else self.quarantine_dir) / name
            if not folder.is_dir():
                out[name] = "missing"
                continue
            try:
                out[name] = "ok" if tree_hash(folder) == rec.get("sha256") else "modified"
            except HubError:
                out[name] = "modified"
        return out


def hub_for(settings: Any) -> SkillsHub:
    g6 = getattr(settings, "g6", None)
    return SkillsHub(settings.home, g6.skills if g6 is not None else None)


async def mcp_skills(services: Any) -> list[dict[str, Any]]:
    """Skills offered by connected MCP servers as resources (a listed, read-only source)."""
    bridge = getattr(services, "mcp", None)
    if bridge is None:
        return []
    out: list[dict[str, Any]] = []
    for server in bridge.connected():
        try:
            resources = await bridge.list_resources(server)
        except Exception:  # one broken server must not hide the others
            continue
        for r in resources:
            m = MCP_SKILL_URI.match(str(r.uri))
            if m:
                out.append(
                    {
                        "source": f"mcp:{server}",
                        "server": server,
                        "name": m.group("name"),
                        "uri": str(r.uri),
                        "description": getattr(r, "description", None) or "",
                    }
                )
    return out


def skill_dict(s: HubSkill) -> dict[str, Any]:
    return asdict(s)
