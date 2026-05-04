"""Tests for the carbonyl-agent exception hierarchy (#23)."""
from __future__ import annotations

import pytest

from carbonyl_agent import (
    BackendMismatchError,
    BrowserCrashed,
    CarbonylError,
    DaemonConnectionError,
    RenderTimeoutError,
    UinputUnavailableError,
)


class TestHierarchy:
    """All custom exceptions should be catchable via CarbonylError."""

    @pytest.mark.parametrize("cls", [
        BrowserCrashed,
        DaemonConnectionError,
        RenderTimeoutError,
        BackendMismatchError,
        UinputUnavailableError,
    ])
    def test_subclass_of_carbonyl_error(self, cls):
        assert issubclass(cls, CarbonylError)

    def test_carbonyl_error_is_exception(self):
        assert issubclass(CarbonylError, Exception)


class TestBackwardsCompatibility:
    """Pre-#23 callers caught these as RuntimeError / TimeoutError —
    co-operative multiple inheritance must preserve those branches."""

    def test_daemon_connection_error_is_runtime_error(self):
        assert issubclass(DaemonConnectionError, RuntimeError)

    def test_backend_mismatch_is_runtime_error(self):
        assert issubclass(BackendMismatchError, RuntimeError)

    def test_uinput_unavailable_is_runtime_error(self):
        assert issubclass(UinputUnavailableError, RuntimeError)

    def test_render_timeout_is_timeout_error(self):
        assert issubclass(RenderTimeoutError, TimeoutError)

    def test_existing_runtime_error_catches_still_work(self):
        with pytest.raises(RuntimeError):
            raise DaemonConnectionError("simulated")


class TestBrowserCrashedAttributes:
    def test_default_optional_attrs_are_none(self):
        exc = BrowserCrashed("crashed")
        assert exc.returncode is None
        assert exc.stderr_tail is None

    def test_carries_returncode_and_stderr(self):
        exc = BrowserCrashed("crashed", returncode=139, stderr_tail="segfault")
        assert exc.returncode == 139
        assert exc.stderr_tail == "segfault"


class TestRenderTimeoutOptIn:
    """wait_for_render_settle(raise_on_timeout=True) raises rather than
    returning False (#23)."""

    def test_raises_on_timeout_when_opted_in(self):
        from carbonyl_agent.browser import CarbonylBrowser

        b = CarbonylBrowser()
        counter = {"i": 0}

        def _drain(_s):
            counter["i"] += 1

        def _text():
            return f"frame-{counter['i']}"

        b.drain = _drain  # type: ignore[method-assign]
        b.page_text = _text  # type: ignore[method-assign]

        with pytest.raises(RenderTimeoutError) as exc_info:
            b.wait_for_render_settle(timeout=0.2, idle_ms=100, poll_ms=10,
                                     raise_on_timeout=True)
        # Message includes the configured budget for triage
        assert "0.2s" in str(exc_info.value)

    def test_returns_false_by_default(self):
        from carbonyl_agent.browser import CarbonylBrowser

        b = CarbonylBrowser()
        counter = {"i": 0}

        def _drain(_s):
            counter["i"] += 1

        def _text():
            return f"frame-{counter['i']}"

        b.drain = _drain  # type: ignore[method-assign]
        b.page_text = _text  # type: ignore[method-assign]
        # No raise_on_timeout: returns False, no exception
        assert b.wait_for_render_settle(timeout=0.2, idle_ms=100, poll_ms=10) is False
