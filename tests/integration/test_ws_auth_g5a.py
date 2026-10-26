"""G5a: `serve --ws-auth off` lets `/ws` skip the token (loopback only); `token` keeps it."""

from __future__ import annotations

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from tests.integration.test_ws import hello, running_server


async def test_ws_auth_off_skips_token_but_http_still_needs_it(
    services: Services, settings: Settings
) -> None:
    settings.server_password = "s3cret-pass"
    settings.cli.ws_auth = "off"
    async with running_server(services, settings) as url:
        async with connect(url) as ws:  # no token
            assert (await hello(ws))["server_seq"] >= 0
        import httpx

        http = url.replace("ws://", "http://").removesuffix("/ws")
        async with httpx.AsyncClient() as c:
            assert (await c.get(http + "/api/sessions")).status_code == 401


async def test_ws_auth_token_default(services: Services, settings: Settings) -> None:
    settings.server_password = "s3cret-pass"
    assert settings.cli.ws_auth == "token"
    async with running_server(services, settings) as url:
        with pytest.raises(InvalidStatus):
            async with connect(url):
                pass
