"""Micro-benchmarks for carbonyl-agent (#22).

These benchmarks intentionally do NOT require a live Carbonyl binary —
they exercise the in-process layers (pyte text extraction, find_text on
a synthetic screen, daemon JSON RPC over a loopback socket, render-
settle polling loop) so they can run on every CI without provisioning
a runtime tarball.

Real-binary benchmarks (PTY read throughput, drain() wall-clock against
known pages) live behind ``@pytest.mark.requires_binary`` and land in
the same suite once #15 (E2E real-binary tests) provides the harness.

Run:
    pip install -e ".[dev,bench]"
    pytest tests/benchmarks/ --benchmark-only

Baselines are committed to ``.aiwg/testing/benchmarks.md``. Regressions
are advisory (>20% degradation against baseline = warning) per #22
acceptance.
"""
from __future__ import annotations

import json
import socket
import threading
from unittest.mock import MagicMock

import pyte
import pytest

pytest.importorskip("pytest_benchmark")

from carbonyl_agent.browser import _render_settle_loop, extract_text
from carbonyl_agent.daemon import DaemonClient, _BrowserServer


# ---------------------------------------------------------------------------
# extract_text — pyte screen → string
# ---------------------------------------------------------------------------

def _make_screen(cols: int, rows: int, payload: str) -> pyte.Screen:
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    stream.feed(payload)
    return screen


@pytest.mark.parametrize("rows", [50, 200])
def test_extract_text_throughput(benchmark, rows):
    """Hash extract_text() over a realistic full-screen render."""
    payload = ("The quick brown fox jumps over the lazy dog. " * 5 + "\r\n") * rows
    screen = _make_screen(220, rows, payload)
    benchmark(extract_text, screen)


# ---------------------------------------------------------------------------
# _render_settle_loop — pure polling cost
# ---------------------------------------------------------------------------

def test_render_settle_loop_immediate(benchmark):
    """Polling cost when the buffer is already settled."""
    sequence = ["stable"] * 10

    def make_callables():
        idx = {"i": 0}

        def drain(_s):
            idx["i"] = min(idx["i"] + 1, len(sequence) - 1)

        def text():
            return sequence[idx["i"]]

        return drain, text

    def run():
        drain, text = make_callables()
        return _render_settle_loop(drain, text, timeout=0.5, idle_ms=10, poll_ms=1)

    benchmark(run)


# ---------------------------------------------------------------------------
# Daemon JSON RPC round-trip — in-process loopback
# ---------------------------------------------------------------------------

class _MockBrowser:
    def page_text(self):
        return "the quick brown fox"

    def nav_bar_url(self):
        return "https://example.com"

    def drain(self, seconds):
        pass


@pytest.fixture
def loopback_client(tmp_path):
    sock = tmp_path / "bench.sock"
    server = _BrowserServer(str(sock), _MockBrowser())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for socket to come up.
    import time
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(sock))
            s.close()
            break
        except (ConnectionRefusedError, FileNotFoundError):
            time.sleep(0.01)

    c = DaemonClient.__new__(DaemonClient)
    c._sock_path = sock
    c._sock = None
    c._buf = ""
    c._require_backend = None
    c.backend = None
    c.protocol_version = 0
    c.connect()
    yield c
    c.disconnect()
    server.shutdown()


def test_daemon_page_text_rpc(benchmark, loopback_client):
    """End-to-end JSON RPC round-trip latency through the Unix socket."""
    benchmark(loopback_client.page_text)


def test_daemon_url_rpc(benchmark, loopback_client):
    """Smaller payload — surfaces socket overhead vs. payload size."""
    benchmark(loopback_client.nav_bar_url)
