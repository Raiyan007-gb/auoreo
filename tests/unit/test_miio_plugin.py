"""miIO plugin, in process, with a fake `miio` module (no Xiaomi device here)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "miio" / "plugin.py"
TOKEN = "0123456789abcdef0123456789abcdef"
CONFIG = {
    "devices": [
        {"name": "purifier", "ip": "192.168.1.20", "token_secret": "MIIO_TOKEN_PURIFIER", "model": "zhimi.x"},
        {"name": "lamp", "ip": "192.168.1.21", "token_secret": "MIIO_TOKEN_LAMP"},
    ],
    "timeout_s": 3,
}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("miio_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mi = load()


class FakeDevice:
    created: list[dict[str, Any]] = []
    sent: list[tuple[str, Any]] = []

    def __init__(self, ip: str, token: str, timeout: int | None = None, *, model: str | None = None) -> None:
        FakeDevice.created.append({"ip": ip, "token": token, "timeout": timeout, "model": model})

    def info(self) -> Any:
        return SimpleNamespace(
            model="zhimi.airpurifier.mb4",
            firmware_version="2.1.8",
            hardware_version="esp32",
            mac_address="aa:bb:cc:dd:ee:ff",
            raw={"token": TOKEN},
        )

    def send(self, method: str, params: Any) -> Any:
        FakeDevice.sent.append((method, params))
        if method == "boom":
            raise TimeoutError(f"no response from device (token {TOKEN} must never leak)")
        return ["ok"]


FAKE_MIIO = SimpleNamespace(Device=FakeDevice, __version__="0.5.12-fake")


class FakeHost:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets
        self.asked: list[str] = []
        self.logs: list[tuple[str, str]] = []

    async def secret(self, name: str) -> str | None:
        self.asked.append(name)
        if not name.startswith("MIIO_TOKEN_"):
            raise PermissionError(f"secret '{name}' is not declared in consumes")
        return self.secrets.get(name)

    def log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeDevice.created, FakeDevice.sent = [], []


def make(importer: Any = lambda: FAKE_MIIO, secrets: dict[str, str] | None = None) -> Any:
    p = mi.Plugin(FakeHost({"MIIO_TOKEN_PURIFIER": TOKEN} if secrets is None else secrets), importer=importer)
    info = p.initialize(CONFIG, ["secrets:MIIO_TOKEN_*"])
    assert [t["name"] for t in info["tools"]] == ["miio_info", "miio_call"]
    return p


async def test_info_never_returns_the_token() -> None:
    p = make()
    res = await p.call_tool("miio_info", {"device": "purifier"}, "s1")
    assert not res["is_error"]
    body = json.loads(res["content"])
    assert body == {
        "device": "purifier",
        "ip": "192.168.1.20",
        "model": "zhimi.airpurifier.mb4",
        "firmware_version": "2.1.8",
        "hardware_version": "esp32",
        "mac_address": "aa:bb:cc:dd:ee:ff",
    }
    assert TOKEN not in res["content"]
    assert FakeDevice.created == [{"ip": "192.168.1.20", "token": TOKEN, "timeout": 3, "model": "zhimi.x"}]


async def test_call_sends_method_and_params_and_caches_the_token() -> None:
    p = make()
    res = await p.call_tool("miio_call", {"device": "purifier", "method": "set_power", "params": ["on"]}, "s")
    assert json.loads(res["content"]) == {"device": "purifier", "method": "set_power", "result": ["ok"]}
    await p.call_tool("miio_call", {"device": "purifier", "method": "get_prop", "params": {"did": "x"}}, "s")
    assert FakeDevice.sent == [("set_power", ["on"]), ("get_prop", {"did": "x"})]
    assert p.host.asked == ["MIIO_TOKEN_PURIFIER"]  # once


async def test_errors_are_honest_and_token_free() -> None:
    p = make()
    bad = await p.call_tool("miio_call", {"device": "purifier", "method": "rm -rf"}, "s")
    assert bad["is_error"] and "invalid miIO method" in bad["content"]
    unknown = await p.call_tool("miio_info", {"device": "fridge"}, "s")
    assert unknown["is_error"] and "unknown device 'fridge'" in unknown["content"]
    missing = await p.call_tool("miio_info", {"device": "lamp"}, "s")
    assert missing["is_error"] and "MIIO_TOKEN_LAMP is missing" in missing["content"]
    boom = await p.call_tool("miio_call", {"device": "purifier", "method": "boom"}, "s")
    assert boom["is_error"] and "TimeoutError" in boom["content"]
    assert TOKEN not in boom["content"] and "[REDACTED]" in boom["content"]
    assert "MIIO_TOKEN" not in str(p.host.logs)


async def test_malformed_token_is_refused() -> None:
    p = make(secrets={"MIIO_TOKEN_PURIFIER": "not-a-token"})
    res = await p.call_tool("miio_info", {"device": "purifier"}, "s")
    assert res["is_error"] and "not a 32-hex" in res["content"] and FakeDevice.created == []


async def test_missing_python_miio_is_reported() -> None:
    def importer() -> Any:
        raise ImportError("No module named 'miio'")

    p = make(importer=importer)
    assert p.check() == {"ok": False, "detail": mi.INSTALL_HINT}
    assert p.host.logs == [("warning", f"miio: {mi.INSTALL_HINT}")]
    res = await p.call_tool("miio_info", {"device": "purifier"}, "s")
    assert res["is_error"] and "python-miio is not installed" in res["content"]
    assert make().check()["detail"] == "python-miio 0.5.12-fake; 2 device(s)"


async def test_host_refusing_the_secret_explains_the_consumes_gap() -> None:
    p = make()
    p.config_update({"devices": [{"name": "x", "ip": "1.2.3.4", "token_secret": "OTHER"}]})
    res = await p.call_tool("miio_info", {"device": "x"}, "s")
    assert res["is_error"] and "declare 'secrets:OTHER'" in res["content"]


def test_default_import_is_lazy() -> None:
    # importing the plugin module never imports python-miio
    assert "miio" not in mi.__dict__ and callable(mi._miio)
