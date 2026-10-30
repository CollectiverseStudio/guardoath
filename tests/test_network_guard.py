"""The shared no-network guard blocks every connection except the standard library's own
loopback socket pair, which asyncio needs to start an event loop on Windows."""
import asyncio
import socket

import pytest

import network_guard
from network_guard import deny_network


@pytest.mark.parametrize("address", [("127.0.0.1", 9), ("203.0.113.10", 443), ("example.com", 443)])
def test_every_direct_connect_is_denied(monkeypatch, address):
    deny_network(monkeypatch, "Network attempted")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError, match="^Network attempted$"):
            sock.connect(address)


def test_create_connection_is_denied(monkeypatch):
    deny_network(monkeypatch, "Network attempted")
    with pytest.raises(AssertionError, match="^Network attempted$"):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_custom_message_is_reported(monkeypatch):
    deny_network(monkeypatch, "Execution/network attempted")
    with socket.socket() as sock, pytest.raises(AssertionError, match="^Execution/network attempted$"):
        sock.connect(("127.0.0.1", 9))


def test_event_loop_still_starts(monkeypatch):
    deny_network(monkeypatch)
    assert asyncio.run(asyncio.sleep(0, result="ran")) == "ran"


def test_socketpair_works_and_does_not_open_the_guard(monkeypatch):
    deny_network(monkeypatch)
    first, second = socket.socketpair()
    with first, second:
        first.sendall(b"x")
        assert second.recv(1) == b"x"
    with socket.socket() as sock, pytest.raises(AssertionError):
        sock.connect(("127.0.0.1", 9))


def test_only_loopback_is_allowed_inside_socketpair(monkeypatch):
    deny_network(monkeypatch)
    network_guard._SOCKETPAIR.active = True
    try:
        with socket.socket() as sock, pytest.raises(AssertionError):
            sock.connect(("203.0.113.10", 443))
    finally:
        network_guard._SOCKETPAIR.active = False
