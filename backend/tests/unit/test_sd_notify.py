"""Unit tests for the dependency-free systemd sd_notify client.

The datagram path needs AF_UNIX (Linux CI/prod). On platforms without it
(Windows dev boxes) those tests skip with a clear reason, but the no-op path
and message formatting are still verified everywhere.
"""
from __future__ import annotations

import os
import socket

import pytest

from app.core.sd_notify import SystemdNotifier

_HAS_AF_UNIX = hasattr(socket, "AF_UNIX")


def test_noop_when_notify_socket_unset(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    n = SystemdNotifier()
    assert n.enabled is False
    # Every notify is a safe no-op returning False.
    assert n.notify_ready() is False
    assert n.notify_watchdog() is False
    assert n.notify("STATUS=whatever") is False


def test_explicit_empty_address_is_noop():
    n = SystemdNotifier(address="")
    assert n.enabled is False
    assert n.notify_ready() is False


def test_message_formatting_via_fake_socket(monkeypatch):
    """Verify the exact wire strings without needing a real socket
    (cross-platform)."""
    sent: list[bytes] = []

    class _FakeSock:
        def sendall(self, data):
            sent.append(data)

        def close(self):
            pass

    n = SystemdNotifier(address="/run/does-not-matter")
    # Force-inject a fake socket so _send has something to write to.
    n._sock = _FakeSock()  # type: ignore[attr-defined]

    assert n.enabled is True
    assert n.notify_ready() is True
    assert n.notify_watchdog() is True
    assert n.notify_stopping() is True
    assert sent == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="AF_UNIX unavailable (runs on Linux CI/prod)")
def test_sends_real_datagrams_to_notify_socket(tmp_path, monkeypatch):
    sock_path = str(tmp_path / "notify.sock")
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(sock_path)
    receiver.settimeout(2.0)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
        n = SystemdNotifier()
        assert n.enabled is True

        assert n.notify_ready() is True
        assert receiver.recv(64) == b"READY=1"

        assert n.notify_watchdog() is True
        assert receiver.recv(64) == b"WATCHDOG=1"

        n.close()
        assert n.enabled is False
    finally:
        receiver.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


@pytest.mark.skipif(not _HAS_AF_UNIX, reason="AF_UNIX unavailable (runs on Linux CI/prod)")
def test_bad_socket_path_degrades_to_noop(monkeypatch, tmp_path):
    # A NOTIFY_SOCKET that nothing is listening on: connect may or may not
    # error depending on the OS, but constructing must never raise and calls
    # must never raise.
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "nobody-here.sock"))
    n = SystemdNotifier()  # must not raise
    # notify* must not raise regardless of whether the socket connected.
    n.notify_ready()
    n.notify_watchdog()
