"""Integration tests for carbonyl_agent.daemon.

Uses a mock browser to exercise the daemon server + client protocol
without requiring a live Carbonyl binary.
"""
import json
import os
import socket
import stat
import threading
import time
from unittest.mock import MagicMock

import pytest

from carbonyl_agent.daemon import (
    DaemonClient,
    _BrowserServer,
    is_daemon_live,
)


class MockBrowser:
    """Duck-type CarbonylBrowser for daemon testing."""

    def __init__(self):
        self._screen = MagicMock()
        # Setup screen buffer for find_text and raw_lines
        self._screen.buffer = {
            0: {i: MagicMock(data=c) for i, c in enumerate("Hello World")},
            1: {i: MagicMock(data=c) for i, c in enumerate("Test Line 2")},
        }
        self.sent_text = []
        self.clicked = []
        self.keys_sent = []
        self.navigated = []
        self.drained = []

    def send(self, text):
        self.sent_text.append(text)

    def mouse_move(self, col, row):
        pass

    def click(self, col, row):
        self.clicked.append((col, row))

    def send_key(self, key):
        self.keys_sent.append(key)

    def drain(self, seconds):
        self.drained.append(seconds)

    def navigate(self, url):
        self.navigated.append(url)

    def page_text(self):
        return "Hello World\nTest Line 2"

    def nav_bar_url(self):
        return "https://example.com"

    def close(self, graceful_timeout=5.0):
        pass


@pytest.fixture
def daemon_server(tmp_path):
    """Start a daemon server with a mock browser in a background thread."""
    sock = tmp_path / "test.sock"
    browser = MockBrowser()
    server = _BrowserServer(str(sock), browser)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for server to be ready
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(sock))
            s.close()
            break
        except (ConnectionRefusedError, FileNotFoundError):
            time.sleep(0.05)

    yield {"server": server, "browser": browser, "sock": sock}

    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def client(daemon_server):
    """Connected DaemonClient."""
    c = DaemonClient.__new__(DaemonClient)
    c._sock_path = daemon_server["sock"]
    c._sock = None
    c._buf = ""
    c.connect()
    yield c
    c.disconnect()


# --- Socket permissions ---

class TestSocketPermissions:
    def test_socket_mode_0600(self, daemon_server):
        sock_stat = os.stat(daemon_server["sock"])
        mode = stat.S_IMODE(sock_stat.st_mode)
        # Socket should be owner-only (0600 or stricter)
        assert mode & stat.S_IRWXG == 0, f"Group perms should be 0, got {oct(mode)}"
        assert mode & stat.S_IRWXO == 0, f"Other perms should be 0, got {oct(mode)}"

    def test_parent_dir_mode_0700(self, daemon_server):
        parent = daemon_server["sock"].parent
        parent_stat = os.stat(parent)
        mode = stat.S_IMODE(parent_stat.st_mode)
        assert mode & stat.S_IRWXG == 0, f"Group perms should be 0, got {oct(mode)}"
        assert mode & stat.S_IRWXO == 0, f"Other perms should be 0, got {oct(mode)}"


# --- Protocol: every command ---

class TestDaemonProtocol:
    def test_send(self, client, daemon_server):
        client.send("hello")
        assert "hello" in daemon_server["browser"].sent_text

    def test_click(self, client, daemon_server):
        client.click(10, 5)
        assert (10, 5) in daemon_server["browser"].clicked

    def test_send_key(self, client, daemon_server):
        client.send_key("enter")
        assert "enter" in daemon_server["browser"].keys_sent

    def test_drain(self, client, daemon_server):
        client.drain(0.1)
        assert 0.1 in daemon_server["browser"].drained

    def test_navigate(self, client, daemon_server):
        client.navigate("https://test.com")
        assert "https://test.com" in daemon_server["browser"].navigated

    def test_page_text(self, client):
        text = client.page_text()
        assert "Hello World" in text

    def test_nav_bar_url(self, client):
        url = client.nav_bar_url()
        assert url == "https://example.com"

    def test_find_text(self, client):
        results = client.find_text("Hello")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert results[0]["col"] == 1
        assert results[0]["row"] == 1

    def test_raw_lines(self, client):
        lines = client.raw_lines()
        assert isinstance(lines, list)
        assert len(lines) >= 1
        assert lines[0]["row"] == 1

    def test_mouse_move(self, client):
        # Should not raise
        client.mouse_move(5, 3)


# --- Error handling ---

class TestErrorHandling:
    def test_unknown_command(self, daemon_server):
        """Send an unknown command and expect an error envelope."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(daemon_server["sock"]))
        s.sendall(json.dumps({"cmd": "nonexistent_command"}).encode() + b"\n")
        resp = b""
        while b"\n" not in resp:
            resp += s.recv(4096)
        s.close()
        data = json.loads(resp.split(b"\n")[0])
        assert data["ok"] is False
        assert "error" in data

    def test_invalid_json(self, daemon_server):
        """Send invalid JSON and expect error response."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(daemon_server["sock"]))
        s.sendall(b"not json\n")
        resp = b""
        while b"\n" not in resp:
            resp += s.recv(4096)
        s.close()
        data = json.loads(resp.split(b"\n")[0])
        assert data["ok"] is False


# --- is_daemon_live ---

class TestIsDaemonLive:
    def test_live_daemon(self, daemon_server):
        assert is_daemon_live.__name__ == "is_daemon_live"  # sanity
        # We can't easily test is_daemon_live directly since it uses
        # _sock_path which expects a session name, but we can test the
        # underlying socket connectivity
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(daemon_server["sock"]))
        s.close()  # connected successfully = daemon is live

    def test_no_socket_file(self, tmp_path):
        result = is_daemon_live("nonexistent-session", session_dir=tmp_path)
        assert result is False


# --- close command ---

class TestCloseCommand:
    def test_close_triggers_shutdown(self, daemon_server):
        """The close command should set shutdown_requested on the server."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(daemon_server["sock"]))
        s.sendall(json.dumps({"cmd": "close"}).encode() + b"\n")
        resp = b""
        while b"\n" not in resp:
            resp += s.recv(4096)
        s.close()
        data = json.loads(resp.split(b"\n")[0])
        assert data["ok"] is True
        # Server should have shutdown_requested set
        assert daemon_server["server"].shutdown_requested is True
