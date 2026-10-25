from __future__ import annotations

from pathlib import Path

from auoreo.core.redaction import StreamRedactor
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.inbox import RunOptions


def chunks(text: str, size: int = 16) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def stream(text: str, size: int = 16) -> str:
    r = StreamRedactor()
    out = "".join(r.feed(c) for c in chunks(text, size))
    return out + r.flush()


def test_task_notification_tag_survives_any_chunking() -> None:
    text = "<task-notification><task-id>x</task-id></task-notification> done"
    for size in (1, 5, 16, 17, 64):
        assert stream(text, size) == text, size


def test_key_split_across_chunks_is_fully_masked() -> None:
    key = "sk-or-v1-" + "a" * 70
    text = f"token: {key} end"
    for size in (3, 16, 50):
        out = stream(text, size)
        assert key not in out and "a" * 20 not in out and out.endswith(" end"), size


async def test_delta_events_are_not_masked_at_chunk_boundaries(
    services: Services, gateway: FakeGateway
) -> None:
    reply = "<task-notification><result>ok</result></task-notification>"
    gateway.queue(Scripted(text=reply))
    sess = await services.create_session()
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    await services.coordinator.submit(sess.id, "go", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    deltas = []
    while not sub.queue.empty():
        e = sub.queue.get_nowait()
        if e.type == "message.delta":
            deltas.append(e.data["text"])
    assert "".join(deltas) == reply


async def test_inject_does_not_flip_interactive(services: Services) -> None:
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    rt.interactive = False
    await services.coordinator.submit(sess.id, "<mail>hi</mail>", "inject")
    assert rt.interactive is False
    await services.coordinator.submit(sess.id, "steer", "steer")
    assert rt.interactive is False


async def test_run_options_limit_steps_tools_and_memory(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("A")
    gateway.queue(
        Scripted(tool_calls=[("read", {"path": "a.txt"}), ("bash", {"command": "echo x"})]),
        Scripted(text="done"),
    )
    sess = await services.create_session()
    opts = RunOptions(max_steps=2, tools_allow=("read", "glob"), tools_deny=("glob",), skip_memory=True)
    await services.coordinator.submit(sess.id, "go", interactive=False, options=opts)
    await services.coordinator.wait(sess.id, 10)
    first = gateway.requests[0]
    assert [t["function"]["name"] for t in first.tools] == ["read"]
    assert "<memory-context>" not in str(first.messages)
    hist = await services.coordinator.runner.load_history(sess.id)
    bash_result = next(m for m in hist if m.get("role") == "tool" and "call_2" in m.get("tool_call_id", ""))
    assert "unknown tool" in bash_result["content"]
    assert gateway.requests[1].tool_choice == "none"  # step 2 of 2 is the forced final step
