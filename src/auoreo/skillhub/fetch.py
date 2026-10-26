"""Skills hub transport (spec §2.11 `skills_hub` pattern): tap indexes, downloads, safe
extraction and the content hash recorded in `~/.auoreo/skills.lock`.

A *tap* is either
- an HTTP(S) JSON index: `{"skills": [{"name", "description", "version", "url", "sha256"}]}`
  where `url` is a raw `SKILL.md` or a `.tar.gz`/`.tgz`/`.zip` of the skill folder and
  `sha256` (when given) pins the downloaded bytes; or
- a git repository (`git+https://…`, `git+file://…`, `….git`): shallow-cloned into the cache,
  skills are its `skills/<name>/SKILL.md` (or top-level `<name>/SKILL.md`) folders.

Indexes are cached for `g6.skills.index_ttl_s` (1 h). Nothing is executed from a download.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from auoreo.harness import frontmatter

TIMEOUT_S = 30.0
SKILL_FILE = "SKILL.md"


class HubError(RuntimeError):
    """A tap/index/download problem (message is user-facing; never contains credentials)."""


@dataclass(frozen=True)
class HubSkill:
    name: str
    description: str
    tap: str
    version: str = ""
    url: str | None = None
    sha256: str | None = None
    path: str | None = None
    """For git taps: the folder inside the clone."""


def is_git(tap: str) -> bool:
    return tap.startswith("git+") or tap.endswith(".git")


def _key(tap: str) -> str:
    return hashlib.sha256(tap.encode()).hexdigest()[:16]


def tree_hash(folder: Path) -> str:
    """sha256 over sorted `relpath \\0 sha256(content)` lines; symlinks are refused."""
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_symlink():
            raise HubError(f"symlinks are not allowed in a skill: {p.relative_to(folder)}")
        if p.is_file():
            rel = p.relative_to(folder).as_posix()
            h.update(f"{rel}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode())
    return h.hexdigest()


def read_meta(skill_md: Path) -> dict[str, Any]:
    try:
        data, _body, _ = frontmatter.split(skill_md.read_text(encoding="utf-8"))
    except (OSError, frontmatter.FrontmatterError) as exc:
        raise HubError(f"unreadable {skill_md.name}: {exc}") from None
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ indexes
def _write_cache(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


async def http_index(tap: str, cache: Path, ttl_s: int) -> list[HubSkill]:
    cached = cache / f"{_key(tap)}.json"
    if cached.is_file() and time.time() - cached.stat().st_mtime < ttl_s:
        body = json.loads(cached.read_text(encoding="utf-8"))
    else:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
                r = await client.get(tap)
        except httpx.HTTPError as exc:
            raise HubError(f"tap {tap} unreachable ({type(exc).__name__})") from None
        if r.status_code >= 400:
            raise HubError(f"tap {tap} answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise HubError(f"tap {tap} is not a JSON index") from None
        _write_cache(cached, body)
    entries = body.get("skills") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise HubError(f"tap {tap}: index has no 'skills' list")
    return [
        HubSkill(
            name=str(e["name"]),
            description=str(e.get("description", "")),
            tap=tap,
            version=str(e.get("version", "")),
            url=str(e["url"]),
            sha256=(str(e["sha256"]).lower() if e.get("sha256") else None),
        )
        for e in entries
        if isinstance(e, dict) and e.get("name") and e.get("url")
    ]


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, check=True)
    except FileNotFoundError:
        raise HubError("git is not installed") from None
    except subprocess.CalledProcessError as exc:
        raise HubError(f"git {args[0]} failed: {exc.stderr.strip()[-300:]}") from None
    except subprocess.TimeoutExpired:
        raise HubError(f"git {args[0]} timed out") from None
    return out.stdout.strip()


def git_checkout(tap: str, cache: Path, ttl_s: int) -> tuple[Path, str]:
    """Shallow clone (or refresh after the TTL) → (checkout dir, HEAD commit)."""
    url = tap[4:] if tap.startswith("git+") else tap
    dest = cache / f"git-{_key(tap)}"
    if not (dest / ".git").is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        cache.mkdir(parents=True, exist_ok=True)
        _git("clone", "--depth", "1", "--quiet", url, str(dest))
    elif time.time() - (dest / ".git").stat().st_mtime >= ttl_s:
        _git("fetch", "--depth", "1", "--quiet", "origin", cwd=dest)
        _git("reset", "--hard", "--quiet", "FETCH_HEAD", cwd=dest)
        (dest / ".git").touch()
    return dest, _git("rev-parse", "HEAD", cwd=dest)


def git_index(tap: str, cache: Path, ttl_s: int) -> list[HubSkill]:
    root, commit = git_checkout(tap, cache, ttl_s)
    out: list[HubSkill] = []
    for base in (root / "skills", root):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.name.startswith(".") or not (d / SKILL_FILE).is_file():
                continue
            if any(s.name == d.name for s in out):
                continue
            meta = read_meta(d / SKILL_FILE)
            out.append(
                HubSkill(
                    name=str(meta.get("name") or d.name),
                    description=str(meta.get("description", "")),
                    tap=tap,
                    version=commit,
                    path=str(d.relative_to(root)),
                )
            )
    return out


# ---------------------------------------------------------------- downloads
async def download(url: str, max_bytes: int) -> bytes:
    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client,
            client.stream("GET", url) as r,
        ):
            if r.status_code >= 400:
                raise HubError(f"download {url} answered HTTP {r.status_code}")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise HubError(f"download {url} exceeds {max_bytes} bytes")
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise HubError(f"download {url} failed ({type(exc).__name__})") from None


def _safe_rel(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise HubError(f"unsafe path in archive: {name}")
    return p


def unpack(data: bytes, url: str, dest: Path, max_bytes: int) -> None:
    """Write a raw SKILL.md or extract an archive into `dest` (single top folder stripped)."""
    dest.mkdir(parents=True)
    lower = url.lower().split("?")[0]
    if not lower.endswith((".tar.gz", ".tgz", ".zip")):
        (dest / SKILL_FILE).write_bytes(data)
        return
    members: list[tuple[PurePosixPath, bytes]] = []
    if lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise HubError(f"symlinks are not allowed in a skill: {info.filename}")
                members.append((_safe_rel(info.filename), zf.read(info)))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.isdir():
                    continue
                if not m.isfile():
                    raise HubError(f"only regular files are allowed in a skill: {m.name}")
                f = tf.extractfile(m)
                members.append((_safe_rel(m.name), f.read() if f else b""))
    if sum(len(b) for _, b in members) > max_bytes:
        raise HubError(f"archive expands beyond {max_bytes} bytes")
    tops = {p.parts[0] for p, _ in members}
    strip = len(tops) == 1 and all(len(p.parts) > 1 for p, _ in members)
    for rel, blob in members:
        target = dest.joinpath(*(rel.parts[1:] if strip else rel.parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    if not (dest / SKILL_FILE).is_file():
        raise HubError(f"archive {url} has no {SKILL_FILE} at its root")
