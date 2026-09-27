from __future__ import annotations

import socket

import pytest

from voxmaps_sim.desktop_launcher import choose_port


def test_choose_port_uses_preferred_port_when_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert choose_port("127.0.0.1", port) == port


def test_choose_port_skips_a_port_that_is_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        if port >= 65535:
            pytest.skip("ephemeral port cannot be followed by another valid port")
        selected = choose_port("127.0.0.1", port)
    assert selected > port
    assert selected <= port + 19


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_choose_port_rejects_invalid_values(port: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        choose_port(preferred=port)
