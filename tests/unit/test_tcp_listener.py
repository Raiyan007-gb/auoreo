"""A second server must not bind a port that is already listening (Windows SO_REUSEADDR would allow it)."""

from __future__ import annotations

import pytest

from auoreo.cli.common import tcp_listener


def test_second_listener_on_a_busy_port_fails() -> None:
    first = tcp_listener("127.0.0.1")
    first.bind(("127.0.0.1", 0))
    first.listen()
    port = first.getsockname()[1]
    second = tcp_listener("127.0.0.1")
    try:
        with pytest.raises(OSError):
            second.bind(("127.0.0.1", port))
    finally:
        second.close()
        first.close()
