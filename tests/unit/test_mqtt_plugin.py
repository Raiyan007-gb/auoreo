"""MQTT plugin, in process, with real paho-mqtt v2 clients against the minimal in-process broker
in `tests/mqtt_broker.py` (amqtt was not used: it pins websockets==15.0.1, which would
downgrade the server's uvicorn[standard] websockets)."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.plugins.connectors import InboundRouter
from tests.mqtt_broker import Broker

paho = pytest.importorskip("paho.mqtt.client")

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "mqtt" / "plugin.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mqtt_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mq = load()


class FakeHost:
    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self.secrets = secrets or {}
        self.asked: list[str] = []
        self.messages: list[dict[str, Any]] = []
        self.logs: list[tuple[str, str]] = []

    async def secret(self, name: str) -> str | None:
        self.asked.append(name)
        return self.secrets.get(name)

    def message(self, connector: str, chat_id: str, user: str, text: str, reply_to: Any = None) -> None:
        self.messages.append(
            {"name": connector, "chat_id": chat_id, "user": user, "text": text, "reply_to": reply_to}
        )

    def log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


async def until(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
async def broker() -> AsyncIterator[Broker]:
    b = Broker(deny=("forbidden/#",))
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


def config(port: int, **extra: Any) -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": port,
        "client_id": "auoreo-test",
        "username": "agent",
        "subscribe": ["home/+/temp", "forbidden/x"],
        "publish_allow": ["home/lights/+/set"],
        "qos": 2,
        **extra,
    }


@pytest.fixture
async def plugin(broker: Broker) -> AsyncIterator[Any]:
    p = mq.Plugin(FakeHost({"MQTT_PASSWORD": "s3cret"}))
    info = p.initialize(config(broker.port), ["sessions", "secrets:MQTT_PASSWORD"])
    assert [t["name"] for t in info["tools"]] == ["mqtt_publish", "mqtt_subscriptions"]
    assert info["connectors"] == ["mqtt"] and info["deliveries"] == []
    await p.connector_start("mqtt")
    await until(lambda: p.connected and "home/+/temp" in broker.subscribed("auoreo-test"))
    await until(lambda: len(p.granted) == 2)
    try:
        yield p
    finally:
        await p.shutdown()


async def publish_from_client(port: int, topic: str, payload: bytes, *, retain: bool = False) -> None:
    from paho.mqtt.enums import CallbackAPIVersion

    def run() -> None:
        c = paho.Client(CallbackAPIVersion.VERSION2, client_id="sensor-1")
        c.connect("127.0.0.1", port)
        c.loop_start()
        c.publish(topic, payload, qos=1, retain=retain).wait_for_publish(5)
        c.disconnect()
        c.loop_stop()

    await asyncio.to_thread(run)


# ----------------------------------------------------------------- pure helpers
@pytest.mark.parametrize(
    ("pattern", "topic", "ok"),
    [
        ("home/+/temp", "home/kitchen/temp", True),
        ("home/+/temp", "home/kitchen/hum", False),
        ("home/#", "home", True),
        ("home/#", "home/a/b/c", True),
        ("#", "$SYS/broker", False),
        ("home/+", "home/a/b", False),
        ("a/b", "a/b", True),
    ],
)
def test_topic_matches(pattern: str, topic: str, ok: bool) -> None:
    assert mq.topic_matches(pattern, topic) is ok


def test_invalid_filters_are_refused() -> None:
    assert not mq.valid_filter("home/#/x") and not mq.valid_filter("home/a+") and mq.valid_filter("a/+/#")
    with pytest.raises(ValueError, match="invalid MQTT topic filter"):
        mq.Plugin(FakeHost()).initialize({"host": "h", "publish_allow": ["x/#/y"]}, [])


def test_payload_encoding_and_cap() -> None:
    assert mq.encode_payload(b"21.5") == ("21.5", "utf-8", False)
    text, enc, cut = mq.encode_payload(b"\xff\x00\x01")
    assert enc == "base64" and base64.b64decode(text) == b"\xff\x00\x01" and not cut
    text, enc, cut = mq.encode_payload(b"x" * 5000)
    assert len(text) == 4096 and cut


# --------------------------------------------------------- against the broker
async def test_connect_is_persistent_with_password_from_secret(plugin: Any, broker: Broker) -> None:
    assert broker.connects[0] == {"client_id": "auoreo-test", "clean_session": False, "username": True}
    assert plugin.host.asked == ["MQTT_PASSWORD"]
    assert broker.subscribed("auoreo-test") == {"home/+/temp": 2}  # requested at qos 2
    # the broker refused one filter (ACL) → reported, not hidden
    assert plugin.granted == {"home/+/temp": 1, "forbidden/x": -1}


async def test_inbound_on_allowed_topic_reaches_host_as_connector_message(
    plugin: Any, broker: Broker
) -> None:
    await publish_from_client(broker.port, "home/kitchen/temp", b"21.5")
    await until(lambda: len(plugin.host.messages) == 1)
    msg = plugin.host.messages[0]
    assert (
        msg["name"] == "mqtt" and msg["chat_id"] == "home/+/temp" and msg["user"] == "mqtt:home/kitchen/temp"
    )
    assert json.loads(msg["text"]) == {
        "topic": "home/kitchen/temp",
        "payload": "21.5",
        "encoding": "utf-8",
        "retained": False,
        "qos": 1,
    }
    assert msg["reply_to"]["filter"] == "home/+/temp"
    logs = " ".join(m for _, m in plugin.host.logs)
    assert "21.5" not in logs and "[REDACTED payload 4 bytes]" in logs


async def test_subscribe_allowlist_drops_stale_broker_subscriptions(plugin: Any, broker: Broker) -> None:
    # a persistent session can keep filters from an older config; the plugin re-checks every topic
    plugin.client.subscribe("home/#", qos=1)
    await until(lambda: "home/#" in broker.subscribed("auoreo-test"))
    await publish_from_client(broker.port, "home/door/state", b"open-secret")
    await publish_from_client(broker.port, "home/hall/temp", b"\xff\xfe")
    await until(lambda: len(plugin.host.messages) == 1 and plugin.stats["dropped"] >= 1)
    body = json.loads(plugin.host.messages[0]["text"])
    assert body["topic"] == "home/hall/temp" and body["encoding"] == "base64"
    assert all("open-secret" not in m for _, m in plugin.host.logs)


async def test_retained_flag_is_reported(broker: Broker) -> None:
    await publish_from_client(broker.port, "home/attic/temp", b"9", retain=True)
    p = mq.Plugin(FakeHost())
    p.initialize(config(broker.port, username=None, client_id="auoreo-r"), [])
    p.config.pop("username")
    await p.connector_start("mqtt")
    try:
        await until(lambda: len(p.host.messages) == 1)
        assert json.loads(p.host.messages[0]["text"])["retained"] is True
        assert p.host.asked == []  # no username → no secret lookup
    finally:
        await p.shutdown()


async def test_publish_allowlist_enforced(plugin: Any, broker: Broker) -> None:
    ok = await plugin.call_tool("mqtt_publish", {"topic": "home/lights/kitchen/set", "payload": "on"}, "s1")
    assert not ok["is_error"] and "qos 2" in ok["content"]
    assert [(p.topic, p.payload, p.qos) for p in broker.published] == [("home/lights/kitchen/set", b"on", 2)]

    refused = await plugin.call_tool(
        "mqtt_publish", {"topic": "home/locks/front/set", "payload": "open"}, "s1"
    )
    assert refused["is_error"] and "not in publish_allow" in refused["content"]
    wild = await plugin.call_tool("mqtt_publish", {"topic": "home/lights/+/set", "payload": "on"}, "s1")
    assert wild["is_error"] and "wildcards" in wild["content"]
    assert len(broker.published) == 1 and plugin.stats["refused"] == 1

    subs = json.loads((await plugin.call_tool("mqtt_subscriptions", {}, "s1"))["content"])
    assert subs["connected"] is True and subs["publish_allow"] == ["home/lights/+/set"]
    assert subs["subscribe"][0] == {"filter": "home/+/temp", "granted_qos": 1}


async def test_publish_needs_connection_and_allowlist() -> None:
    p = mq.Plugin(FakeHost())
    p.initialize({"host": "h"}, [])
    res = await p.publish({"topic": "a/b", "payload": "x"})
    assert res["is_error"] and "publish_allow is empty" in res["content"]
    p.config_update({"host": "h", "publish_allow": ["a/#"]})
    assert "not connected" in (await p.publish({"topic": "a/b", "payload": "x"}))["content"]


def test_manifest_publish_defaults_to_ask() -> None:
    from auoreo.plugins.bridge import make_tool
    from auoreo.tools.context import ToolResult

    async def call(*_: Any) -> ToolResult:
        return ToolResult("")

    tool = make_tool("mqtt", mq.TOOLS[0], call, default_timeout_s=30)
    assert tool.manifest.name == "plugin__mqtt__mqtt_publish" and tool.manifest.default_action == "ask"


async def test_connector_message_routes_to_a_session(plugin: Any, broker: Broker, services: Services) -> None:
    await publish_from_client(broker.port, "home/kitchen/temp", b"22")
    await until(lambda: len(plugin.host.messages) == 1)
    sent: list[tuple[str, str]] = []

    async def send(target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        sent.append((target, text))
        return {"ok": False, "detail": "plugin provides no delivery"}

    out = await InboundRouter(services, "mqtt").handle(plugin.host.messages[0], send)
    row = await services.get_session(out["session_id"])
    assert row is not None and row.title == "mqtt: mqtt:home/kitchen/temp"
    assert sent and sent[0][0] == "home/+/temp"
