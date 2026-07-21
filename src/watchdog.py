"""
src/watchdog.py
───────────────
Systemd watchdog integration.

Call ``notify_watchdog()`` at the end of each scan cycle so systemd knows
the process is alive.  If WatchdogSec expires without a ping, systemd
restarts the service.

Also supports ``notify_ready()`` for Type=notify services (signals that
startup is complete).

Works without any pip dependency — uses the raw ``$NOTIFY_SOCKET`` that
systemd sets when ``WatchdogSec`` or ``Type=notify`` is configured.
Falls back to no-op when not running under systemd.
"""

import logging
import os
import socket

logger = logging.getLogger(__name__)

_sock = None
_addr: str = ""


def _get_socket():
    """Lazy-init the notification socket (AF_UNIX DGRAM)."""
    global _sock, _addr
    if _sock is not None:
        return _sock

    addr = os.environ.get("NOTIFY_SOCKET", "")
    if not addr:
        return None

    # Abstract socket (starts with @) or filesystem socket
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    try:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        _addr = addr
        return _sock
    except OSError as exc:
        logger.debug("watchdog: failed to create notify socket: %s", exc)
        return None


def _send(msg: str) -> bool:
    """Send a message to systemd's notify socket."""
    sock = _get_socket()
    if sock is None:
        return False
    try:
        sock.sendto(msg.encode(), _addr)
        return True
    except OSError as exc:
        logger.debug("watchdog: sendto failed: %s", exc)
        return False


def notify_watchdog() -> bool:
    """Ping the watchdog timer.  Call once per scan cycle."""
    return _send("WATCHDOG=1")


def notify_ready() -> bool:
    """Signal that service startup is complete (Type=notify)."""
    return _send("READY=1")


def notify_status(status: str) -> bool:
    """Set a human-readable status string visible in ``systemctl status``."""
    return _send("STATUS={}".format(status))


def notify_stopping() -> bool:
    """Signal that the service is shutting down gracefully."""
    return _send("STOPPING=1")
