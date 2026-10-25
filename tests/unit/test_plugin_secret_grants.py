"""Plugin `consumes: secrets:` grants: exact names and narrow prefix globs only."""

from __future__ import annotations

import pytest

from auoreo.plugins.grants import secret_granted


@pytest.mark.parametrize(
    ("name", "consumes", "ok"),
    [
        ("TELEGRAM_BOT_TOKEN", ["secrets:TELEGRAM_BOT_TOKEN"], True),
        ("OTHER", ["secrets:TELEGRAM_BOT_TOKEN"], False),
        ("MIIO_TOKEN_LAMP", ["secrets:MIIO_TOKEN_*"], True),
        ("MQTT_PASSWORD", ["secrets:MIIO_TOKEN_*"], False),
        ("OPENROUTER_API_KEY", ["secrets:*"], False),
        ("ABC", ["secrets:AB*"], False),
        ("MIIO_TOKEN_X", ["secrets:MI*O_*"], False),
        ("MIIO_TOKEN_X", ["sessions"], False),
    ],
)
def test_secret_grants(name: str, consumes: list[str], ok: bool) -> None:
    assert secret_granted(name, consumes) is ok
