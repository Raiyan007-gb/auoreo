"""H4: OAuth login for connectors (spec §25.14 "OAuth flows"): `auoreo login <connector>`.

A connector declares `connectors/<name>/oauth.yaml` in any definition root:

    auth_url: https://example.com/oauth/authorize
    token_url: https://example.com/oauth/token
    client_id: abc123                 # public client id (never a client secret)
    scopes: [read, write]
    device_code_url: https://example.com/oauth/device/code   # optional (RFC 8628)
    loopback_port: auto               # or a fixed port registered with the provider

Flows (dict dispatch, tried in order unless one is forced): loopback-PKCE (S256, a one-shot
HTTP listener on 127.0.0.1) → device-code → manual-code (paste the code). Only the
`refresh_token` is persisted, in the secret store as `oauth/<connector>`; access tokens stay
in memory. Token values are never printed or logged.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets as pysecrets
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

OOB_REDIRECT = "urn:ietf:wg:oauth:2.0:oob"
FLOW_ORDER = ("loopback", "device", "manual")


class OAuthError(RuntimeError):
    pass


class OAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auth_url: str
    token_url: str
    client_id: str
    scopes: list[str] = Field(default_factory=list)
    device_code_url: str | None = None
    pkce: Literal["S256"] = "S256"
    loopback_port: int | Literal["auto"] = "auto"
    redirect_uri: str = OOB_REDIRECT
    """Redirect for the manual-code flow (the page that shows the code to copy)."""


def secret_name(connector: str) -> str:
    return f"oauth/{connector}"


def find_config(connector: str, roots: list[Path]) -> Path | None:
    for root in roots:
        path = root / "connectors" / connector / "oauth.yaml"
        if path.is_file():
            return path
    return None


def load_config(path: Path) -> OAuthConfig:
    try:
        return OAuthConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except Exception as exc:
        raise OAuthError(f"{path}: {exc}") from exc


def pkce_pair() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(cfg: OAuthConfig, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in cfg.auth_url else "?"
    return cfg.auth_url + sep + urllib.parse.urlencode({k: v for k, v in query.items() if v})


Post = Callable[[str, dict[str, str]], dict[str, Any]]


def http_post(url: str, data: dict[str, str]) -> dict[str, Any]:
    r = httpx.post(url, data=data, headers={"Accept": "application/json"}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = dict(urllib.parse.parse_qsl(r.text))
    if not isinstance(body, dict):
        raise OAuthError(f"{url}: unexpected response")
    if r.status_code >= 400 and "error" not in body:
        raise OAuthError(f"{url}: HTTP {r.status_code}")
    return body


def _tokens(body: dict[str, Any]) -> dict[str, Any]:
    if "error" in body:
        raise OAuthError(f"token endpoint: {body.get('error')} {body.get('error_description', '')}".strip())
    if not body.get("access_token"):
        raise OAuthError("token endpoint returned no access_token")
    return body


def exchange(cfg: OAuthConfig, code: str, verifier: str, redirect_uri: str, post: Post) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": redirect_uri,
        "client_id": cfg.client_id,
        "code_verifier": verifier,
    }
    return _tokens(post(cfg.token_url, data))


@dataclass
class Io:
    """Side effects of a flow, injectable for tests."""

    say: Callable[[str], None]
    ask: Callable[[str], str]
    open_url: Callable[[str], bool]
    post: Post = field(default=lambda url, data: http_post(url, data))  # late-bound (tests patch it)
    sleep: Callable[[float], None] = time.sleep
    timeout_s: float = 300.0


# ------------------------------------------------------------------ flows
def flow_loopback(cfg: OAuthConfig, io: Io) -> dict[str, Any]:
    verifier, challenge = pkce_pair()
    state = pysecrets.token_urlsafe(24)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if params.get("state") == state:
                got.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"AUOREO login complete; you can close this tab.")

        def log_message(self, *args: Any) -> None:  # keep codes out of stderr
            return

    port = 0 if cfg.loopback_port == "auto" else int(cfg.loopback_port)
    with http.server.HTTPServer(("127.0.0.1", port), Handler) as server:
        redirect = f"http://127.0.0.1:{server.server_address[1]}/callback"
        url = authorize_url(cfg, redirect, state, challenge)
        if not io.open_url(url):
            raise OAuthError("no browser available for the loopback flow")
        io.say(f"opened the browser; if nothing happened, visit:\n  {url}")
        server.timeout = 1.0
        deadline = time.monotonic() + io.timeout_s
        while not got and time.monotonic() < deadline:
            server.handle_request()
    if "error" in got:
        raise OAuthError(f"authorization denied: {got['error']}")
    if "code" not in got:
        raise OAuthError("timed out waiting for the browser redirect")
    return exchange(cfg, got["code"], verifier, redirect, io.post)


def flow_device(cfg: OAuthConfig, io: Io) -> dict[str, Any]:
    if not cfg.device_code_url:
        raise OAuthError("this connector has no device_code_url")
    start = io.post(cfg.device_code_url, {"client_id": cfg.client_id, "scope": " ".join(cfg.scopes)})
    if "device_code" not in start:
        raise OAuthError(f"device authorization failed: {start.get('error', 'no device_code')}")
    where = start.get("verification_uri_complete") or start.get("verification_uri")
    io.say(f"visit {where} and enter the code {start.get('user_code', '')}")
    interval = float(start.get("interval", 5))
    deadline = time.monotonic() + min(float(start.get("expires_in", io.timeout_s)), io.timeout_s)
    grant = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": str(start["device_code"]),
        "client_id": cfg.client_id,
    }
    while time.monotonic() < deadline:
        io.sleep(interval)
        body = io.post(cfg.token_url, grant)
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        return _tokens(body)
    raise OAuthError("device code expired before authorization")


def flow_manual(cfg: OAuthConfig, io: Io) -> dict[str, Any]:
    verifier, challenge = pkce_pair()
    state = pysecrets.token_urlsafe(24)
    url = authorize_url(cfg, cfg.redirect_uri, state, challenge)
    io.say(f"open this URL, approve, then paste the code shown:\n  {url}")
    code = io.ask("code").strip()
    if not code:
        raise OAuthError("no code entered")
    return exchange(cfg, code, verifier, cfg.redirect_uri, io.post)


FLOWS: dict[str, Callable[[OAuthConfig, Io], dict[str, Any]]] = {
    "loopback": flow_loopback,
    "device": flow_device,
    "manual": flow_manual,
}


def login(cfg: OAuthConfig, io: Io, flow: str = "auto") -> tuple[str, dict[str, Any]]:
    """Run the forced flow, or each applicable flow in order until one succeeds."""
    order = FLOW_ORDER if flow == "auto" else (flow,)
    if any(f not in FLOWS for f in order):
        raise OAuthError(f"unknown flow '{flow}' (auto, {', '.join(FLOW_ORDER)})")
    errors: list[str] = []
    for name in order:
        if name == "device" and not cfg.device_code_url and flow == "auto":
            continue
        try:
            return name, FLOWS[name](cfg, io)
        except OAuthError as exc:
            errors.append(f"{name}: {exc}")
            if flow != "auto":
                break
    raise OAuthError("; ".join(errors) or "no flow applies")
