"""F5 `deploy/`: LiveKit self-host + opt-in coturn configs parse, carry the spec §26.8 ports, keep
secrets out of the files, and coturn refuses to relay to private ranges. `docker compose config`
is exercised when the CLI is present (it needs no daemon)."""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
LK = DEPLOY / "livekit"
COTURN = DEPLOY / "coturn"
YAMLS = [
    LK / "livekit.yaml",
    LK / "sip.yaml",
    LK / "egress.yaml",
    LK / "compose.livekit.yaml",
    COTURN / "compose.coturn.yaml",
]
SECRETISH = re.compile(
    r"^\s*(api_secret|api_key|static-auth-secret|password|secret)\s*[:=]\s*(?!\$\{)\S+", re.I | re.M
)


def load(p: Path) -> Any:
    return yaml.safe_load(p.read_text())


@pytest.mark.parametrize("path", YAMLS, ids=lambda p: p.name)
def test_yaml_parses_and_has_no_inline_secrets(path: Path) -> None:
    assert isinstance(load(path), dict)
    text = path.read_text()
    assert not SECRETISH.search(text), SECRETISH.search(text)
    assert "keys:" not in [ln.strip() for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def test_livekit_ports_and_turn() -> None:
    cfg = load(LK / "livekit.yaml")
    rtc = cfg["rtc"]
    assert (rtc["port_range_start"], rtc["port_range_end"], rtc["tcp_port"]) == (50000, 60000, 7881)
    assert cfg["redis"]["address"]
    turn = cfg["turn"]
    assert turn["enabled"] and turn["tls_port"] == 5349 and turn["domain"].startswith("turn.")
    assert turn["cert_file"] and turn["key_file"]


def test_sip_and_egress() -> None:
    sip = load(LK / "sip.yaml")
    assert sip["sip_port"] == 5060 and sip["rtp_port"] == "10000-20000" and sip["redis"]["address"]
    egress = load(LK / "egress.yaml")
    assert egress["redis"]["address"] == load(LK / "livekit.yaml")["redis"]["address"]


def test_compose_livekit() -> None:
    svc = load(LK / "compose.livekit.yaml")["services"]
    assert set(svc) == {"redis", "livekit-server", "livekit-sip", "livekit-egress"}
    assert svc["livekit-egress"]["cap_add"] == ["SYS_ADMIN"]
    res = svc["livekit-egress"]["deploy"]["resources"]["reservations"]
    assert float(res["cpus"]) >= 4 and res["memory"] == "4G"
    assert all(s.get("network_mode") == "host" for s in svc.values())  # UDP ranges, no docker-proxy
    assert "${LIVEKIT_API_KEY" in svc["livekit-server"]["environment"]["LIVEKIT_KEYS"]
    for name in ("livekit-sip", "livekit-egress"):
        assert svc[name]["environment"]["LIVEKIT_API_SECRET"].startswith("${LIVEKIT_API_SECRET")


def coturn_conf() -> list[tuple[str, str]]:
    out = []
    for ln in (COTURN / "turnserver.conf").read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            k, _, v = ln.partition("=")
            out.append((k.strip(), v.strip()))
    return out


def test_coturn_auth_ports_and_no_secret() -> None:
    conf = coturn_conf()
    keys = {k for k, _ in conf}
    assert {"use-auth-secret", "no-multicast-peers", "no-tcp-relay", "realm"} <= keys
    assert "static-auth-secret" not in keys  # passed by compose from ${TURN_SECRET}
    d = dict(conf)
    assert d["listening-port"] == "3478" and d["tls-listening-port"] == "5349"
    assert int(d["min-port"]) < int(d["max-port"])
    cmd = load(COTURN / "compose.coturn.yaml")["services"]["coturn"]["command"]
    assert any(c.startswith("--static-auth-secret=${TURN_SECRET") for c in cmd)


@pytest.mark.parametrize(
    "ip",
    [
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
    ],
)
def test_coturn_denies_private_ranges(ip: str) -> None:
    addr = ipaddress.ip_address(ip)
    ranges = []
    for k, v in coturn_conf():
        if k == "denied-peer-ip":
            lo, _, hi = v.partition("-")
            ranges.append((ipaddress.ip_address(lo), ipaddress.ip_address(hi or lo)))
    assert any(lo.version == addr.version and lo <= addr <= hi for lo, hi in ranges), ip  # type: ignore[operator]
    public = ipaddress.ip_address("8.8.8.8")
    assert not any(lo.version == 4 and lo <= public <= hi for lo, hi in ranges)  # type: ignore[operator]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
@pytest.mark.parametrize(
    "compose", [LK / "compose.livekit.yaml", COTURN / "compose.coturn.yaml"], ids=lambda p: p.name
)
def test_docker_compose_config(compose: Path) -> None:
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "LIVEKIT_API_KEY": "k",
        "LIVEKIT_API_SECRET": "s",
        "TURN_SECRET": "t",
    }
    r = subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "-q"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if r.returncode != 0 and "compose" in r.stderr and "is not a docker command" in r.stderr:
        pytest.skip("docker compose plugin missing")
    assert r.returncode == 0, r.stderr
    missing = subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "-q"],
        capture_output=True,
        text=True,
        env={"PATH": env["PATH"]},
        timeout=60,
    )
    assert missing.returncode != 0 and "required variable" in missing.stderr  # secrets must be supplied
