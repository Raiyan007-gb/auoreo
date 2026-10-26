"""G5a: `auoreo run` flags — --print, --interactive, --bare, -f, --fork, --continue,
--variant, --thinking, --allow/--deny, --auto, --attach (validation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.cli.run_support import MAX_ATTACHMENT, RunUsageError, compose_prompt, parse_rule
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness
from auoreo.runtime.inbox import RunOptions
from auoreo.runtime.run_overrides import UnknownVariant, apply_run_options, find_variant
from tests.conftest import REPO

runner = CliRunner()


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.chdir(workdir)
    return workdir


def _json(out: str) -> dict:  # type: ignore[type-arg]
    return json.loads(out[out.index("{") :])


# ------------------------------------------------------------------ helpers
def test_compose_prompt_inlines_text_and_names_binaries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello attachment")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02")
    text = compose_prompt("look", [tmp_path / "a.txt", tmp_path / "b.bin"])
    assert text.startswith("look\n\n<attachment path=")
    # I5: small binaries now travel base64-encoded (within `i5.attachments` caps)
    assert "hello attachment" in text and 'encoding="base64">\nAAEC\n' in text


def test_compose_prompt_caps_each_file(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    with big.open("wb") as fh:
        fh.truncate(MAX_ATTACHMENT + 1)
    with pytest.raises(RunUsageError, match="10 MiB"):
        compose_prompt("x", [big])
    with pytest.raises(RunUsageError, match="not found"):
        compose_prompt("x", [tmp_path / "missing.txt"])


def test_parse_rule() -> None:
    r = parse_rule("bash:git *", "allow")
    assert (r.permission, r.pattern, r.action) == ("bash", "git *", "allow")
    assert parse_rule("read", "deny").pattern == "*"
    with pytest.raises(RunUsageError):
        parse_rule(":x", "allow")


def test_run_overrides_bare_variant_effort(home: Path) -> None:
    h = Harness(default_roots(home, home, REPO, []))
    from auoreo.harness.orchestrator import build_spec

    spec = build_spec(h, "default", "openai/gpt-5", "manual")
    assert apply_run_options(h, spec, None) is spec
    bare = apply_run_options(h, spec, RunOptions(bare=True, effort="high"))
    assert bare.system_prompt == spec.agent.body.strip() and len(bare.system_prompt) < len(spec.system_prompt)
    assert bare.agent.manifest.effort == "high"
    shaped = apply_run_options(h, spec, RunOptions(variant="openrouter/x/y:free"))
    assert shaped.variant is not None and shaped.variant.reasoning == "off"
    with pytest.raises(ValueError, match="effort"):
        apply_run_options(h, spec, RunOptions(effort="huge"))


def test_unknown_variant_without_variants_file() -> None:
    from types import SimpleNamespace

    harness = SimpleNamespace(snapshot=SimpleNamespace(variants=None))
    with pytest.raises(UnknownVariant):
        find_variant(harness, "anything")  # type: ignore[arg-type]


# ------------------------------------------------------------------ CLI
def test_print_outputs_only_final_text(cli_env: Path) -> None:
    (cli_env / "notes.txt").write_text("attached words")
    r = runner.invoke(app, ["run", "hello", "--print", "-f", "notes.txt", "--bare", "--thinking", "low"])
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() and "session" not in r.stdout


def test_continue_and_fork(cli_env: Path) -> None:
    first = _json(runner.invoke(app, ["run", "first", "--format", "json", "--title", "t1"]).stdout)
    again = _json(runner.invoke(app, ["run", "second", "-c", "--format", "json"]).stdout)
    assert again["session_id"] == first["session_id"]
    forked = _json(
        runner.invoke(app, ["run", "third", "--fork", first["session_id"], "--format", "json"]).stdout
    )
    assert forked["session_id"] != first["session_id"] and forked["status"] == "completed"


def test_flag_validation(cli_env: Path) -> None:
    cases = [
        ["run", "x", "--print", "--interactive"],
        ["run", "x", "--thinking", "huge"],
        ["run", "x", "-c", "--session", "ses_1"],
        ["run", "x", "--attach", "http://127.0.0.1:9", "--auto"],
        ["run", "x", "--interactive", "--format", "json"],
        ["run", "x", "--allow", ":nope"],
    ]
    for args in cases:
        r = runner.invoke(app, args)
        assert r.exit_code == 2, (args, r.output)


def test_continue_without_sessions(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "x", "-c"])
    assert r.exit_code == 2 and "no previous session" in r.output


def test_allow_deny_auto_run(cli_env: Path) -> None:
    r = runner.invoke(
        app, ["run", "hi", "--allow", "bash:ls *", "--deny", "write", "--auto", "--format", "json"]
    )
    assert r.exit_code == 0, r.output
    assert _json(r.stdout)["status"] == "completed"


def test_interactive_loop_reads_stdin_until_eof(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "first", "--interactive"], input="second\n/exit\n")
    assert r.exit_code == 0, r.output
    assert r.output.count("completed") >= 2


def test_attach_unreachable_server(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "x", "--attach", "http://127.0.0.1:9"])
    assert r.exit_code == 1 and "--attach" in r.output
