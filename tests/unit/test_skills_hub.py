"""G6 skills hub: HTTP index + git taps, quarantine (invisible until approved), sha256 lockfile,
tamper/lint/traversal refusals, remove/verify, MCP skill sources, CLI and API."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.core import settings_store
from auoreo.core.services import Services
from auoreo.core.settings_g6 import SkillsHubSettings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.skillhub import HubError, SkillsHub, mcp_skills
from auoreo.skillhub.fetch import tree_hash
from tests.conftest import REPO

INDEX = "https://hub.test/index.json"
GOOD = (
    "---\nname: {name}\ndescription: Use when you need to {name} things carefully and step by step.\n---\n"
    "# {name}\n\nFollow these steps exactly: read the input, plan, act, verify the outcome.\n"
)


def skill_md(name: str) -> bytes:
    return GOOD.format(name=name).encode()


def tgz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def hub(home: Path) -> SkillsHub:
    return SkillsHub(home, SkillsHubSettings(taps=[INDEX]))


def discovered(home: Path) -> set[str]:
    return set(Harness([Root("runtime", home), Root("bundled", REPO)]).skills)


def add_index(httpx_mock: HTTPXMock, entries: list[dict[str, Any]]) -> None:
    httpx_mock.add_response(url=INDEX, json={"skills": entries})


# -------------------------------------------------------------- HTTP taps
async def test_http_install_quarantine_approve(hub: SkillsHub, home: Path, httpx_mock: HTTPXMock) -> None:
    md = skill_md("tidy-imports")
    add_index(
        httpx_mock,
        [
            {
                "name": "tidy-imports",
                "description": "sort imports",
                "version": "1.0",
                "url": "https://hub.test/tidy/SKILL.md",
                "sha256": sha(md),
            },
            {"name": "other", "description": "x", "url": "https://hub.test/other.md"},
        ],
    )
    httpx_mock.add_response(url="https://hub.test/tidy/SKILL.md", content=md)
    hits, errors = await hub.search("import")
    assert [h.name for h in hits] == ["tidy-imports"] and errors == []
    rec = await hub.install("tidy-imports")  # index served from the 1 h cache
    assert rec["status"] == "quarantined" and rec["files"] == ["SKILL.md"] and rec["lint"] == []
    assert (home / "skills" / ".quarantine" / "tidy-imports" / "SKILL.md").read_bytes() == md
    assert "tidy-imports" not in discovered(home)  # quarantined skills are invisible
    lock = json.loads((home / "skills.lock").read_text())["skills"]["tidy-imports"]
    assert (
        lock["sha256"] == tree_hash(home / "skills" / ".quarantine" / "tidy-imports") and lock["tap"] == INDEX
    )
    approved = hub.approve("tidy-imports")
    assert approved["status"] == "approved" and approved["approved_at"]
    assert "tidy-imports" in discovered(home)
    assert hub.verify() == {"tidy-imports": "ok"}
    (home / "skills" / "tidy-imports" / "SKILL.md").write_text("tampered")
    assert hub.verify() == {"tidy-imports": "modified"}
    hub.remove("tidy-imports")
    assert not (home / "skills" / "tidy-imports").exists() and hub.lock() == {}
    actions = [
        json.loads(line)["action"] for line in (home / "logs" / "skills_hub.jsonl").read_text().splitlines()
    ]
    assert actions == ["install", "approve", "remove"]


async def test_archive_install_and_pinned_hash_mismatch(
    hub: SkillsHub, home: Path, httpx_mock: HTTPXMock
) -> None:
    archive = tgz({"pack/SKILL.md": skill_md("pack"), "pack/scripts/run.sh": b"echo hi\n"})
    add_index(
        httpx_mock,
        [
            {"name": "pack", "url": "https://hub.test/pack.tar.gz", "sha256": sha(archive)},
            {"name": "evil", "url": "https://hub.test/evil.md", "sha256": "0" * 64},
        ],
    )
    httpx_mock.add_response(url="https://hub.test/pack.tar.gz", content=archive)
    httpx_mock.add_response(url="https://hub.test/evil.md", content=skill_md("evil"))
    rec = await hub.install("pack")
    assert rec["files"] == ["SKILL.md", "scripts/run.sh"] and rec["has_scripts"]
    with pytest.raises(HubError, match="sha256 mismatch"):
        await hub.install("evil")
    assert "evil" not in hub.lock()
    with pytest.raises(HubError, match="already installed"):
        await hub.install("pack")


async def test_traversal_and_symlinks_refused(hub: SkillsHub, httpx_mock: HTTPXMock) -> None:
    bad = tgz({"../escape/SKILL.md": skill_md("x")})
    add_index(httpx_mock, [{"name": "trav", "url": "https://hub.test/trav.tgz"}])
    httpx_mock.add_response(url="https://hub.test/trav.tgz", content=bad)
    with pytest.raises(HubError, match="unsafe path"):
        await hub.install("trav")


async def test_tampered_quarantine_and_lint_block_approval(
    hub: SkillsHub, home: Path, httpx_mock: HTTPXMock
) -> None:
    add_index(
        httpx_mock,
        [
            {"name": "good-one", "url": "https://hub.test/g.md"},
            {"name": "bad-one", "url": "https://hub.test/b.md"},
        ],
    )
    httpx_mock.add_response(url="https://hub.test/g.md", content=skill_md("good-one"))
    httpx_mock.add_response(
        url="https://hub.test/b.md", content=b"---\nname: bad-one\ndescription: short\n---\nx\n"
    )
    await hub.install("good-one")
    (home / "skills" / ".quarantine" / "good-one" / "extra.py").write_text("print('sneaky')")
    with pytest.raises(HubError, match="changed in quarantine"):
        hub.approve("good-one")
    rec = await hub.install("bad-one")
    assert rec["lint"]
    with pytest.raises(HubError, match="fails lint"):
        hub.approve("bad-one")
    with pytest.raises(HubError, match="not quarantined"):
        hub.approve("never-installed")
    with pytest.raises(HubError, match="not hub-managed"):
        hub.remove("code-review")


async def test_unknown_skill_reports_tap_errors(home: Path, httpx_mock: HTTPXMock) -> None:
    hub = SkillsHub(home, SkillsHubSettings(taps=[INDEX]))
    httpx_mock.add_response(url=INDEX, status_code=503)
    with pytest.raises(HubError, match="not in any tap.*HTTP 503"):
        await hub.install("anything")
    with pytest.raises(HubError, match="invalid skill name"):
        await hub.install("../etc")


async def test_existing_unmanaged_skill_is_never_overwritten(
    hub: SkillsHub, home: Path, httpx_mock: HTTPXMock
) -> None:
    (home / "skills" / "mine").mkdir(parents=True)
    add_index(httpx_mock, [{"name": "mine", "url": "https://hub.test/m.md"}])
    with pytest.raises(HubError, match="not hub-managed"):
        await hub.install("mine")


# ---------------------------------------------------------------- git taps
def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_tap(tmp_path: Path) -> str:
    repo = tmp_path / "taprepo"
    (repo / "skills" / "git-skill").mkdir(parents=True)
    (repo / "skills" / "git-skill" / "SKILL.md").write_bytes(skill_md("git-skill"))
    (repo / "skills" / "git-skill" / "notes.md").write_text("extra notes\n")
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=repo)
    return f"git+file://{repo}"


async def test_git_tap_install(home: Path, git_tap: str) -> None:
    hub = SkillsHub(home, SkillsHubSettings(taps=[git_tap]))
    hits, errors = await hub.search()
    assert errors == [] and [h.name for h in hits] == ["git-skill"] and len(hits[0].version) == 40
    rec = await hub.install("git-skill")
    assert rec["files"] == ["SKILL.md", "notes.md"] and rec["version"] == hits[0].version
    hub.approve("git-skill")
    assert "git-skill" in discovered(home)


# ------------------------------------------------------------------- MCP
async def test_mcp_skills_listed_as_source() -> None:
    res = [
        SimpleNamespace(uri="skill://deploy-checklist", description="deploy steps"),
        SimpleNamespace(uri="auoreo://skills/code-review", description=None),
        SimpleNamespace(uri="file:///etc/hosts", description="not a skill"),
    ]

    class Bridge:
        def connected(self) -> list[str]:
            return ["docs", "broken"]

        async def list_resources(self, server: str) -> list[Any]:
            if server == "broken":
                raise RuntimeError("down")
            return res

    out = await mcp_skills(SimpleNamespace(mcp=Bridge()))
    assert [(s["source"], s["name"]) for s in out] == [
        ("mcp:docs", "deploy-checklist"),
        ("mcp:docs", "code-review"),
    ]
    assert await mcp_skills(SimpleNamespace(mcp=None)) == []


# ------------------------------------------------------------------- CLI
def test_cli_taps_and_install(home: Path, httpx_mock: HTTPXMock) -> None:
    from auoreo.cli.app import app

    runner = CliRunner()
    r = runner.invoke(app, ["skills", "taps", "add", INDEX])
    assert r.exit_code == 0, r.output
    assert settings_store.read(home)["g6"]["skills"]["taps"] == [INDEX]
    assert INDEX in runner.invoke(app, ["skills", "taps"]).output
    add_index(
        httpx_mock, [{"name": "cli-skill", "description": "from the cli", "url": "https://hub.test/c.md"}]
    )
    httpx_mock.add_response(url="https://hub.test/c.md", content=skill_md("cli-skill"))
    r = runner.invoke(app, ["skills", "install", "cli-skill"])
    assert r.exit_code == 0 and "quarantined" in r.output, r.output
    r = runner.invoke(app, ["skills", "list"])
    assert "cli-skill  quarantined  ok" in r.output
    assert runner.invoke(app, ["skills", "approve", "cli-skill"]).exit_code == 0
    assert runner.invoke(app, ["skills", "verify"]).exit_code == 0
    assert runner.invoke(app, ["skills", "remove", "cli-skill"]).exit_code == 0
    assert runner.invoke(app, ["skills", "approve", "cli-skill"]).exit_code == 1
    assert runner.invoke(app, ["skills", "taps", "remove", INDEX]).exit_code == 0
    assert runner.invoke(app, ["skills", "search"]).exit_code == 1  # no taps left


# ------------------------------------------------------------------- API
@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_api_sources_and_approve(client: httpx.AsyncClient, services: Services) -> None:
    home = services.settings.home
    hub = SkillsHub(home)
    q = hub.quarantine_dir / "api-skill"
    q.mkdir(parents=True)
    (q / "SKILL.md").write_bytes(skill_md("api-skill"))
    hub._write_lock(
        {
            "api-skill": {
                "source": "hub",
                "tap": INDEX,
                "version": "1",
                "url": None,
                "sha256": tree_hash(q),
                "status": "quarantined",
                "installed_at": 1.0,
                "approved_at": None,
                "files": ["SKILL.md"],
                "has_scripts": False,
                "lint": [],
            }
        }
    )
    r = await client.get("/api/skills/sources")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mcp"] == [] and body["installed"][0]["status"] == "quarantined"
    assert body["installed"][0]["integrity"] == "ok"
    assert "api-skill" not in services.harness.skills
    r = await client.post("/api/skills/hub/api-skill/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert "api-skill" in services.harness.skills  # reloaded
    assert (await client.post("/api/skills/hub/api-skill/approve")).status_code == 404
