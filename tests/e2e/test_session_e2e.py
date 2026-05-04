"""End-to-end tests for SessionManager + session-mode browsers (#15).

Verifies session-dir lifecycle wiring against a real browser process.
Cookie-level persistence is its own beast (Chromium periodic flush
timing — see #51) and is exercised separately by the carbonyl-agent-qa
Layer 6 suite. These tests focus on what we own in this repo: profile
directory creation, lock release on close, fork/list/destroy correctness
under a real spawn.

Tests redirect the session store to ``tmp_path`` via
``CARBONYL_SESSION_DIR`` so a flaky test never leaves state under the
user's real ``~/.local/share/carbonyl/sessions/``.
"""
from __future__ import annotations

import pytest

from carbonyl_agent import CarbonylBrowser, SessionManager


@pytest.fixture
def session_env(monkeypatch, short_session_dir):
    """Pin the session store to a tmp dir for this test.

    Both SessionManager() (no args) and the CarbonylBrowser internal
    SessionManager use ``CARBONYL_SESSION_DIR`` to resolve the root.
    """
    monkeypatch.setenv("CARBONYL_SESSION_DIR", str(short_session_dir))
    return short_session_dir


class TestSessionLifecycle:
    def test_session_dir_created_on_open_and_persists_after_close(
        self, stable_test_url, session_env,
    ):
        sm = SessionManager()
        sm.create("e2e-alpha")
        assert sm.exists("e2e-alpha")

        with CarbonylBrowser(session="e2e-alpha") as b:
            b.open(stable_test_url)
            b.drain(5.0)

        # Session dir must survive close() — that's the whole point.
        assert sm.exists("e2e-alpha")
        names = [s["name"] for s in sm.list()]
        assert "e2e-alpha" in names

    def test_reopen_same_session_uses_existing_dir(
        self, stable_test_url, session_env,
    ):
        sm = SessionManager()
        sm.create("e2e-reopen")

        with CarbonylBrowser(session="e2e-reopen") as b:
            b.open(stable_test_url)
            b.drain(4.0)

        with CarbonylBrowser(session="e2e-reopen") as b:
            b.open(stable_test_url)
            b.drain(4.0)

        names = [s["name"] for s in sm.list()]
        assert names.count("e2e-reopen") == 1


class TestSessionFork:
    def test_fork_creates_independent_session(
        self, stable_test_url, session_env,
    ):
        sm = SessionManager()
        sm.create("base")

        # Seed the base profile so the fork has something non-trivial to copy.
        with CarbonylBrowser(session="base") as b:
            b.open(stable_test_url)
            b.drain(4.0)

        sm.fork("base", "worker-1")
        assert sm.exists("worker-1")

        names = [s["name"] for s in sm.list()]
        assert "base" in names
        assert "worker-1" in names

        # The forked profile should be independently spawnable.
        with CarbonylBrowser(session="worker-1") as b:
            b.open(stable_test_url)
            b.drain(4.0)


class TestSessionDestroy:
    def test_destroy_removes_dir(self, stable_test_url, session_env):
        sm = SessionManager()
        sm.create("e2e-throwaway")
        with CarbonylBrowser(session="e2e-throwaway") as b:
            b.open(stable_test_url)
            b.drain(3.0)

        sm.destroy("e2e-throwaway")
        assert not sm.exists("e2e-throwaway")
        names = [s["name"] for s in sm.list()]
        assert "e2e-throwaway" not in names
