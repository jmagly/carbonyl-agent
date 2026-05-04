"""End-to-end tests for the daemon (#15).

Spawns a real daemon process via ``start_daemon``, connects with one or
more ``DaemonClient`` instances, exercises the public API, and stops
the daemon. All session state goes to a tmp dir so leaks don't pollute
``~/.local/share/carbonyl/sessions/``.
"""
from __future__ import annotations

import time

import pytest

from carbonyl_agent import (
    DaemonClient,
    daemon_status,
    is_daemon_live,
    start_daemon,
    stop_daemon,
)


@pytest.fixture
def daemon_session(short_session_dir):
    """Yield a (session_name, session_dir) tuple and guarantee daemon
    cleanup on test teardown — even if the test body fails.

    Each test gets a unique session name so parallel xdist workers
    don't collide on socket paths.
    """
    name = f"e2e-daemon-{int(time.time() * 1000)}"
    yield name, short_session_dir
    # Teardown: stop the daemon if it's still alive
    try:
        if is_daemon_live(name, session_dir=short_session_dir):
            stop_daemon(name, session_dir=short_session_dir)
    except Exception:
        pass


class TestDaemonLifecycle:
    def test_start_connect_drive_stop(self, stable_test_url, daemon_session):
        name, sdir = daemon_session

        pid = start_daemon(name, url=stable_test_url, session_dir=sdir, wait=10.0)
        assert pid > 0
        assert is_daemon_live(name, session_dir=sdir)

        with DaemonClient(name, session_dir=sdir) as client:
            assert client.ping() is True
            client.drain(8.0)
            text = client.page_text()
            assert "Example Domain" in text, f"body missing; got: {text[:200]!r}"

        # Client disconnect must NOT shut down the daemon
        assert is_daemon_live(name, session_dir=sdir)

        stop_daemon(name, session_dir=sdir)
        # Allow socket cleanup
        for _ in range(20):
            if not is_daemon_live(name, session_dir=sdir):
                break
            time.sleep(0.1)
        assert not is_daemon_live(name, session_dir=sdir)


class TestDaemonMultiClient:
    def test_two_clients_share_state(self, stable_test_url, daemon_session):
        """Daemon mode's whole reason for existing: amortise the browser
        across multiple clients. Verify the second client sees state set
        by the first."""
        name, sdir = daemon_session

        start_daemon(name, url=stable_test_url, session_dir=sdir, wait=10.0)

        with DaemonClient(name, session_dir=sdir) as c1:
            c1.drain(8.0)
            text1 = c1.page_text()
            assert "Example Domain" in text1

        # Independent connection — same daemon, same browser, same DOM
        with DaemonClient(name, session_dir=sdir) as c2:
            text2 = c2.page_text()
            url = c2.nav_bar_url()
        # Body still rendered between connections
        assert "Example Domain" in text2
        assert "example" in url.lower()

        stop_daemon(name, session_dir=sdir)


class TestDaemonStatus:
    def test_status_lists_running_daemon(self, stable_test_url, daemon_session):
        name, sdir = daemon_session
        start_daemon(name, url=stable_test_url, session_dir=sdir, wait=10.0)
        try:
            statuses = daemon_status(session_dir=sdir)
            running_names = [s.get("name") or s.get("session") for s in statuses]
            assert name in running_names, f"daemon {name!r} not in status: {statuses}"
        finally:
            stop_daemon(name, session_dir=sdir)


class TestDaemonRenderSettle:
    """#50 — DaemonClient.wait_for_render_settle should work end-to-end
    against a real daemon-driven browser."""

    def test_wait_for_render_settle_against_real_page(
        self, stable_test_url, daemon_session,
    ):
        name, sdir = daemon_session
        start_daemon(name, url=stable_test_url, session_dir=sdir, wait=10.0)
        try:
            with DaemonClient(name, session_dir=sdir) as client:
                settled = client.wait_for_render_settle(
                    timeout=15.0, idle_ms=500, poll_ms=100,
                )
                text = client.page_text()
            assert settled is True
            assert "Example Domain" in text
        finally:
            stop_daemon(name, session_dir=sdir)
