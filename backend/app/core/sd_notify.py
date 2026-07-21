"""Dependency-free systemd ``sd_notify(3)`` client.

Lets a worker tell systemd it has finished starting (``READY=1``) and keep a
``Type=notify`` watchdog alive (``WATCHDOG=1``). Pure stdlib ``socket`` — no
``python-systemd`` / ``cysystemd`` dependency, so it works in the bare venv on
prod and imports cleanly on dev/Windows.

Protocol (see sd_notify(3)):
  * systemd passes the notify socket path in ``$NOTIFY_SOCKET``.
  * Messages are newline-separated ``KEY=VALUE`` payloads sent as a single
    datagram to an ``AF_UNIX``/``SOCK_DGRAM`` socket.
  * A path beginning with ``@`` denotes the Linux abstract namespace — the
    leading ``@`` is replaced with a ``NUL`` byte. A path beginning with ``/``
    is a filesystem socket, used verbatim.

When ``$NOTIFY_SOCKET`` is unset (dev/local/non-systemd) — or ``AF_UNIX`` is
unavailable (Windows) — the notifier is a silent no-op, so importing and
calling it is always safe regardless of platform.
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

logger = logging.getLogger(__name__)


class SystemdNotifier:
    """Small ``sd_notify`` sender. Construct once, reuse for the process life.

    ``enabled`` is True only when a usable ``AF_UNIX`` datagram socket to
    ``$NOTIFY_SOCKET`` was opened. Every ``notify*`` call is a no-op returning
    ``False`` otherwise — the caller never has to branch on the environment.
    """

    def __init__(self, address: Optional[str] = None) -> None:
        # Resolve the socket address: explicit arg wins (tests), else the env.
        self._address: Optional[str] = (
            address if address is not None else os.environ.get("NOTIFY_SOCKET")
        )
        self._sock: Optional[socket.socket] = None

        if not self._address:
            return
        if not hasattr(socket, "AF_UNIX"):
            # Windows / platforms without AF_UNIX — silent no-op.
            logger.debug("sd_notify: AF_UNIX unavailable on this platform; no-op")
            return

        connect_addr = self._address
        # Abstract-namespace socket: leading '@' → NUL byte.
        if connect_addr.startswith("@"):
            connect_addr = "\0" + connect_addr[1:]

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.connect(connect_addr)
        except OSError as exc:
            logger.warning(
                "sd_notify: could not open NOTIFY_SOCKET %r: %s", self._address, exc
            )
            self._sock = None
            return
        self._sock = sock

    @property
    def enabled(self) -> bool:
        """True when a real notify socket is open (systemd Type=notify)."""
        return self._sock is not None

    def notify(self, state: str) -> bool:
        """Send an arbitrary ``KEY=VALUE`` state string. Returns True on send."""
        if self._sock is None:
            return False
        try:
            self._sock.sendall(state.encode("utf-8"))
            return True
        except OSError as exc:
            logger.warning("sd_notify: send failed for %r: %s", state, exc)
            return False

    def notify_ready(self) -> bool:
        """``READY=1`` — startup complete (required by ``Type=notify``)."""
        return self.notify("READY=1")

    def notify_watchdog(self) -> bool:
        """``WATCHDOG=1`` — pet the ``WatchdogSec=`` timer (keep-alive)."""
        return self.notify("WATCHDOG=1")

    def notify_stopping(self) -> bool:
        """``STOPPING=1`` — graceful shutdown has begun."""
        return self.notify("STOPPING=1")

    def close(self) -> None:
        """Close the underlying socket (best-effort, idempotent)."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
