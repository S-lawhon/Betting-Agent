"""Tests for src/watchdog.py."""
import os
import socket
import tempfile
import unittest
from unittest.mock import patch

from src.watchdog import (
    _send,
    notify_ready,
    notify_status,
    notify_stopping,
    notify_watchdog,
)


class TestWatchdogNoSocket(unittest.TestCase):
    """When NOTIFY_SOCKET is unset, all calls are no-ops."""

    def setUp(self):
        # Reset module-level state
        import src.watchdog as wd
        wd._sock = None
        wd._addr = ""

    @patch.dict(os.environ, {}, clear=True)
    def test_notify_watchdog_noop(self):
        import src.watchdog as wd
        wd._sock = None
        self.assertFalse(notify_watchdog())

    @patch.dict(os.environ, {}, clear=True)
    def test_notify_ready_noop(self):
        import src.watchdog as wd
        wd._sock = None
        self.assertFalse(notify_ready())

    @patch.dict(os.environ, {}, clear=True)
    def test_notify_status_noop(self):
        import src.watchdog as wd
        wd._sock = None
        self.assertFalse(notify_status("test"))

    @patch.dict(os.environ, {}, clear=True)
    def test_notify_stopping_noop(self):
        import src.watchdog as wd
        wd._sock = None
        self.assertFalse(notify_stopping())


class TestWatchdogWithSocket(unittest.TestCase):
    """When a real NOTIFY_SOCKET exists, messages are sent."""

    def setUp(self):
        import src.watchdog as wd
        wd._sock = None
        wd._addr = ""

        # Create a temporary Unix socket to receive messages
        self.sock_path = tempfile.mktemp(suffix=".sock")
        self.recv_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.recv_sock.bind(self.sock_path)

    def tearDown(self):
        self.recv_sock.close()
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass

    @patch.dict(os.environ, {})
    def test_watchdog_sends_message(self):
        import src.watchdog as wd
        wd._sock = None
        os.environ["NOTIFY_SOCKET"] = self.sock_path
        self.assertTrue(notify_watchdog())
        data = self.recv_sock.recv(1024)
        self.assertEqual(data, b"WATCHDOG=1")

    @patch.dict(os.environ, {})
    def test_ready_sends_message(self):
        import src.watchdog as wd
        wd._sock = None
        os.environ["NOTIFY_SOCKET"] = self.sock_path
        self.assertTrue(notify_ready())
        data = self.recv_sock.recv(1024)
        self.assertEqual(data, b"READY=1")

    @patch.dict(os.environ, {})
    def test_status_sends_message(self):
        import src.watchdog as wd
        wd._sock = None
        os.environ["NOTIFY_SOCKET"] = self.sock_path
        self.assertTrue(notify_status("cycle 5 done"))
        data = self.recv_sock.recv(1024)
        self.assertEqual(data, b"STATUS=cycle 5 done")


if __name__ == "__main__":
    unittest.main()
