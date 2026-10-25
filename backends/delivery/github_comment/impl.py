"""GitHub comment delivery: `POST /repos/{owner}/{repo}/issues/{number}/comments`."""

from __future__ import annotations

import re
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.delivery import DeliveryProvider, DeliveryReceipt
from auoreo.core.secrets import SecretStore

API = "https://api.github.com"
TOKEN = "github/token"
TARGET_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)#(?P<number>\d+)$")
MAX_BODY = 65_000  # GitHub caps comment bodies at 65 536 chars


class Backend(DeliveryProvider):
    def _token(self) -> str | None:
        if self.services is not None:
            return self.services.secrets.get(TOKEN)
        if self.settings is not None:
            return SecretStore(self.settings.home, self.settings.keyring).get(TOKEN)
        return None

    async def check(self) -> BackendStatus:
        if self._token():
            return BackendStatus(True, f"secret {TOKEN} is set")
        return BackendStatus(False, f"secret {TOKEN} is missing", f"auoreo secrets set {TOKEN}")

    async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt:
        m = TARGET_RE.match(target or "")
        if m is None:
            return DeliveryReceipt(False, f"target must be owner/repo#number, got {target!r}")
        token = self._token()
        if not token:
            return DeliveryReceipt(False, f"secret {TOKEN} is missing")
        url = f"{API}/repos/{m['owner']}/{m['repo']}/issues/{m['number']}/comments"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "auoreo",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(url, headers=headers, json={"body": text[:MAX_BODY]})
        except httpx.HTTPError as exc:
            return DeliveryReceipt(False, f"github request failed: {exc.__class__.__name__}")
        if r.status_code >= 300:
            return DeliveryReceipt(False, f"github answered {r.status_code}")
        body = r.json() if r.content else {}
        return DeliveryReceipt(True, str(body.get("html_url", "")), str(body.get("id", "")) or None)
