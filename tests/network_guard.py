"""Shared guard for the tests that prove a check makes no network connection.

Every socket connect fails the test, on every platform. The single exception is the
standard library's own socket.socketpair(): Windows has no native socket pair, so Python
builds one by connecting a socket to a listener it has just opened on the loopback
address, and asyncio.run() calls it to create every event loop. That one connection is
allowed only while socket.socketpair() itself is running, and only to a loopback address.
"""
import ipaddress
import socket
import threading

_SOCKETPAIR = threading.local()


def _is_loopback(address):
    if not isinstance(address, tuple) or not address:
        return False
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return False


def deny_network(monkeypatch, message="Network attempted"):
    real_connect = socket.socket.connect
    real_socketpair = socket.socketpair

    def connect(sock, address, *args, **kwargs):
        if getattr(_SOCKETPAIR, "active", False) and _is_loopback(address):
            return real_connect(sock, address, *args, **kwargs)
        raise AssertionError(message)

    def socketpair(*args, **kwargs):
        _SOCKETPAIR.active = True
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            _SOCKETPAIR.active = False

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "socketpair", socketpair)
