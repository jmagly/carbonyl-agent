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
    # Attributes added by #40 (backend awareness). Tests bypass __init__
    # to inject a custom socket path; mimic the defaults here.
    c._require_backend = None
    c.backend = None
    c.protocol_version = 0
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


# --- Backend awareness (#40) ---


class TestBackendHandshake:
    def test_hello_returns_input_backend(self, daemon_server):
        """The hello command exposes the daemon's input_backend."""
        # MockBrowser has no input_backend attribute → defensive default 'pty'
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(daemon_server["sock"]))
        s.sendall(json.dumps({"cmd": "hello"}).encode() + b"\n")
        resp = b""
        while b"\n" not in resp:
            resp += s.recv(4096)
        s.close()
        data = json.loads(resp.split(b"\n")[0])
        assert data["ok"] is True
        assert data["result"]["input_backend"] == "pty"
        assert "protocol_version" in data["result"]

    def test_hello_reflects_browser_backend(self, daemon_server):
        """When the browser has input_backend='uinput', hello reports it."""
        # MockBrowser doesn't set input_backend; inject it for this test.
        daemon_server["browser"].input_backend = "uinput"
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect(str(daemon_server["sock"]))
            s.sendall(json.dumps({"cmd": "hello"}).encode() + b"\n")
            resp = b""
            while b"\n" not in resp:
                resp += s.recv(4096)
            s.close()
            data = json.loads(resp.split(b"\n")[0])
            assert data["result"]["input_backend"] == "uinput"
        finally:
            del daemon_server["browser"].input_backend

    def test_client_records_backend_on_connect(self, client):
        """DaemonClient.connect() populates .backend and .protocol_version."""
        # client fixture connects and yields; backend should be populated
        assert client.backend == "pty"
        assert client.protocol_version >= 1

    def test_require_backend_match_succeeds(self, daemon_server):
        """DaemonClient(require_backend=...) succeeds when backend matches."""
        from carbonyl_agent.daemon import DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = "pty"
        c.backend = None
        c.protocol_version = 0
        c.connect()
        assert c.backend == "pty"
        c.disconnect()

    def test_require_backend_mismatch_raises(self, daemon_server):
        """DaemonClient with require_backend='uinput' against pty daemon raises."""
        from carbonyl_agent.daemon import BackendMismatchError, DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = "uinput"
        c.backend = None
        c.protocol_version = 0
        with pytest.raises(BackendMismatchError, match="uinput"):
            c.connect()


class TestStartDaemonBackend:
    def test_invalid_backend_raises_valueerror(self):
        from carbonyl_agent.daemon import start_daemon
        with pytest.raises(ValueError, match="backend"):
            start_daemon("nope", backend="bogus")

    def test_uinput_backend_preflight_failure_raises_oserror(self, monkeypatch):
        from carbonyl_agent import uinput_emitter
        from carbonyl_agent.daemon import start_daemon
        # Force python-uinput unavailable so the preflight fails
        monkeypatch.setattr(uinput_emitter, "_UINPUT_AVAILABLE", False)
        with pytest.raises(OSError, match="pre-flight failed"):
            start_daemon("preflight-test", backend="uinput")


# --- Public surface (#47) ---


class TestPublicSurface:
    def test_sock_path_is_public(self, tmp_path):
        from carbonyl_agent.daemon import sock_path
        p = sock_path("alpha", session_dir=tmp_path)
        assert p == tmp_path / "alpha.sock"

    def test_default_socket_dir_exposed(self):
        from carbonyl_agent import DEFAULT_SOCKET_DIR
        assert DEFAULT_SOCKET_DIR is not None
        # Should be the default sessions root
        assert str(DEFAULT_SOCKET_DIR).endswith("carbonyl/sessions")

    def test_top_level_reexports_present(self):
        import carbonyl_agent
        for name in ("sock_path", "DEFAULT_SOCKET_DIR", "is_daemon_live",
                    "DaemonClient", "BackendMismatchError"):
            assert hasattr(carbonyl_agent, name), f"missing re-export: {name}"


class TestPing:
    def test_ping_returns_true_when_handshake_succeeds(self, client):
        assert client.ping() is True

    def test_ping_returns_false_when_disconnected(self, daemon_server):
        from carbonyl_agent.daemon import DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        # Never connected
        assert c.ping() is False


class TestWaitForRenderSettle:
    """Issue #50: same readiness probe as CarbonylBrowser, lifted onto
    DaemonClient so daemon-mode visual-capture tests don't re-implement
    the polling loop inline."""

    def _patch_client(self, client, sequence):
        idx = {"i": 0}

        def _drain(_seconds):
            idx["i"] = min(idx["i"] + 1, len(sequence) - 1)

        def _text():
            return sequence[idx["i"]]

        client.drain = _drain  # type: ignore[method-assign]
        client.page_text = _text  # type: ignore[method-assign]

    def test_returns_true_when_buffer_settles(self, client):
        self._patch_client(client, ["a", "ab", "abc", "abc", "abc", "abc"])
        assert client.wait_for_render_settle(timeout=2.0, idle_ms=50, poll_ms=10) is True

    def test_returns_false_when_buffer_never_settles(self, client):
        counter = {"i": 0}

        def _drain(_s):
            counter["i"] += 1

        def _text():
            return f"frame-{counter['i']}"

        client.drain = _drain  # type: ignore[method-assign]
        client.page_text = _text  # type: ignore[method-assign]
        assert client.wait_for_render_settle(timeout=0.3, idle_ms=100, poll_ms=10) is False

    def test_validates_arguments(self, client):
        with pytest.raises(ValueError):
            client.wait_for_render_settle(timeout=0)
        with pytest.raises(ValueError):
            client.wait_for_render_settle(idle_ms=0)
        with pytest.raises(ValueError):
            client.wait_for_render_settle(poll_ms=0)


class TestAutoReconnect:
    """Issue #23 — DaemonClient(auto_reconnect=True) survives transient
    socket failures by reconnecting and retrying transparently."""

    def test_constructor_validates_retry_args(self):
        from carbonyl_agent.daemon import DaemonClient
        with pytest.raises(ValueError, match="max_reconnect_attempts"):
            DaemonClient("x", max_reconnect_attempts=0)
        with pytest.raises(ValueError, match="reconnect_backoff"):
            DaemonClient("x", reconnect_backoff=0)

    def test_default_no_retry_preserves_legacy_behavior(self, daemon_server):
        """Without opt-in, a transient failure surfaces immediately."""
        from carbonyl_agent.daemon import DaemonClient, DaemonConnectionError
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        c._auto_reconnect = False
        c._max_reconnect_attempts = 3
        c._reconnect_backoff = 0.5
        # _rpc with no socket → DaemonConnectionError immediately, no retry
        with pytest.raises(DaemonConnectionError, match="Not connected"):
            c._rpc({"cmd": "page_text"})

    def test_retries_after_transient_failure(self, daemon_server, monkeypatch):
        """Inject a transient broken-pipe on first call; reconnect must
        succeed on retry against the live daemon."""
        from carbonyl_agent.daemon import DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        c._auto_reconnect = True
        c._max_reconnect_attempts = 3
        c._reconnect_backoff = 0.01  # fast for tests
        c.connect()

        # Inject a one-shot BrokenPipeError on the next _rpc_once call
        original_rpc_once = c._rpc_once
        call_count = {"n": 0}

        def flaky_rpc_once(payload, timeout=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise BrokenPipeError("simulated transient failure")
            return original_rpc_once(payload, timeout)

        monkeypatch.setattr(c, "_rpc_once", flaky_rpc_once)
        # Reassign original after monkeypatch wraps fresh attr lookups
        c._rpc_once = flaky_rpc_once  # type: ignore[method-assign]

        # _rpc should retry, reconnect, and the second call should succeed
        result = c._rpc({"cmd": "page_text"})
        assert result["ok"] is True
        assert call_count["n"] >= 2
        c.disconnect()

    def test_does_not_retry_daemon_side_errors(self, daemon_server):
        """Server-side errors (daemon dispatched the command and returned
        ok=false) should NOT trigger reconnect — retrying would just
        reproduce the same error."""
        from carbonyl_agent.daemon import DaemonClient, DaemonConnectionError
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        c._auto_reconnect = True
        c._max_reconnect_attempts = 3
        c._reconnect_backoff = 0.01
        c.connect()

        # Send an unknown command — daemon returns {ok: false, error: "..."}
        # → DaemonConnectionError("Daemon error: ..."). Should NOT retry.
        with pytest.raises(DaemonConnectionError, match="Daemon error"):
            c._rpc({"cmd": "totally-not-a-real-command"})
        c.disconnect()

    def test_exhausts_attempts_then_raises(self, daemon_server, monkeypatch):
        """When the failure persists, raise after exhausting attempts."""
        from carbonyl_agent.daemon import DaemonClient, DaemonConnectionError
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        c._auto_reconnect = True
        c._max_reconnect_attempts = 2
        c._reconnect_backoff = 0.01
        c.connect()

        def always_fails(payload, timeout=None):
            raise BrokenPipeError("permanent failure")

        c._rpc_once = always_fails  # type: ignore[method-assign]

        with pytest.raises(DaemonConnectionError, match="reconnect attempts exhausted"):
            c._rpc({"cmd": "page_text"})
        c.disconnect()


class TestContextManager:
    """Issue #24: DaemonClient as context manager — connect on enter,
    disconnect (NOT close_daemon) on exit so the daemon keeps serving
    other clients."""

    def test_enter_returns_self_and_connects(self, daemon_server):
        from carbonyl_agent.daemon import DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        with c as ctx:
            assert ctx is c
            assert c._sock is not None
        assert c._sock is None  # disconnected on exit

    def test_disconnect_called_on_exception(self, daemon_server):
        from carbonyl_agent.daemon import DaemonClient
        c = DaemonClient.__new__(DaemonClient)
        c._sock_path = daemon_server["sock"]
        c._sock = None
        c._buf = ""
        c._require_backend = None
        c.backend = None
        c.protocol_version = 0
        with pytest.raises(RuntimeError, match="boom"):
            with c:
                assert c._sock is not None
                raise RuntimeError("boom")
        assert c._sock is None

    def test_does_not_close_daemon_on_exit(self, client, daemon_server):
        # Use the existing connected client, then verify the daemon is
        # still alive after a context-manager exit (i.e. exit calls
        # disconnect, not close_daemon).
        with client:
            pass
        # Daemon socket should still be servable; new connection works
        from carbonyl_agent.daemon import DaemonClient
        c2 = DaemonClient.__new__(DaemonClient)
        c2._sock_path = daemon_server["sock"]
        c2._sock = None
        c2._buf = ""
        c2._require_backend = None
        c2.backend = None
        c2.protocol_version = 0
        c2.connect()
        assert c2.ping() is True
        c2.disconnect()
