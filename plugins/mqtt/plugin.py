"""MQTT connector + tools for AUOREO (paho-mqtt v2).

Client: `mqtt.Client(CallbackAPIVersion.VERSION2, client_id=..., clean_session=False)` so QoS 1/2
subscriptions and queued messages survive reconnects, `connect_async` + `loop_start()` (paho's
network thread). Callbacks hop to the plugin's asyncio loop with `call_soon_threadsafe`.

Inbound: a message whose topic matches a configured `subscribe` filter becomes a connector
message (`HostAPI.message`) whose text is JSON `{topic, payload, encoding, retained, qos}`;
the payload is cut at 4 KiB and sent as utf-8 or base64. Topics outside the allowlist are
dropped even when the broker delivers them (a persistent session may still hold filters from an
older config). Logs never contain payloads: `[REDACTED payload N bytes]`.

Tools: `mqtt_publish` (topic must match `publish_allow`; the host asks for approval unless a
permission rule allows `plugin__mqtt__mqtt_publish`) and `mqtt_subscriptions`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
from collections.abc import Callable
from typing import Any

NAME = "mqtt"
MAX_PAYLOAD = 4096
PUBLISH_TIMEOUT_S = 10.0

TOOLS: list[dict[str, Any]] = [
    {
        "name": "mqtt_publish",
        "description": "Publish a message to an MQTT topic (only topics matching publish_allow).",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "minLength": 1, "description": "Concrete topic (no + or #)"},
                "payload": {"type": "string", "maxLength": 65536},
                "qos": {"type": "integer", "enum": [0, 1, 2]},
                "retain": {"type": "boolean", "default": False},
            },
            "required": ["topic", "payload"],
            "additionalProperties": False,
        },
    },
    {
        "name": "mqtt_subscriptions",
        "description": "List the MQTT topic filters this connector subscribes to and may publish to.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT filter semantics: `+` is one level, a trailing `#` is any number of levels
    (including none); `$`-topics only match filters that start with `$`."""
    if topic.startswith("$") and not pattern.startswith("$"):
        return False
    p_parts, t_parts = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p_parts):
        if part == "#":
            return i == len(p_parts) - 1
        if i >= len(t_parts) or (part != "+" and part != t_parts[i]):
            return False
    return len(p_parts) == len(t_parts)


def valid_filter(pattern: str) -> bool:
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        if "#" in part and (part != "#" or i != len(parts) - 1):
            return False
        if "+" in part and part != "+":
            return False
    return bool(pattern) and "\x00" not in pattern


def encode_payload(raw: bytes) -> tuple[str, str, bool]:
    """`(text, encoding, truncated)`: utf-8 when it decodes, else base64; at most 4 KiB of input."""
    cut = raw[:MAX_PAYLOAD]
    try:
        return cut.decode("utf-8"), "utf-8", len(raw) > MAX_PAYLOAD
    except UnicodeDecodeError:
        return base64.b64encode(cut).decode("ascii"), "base64", len(raw) > MAX_PAYLOAD


def redact(raw: bytes) -> str:
    return f"[REDACTED payload {len(raw)} bytes]"


def _paho() -> Any:
    import paho.mqtt.client as mqtt

    return mqtt


def default_client(client_id: str) -> Any:
    mqtt = _paho()
    from paho.mqtt.enums import CallbackAPIVersion

    return mqtt.Client(CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False)


class Plugin:
    def __init__(self, host: Any, client_factory: Callable[[str], Any] | None = None) -> None:
        self.host = host
        self.client_factory = client_factory or default_client
        self.config: dict[str, Any] = {}
        self.client: Any = None
        self.connected = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.granted: dict[str, int] = {}
        self.stats = {"received": 0, "dropped": 0, "published": 0, "refused": 0}

    # ------------------------------------------------------------- protocol
    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        return {"tools": TOOLS, "deliveries": [], "connectors": [NAME], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        bad = [f for f in [*self.subscribe, *self.publish_allow] if not valid_filter(f)]
        if bad:
            raise ValueError(f"invalid MQTT topic filter(s): {bad}")
        return {}

    @property
    def subscribe(self) -> list[str]:
        return [str(t) for t in self.config.get("subscribe", [])]

    @property
    def publish_allow(self) -> list[str]:
        return [str(t) for t in self.config.get("publish_allow", [])]

    @property
    def qos(self) -> int:
        return int(self.config.get("qos", 1))

    async def connector_start(self, name: str) -> dict[str, Any]:
        if self.client is not None:
            return {}
        self.loop = asyncio.get_running_loop()
        host = str(self.config.get("host") or "")
        if not host:
            raise ValueError("plugins.mqtt.host is not set")
        client_id = str(self.config.get("client_id") or f"auoreo-{socket.gethostname()}")[:64]
        try:
            client = self.client_factory(client_id)
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is not installed in the plugin venv") from exc
        if self.config.get("username"):
            secret_name = str(self.config.get("password_secret") or "MQTT_PASSWORD")
            password = await self.host.secret(secret_name)
            client.username_pw_set(str(self.config["username"]), password)
        if self.config.get("tls"):
            client.tls_set()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.connect_async(host, int(self.config.get("port", 1883)), keepalive=60)
        client.loop_start()
        self.client = client
        self.host.log("info", f"mqtt connecting to {host}:{self.config.get('port', 1883)} as {client_id}")
        return {}

    async def connector_stop(self, name: str) -> dict[str, Any]:
        client, self.client = self.client, None
        if client is not None:
            with contextlib.suppress(Exception):
                client.disconnect()
            await asyncio.to_thread(client.loop_stop)
        self.connected = False
        return {}

    async def shutdown(self) -> None:
        await self.connector_stop(NAME)

    # ----------------------------------------------- paho thread → asyncio
    def _hop(self, fn: Callable[..., Any], *args: Any) -> None:
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(fn, *args)

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
        if getattr(reason_code, "is_failure", False):
            self._hop(self.host.log, "warning", f"mqtt connect refused: {reason_code}")
            return
        self.connected = True
        subs = [(t, self.qos) for t in self.subscribe]
        if subs:  # (re)subscribe on every connect, as paho recommends
            client.subscribe(subs)
        self._hop(self.host.log, "info", f"mqtt connected; subscribing to {len(subs)} filter(s)")

    def _on_disconnect(
        self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any
    ) -> None:
        self.connected = False
        self._hop(self.host.log, "info", f"mqtt disconnected: {reason_code}")

    def _on_subscribe(self, client: Any, userdata: Any, mid: int, codes: Any, properties: Any) -> None:
        for topic, code in zip(self.subscribe, codes or [], strict=False):
            failed = getattr(code, "is_failure", False)
            self.granted[topic] = -1 if failed else int(getattr(code, "value", code))

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        self._hop(self.handle_message, str(msg.topic), bytes(msg.payload), bool(msg.retain), int(msg.qos))

    # ------------------------------------------------------------- inbound
    def handle_message(self, topic: str, raw: bytes, retained: bool, qos: int) -> bool:
        matched = next((f for f in self.subscribe if topic_matches(f, topic)), None)
        if matched is None:
            self.stats["dropped"] += 1
            self.host.log("warning", f"mqtt dropped message on non-allowlisted topic {topic} {redact(raw)}")
            return False
        self.stats["received"] += 1
        text, encoding, truncated = encode_payload(raw)
        body = {"topic": topic, "payload": text, "encoding": encoding, "retained": retained, "qos": qos}
        if truncated:
            body["truncated_from"] = len(raw)
        chat_id = topic if self.config.get("session_key") == "topic" else matched
        meta = {"topic": topic, "filter": matched, "retained": retained, "qos": qos}
        self.host.log("info", f"mqtt inbound {topic} qos={qos} retained={retained} {redact(raw)}")
        self.host.message(NAME, chat_id, f"mqtt:{topic}", json.dumps(body), reply_to=meta)
        return True

    # --------------------------------------------------------------- tools
    async def call_tool(self, name: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        if name == "mqtt_subscriptions":
            return {"content": json.dumps(self.describe()), "is_error": False}
        if name == "mqtt_publish":
            return await self.publish(args)
        return {"content": f"unknown tool {name}", "is_error": True}

    def describe(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "subscribe": [{"filter": f, "granted_qos": self.granted.get(f)} for f in self.subscribe],
            "publish_allow": self.publish_allow,
            "qos": self.qos,
            "stats": dict(self.stats),
        }

    async def publish(self, args: dict[str, Any]) -> dict[str, Any]:
        topic = str(args.get("topic", ""))
        if not topic or "+" in topic or "#" in topic:
            return {"content": "topic must be a concrete topic (no + or # wildcards)", "is_error": True}
        if not any(topic_matches(f, topic) for f in self.publish_allow):
            self.stats["refused"] += 1
            allowed = self.publish_allow or "nothing (publish_allow is empty)"
            return {"content": f"topic '{topic}' is not in publish_allow: {allowed}", "is_error": True}
        if self.client is None or not self.connected:
            return {"content": "mqtt is not connected", "is_error": True}
        qos = int(args.get("qos", self.qos))
        info = self.client.publish(
            topic, str(args.get("payload", "")).encode(), qos=qos, retain=bool(args.get("retain"))
        )
        if qos > 0:
            await asyncio.to_thread(info.wait_for_publish, PUBLISH_TIMEOUT_S)
        if getattr(info, "rc", 0) != 0 or (qos > 0 and not info.is_published()):
            return {
                "content": f"publish to '{topic}' failed (rc={getattr(info, 'rc', '?')})",
                "is_error": True,
            }
        self.stats["published"] += 1
        return {"content": f"published to {topic} (qos {qos}, mid {info.mid})", "is_error": False}
