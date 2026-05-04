"""End-to-end tests for ``CarbonylBrowser`` against a live runtime (#15).

Exercises the browser's public surface against a real network page so
PTY → pyte → screen extraction is validated end to end. Scoped to a
single canonical target (`example.com`) to keep flake low.
"""
from __future__ import annotations

import pytest

from carbonyl_agent import CarbonylBrowser


def _drain_until_loaded(b: CarbonylBrowser, total: float = 10.0) -> str:
    """Helper: drain the PTY long enough for example.com's full text to
    arrive, with render-settle as a secondary signal.

    Real-binary E2E is timing-sensitive — Carbonyl renders at ~5fps and
    a stable hash can land before the body has fully painted. We drain
    a generous fixed window and use render-settle only as a guard.
    """
    b.drain(total)
    b.wait_for_render_settle(timeout=2.0, idle_ms=500, poll_ms=100)
    return b.page_text()


class TestBrowserOpen:
    def test_open_renders_example_domain(self, stable_test_url):
        with CarbonylBrowser() as b:
            b.open(stable_test_url)
            text = _drain_until_loaded(b)
        # Match on the body text, not the URL bar — the URL bar always
        # contains the URL, so checking "example" in text would pass
        # even if the body never rendered.
        assert "Example Domain" in text, f"body text missing; got: {text[:300]!r}"

    def test_navigate_after_open(self, stable_test_url):
        with CarbonylBrowser() as b:
            b.open("https://example.org")
            b.drain(6.0)
            b.navigate(stable_test_url)
            text = _drain_until_loaded(b, total=8.0)
        assert "Example Domain" in text


class TestRenderSettle:
    """#48 / #50 — wait_for_render_settle is the deterministic readiness
    primitive; verify it actually settles against a real page.

    Note: the settle predicate is "buffer hash unchanged for idle_ms" —
    an empty / still-loading buffer is technically stable, so we do an
    initial drain to ensure body bytes have started arriving before
    treating settle=True as proof of full paint. This pattern matches
    real-world usage in `tests/e2e/test_browser_e2e.py::_drain_until_loaded`.
    Unit tests cover settle/timeout edge cases against mocks; this E2E
    case verifies it works against a live PTY at all.
    """

    def test_settles_after_initial_drain(self, stable_test_url):
        with CarbonylBrowser() as b:
            b.open(stable_test_url)
            # Give the body bytes time to start arriving so settle
            # doesn't latch on to the initial empty-screen hash.
            b.drain(4.0)
            settled = b.wait_for_render_settle(timeout=10.0, idle_ms=500, poll_ms=100)
            text = b.page_text()
        assert settled is True, f"render did not settle within 10s; text: {text[:200]!r}"
        assert "Example Domain" in text


class TestScreenInspector:
    def test_find_text_locates_body_string(self, stable_test_url):
        with CarbonylBrowser() as b:
            b.open(stable_test_url)
            _drain_until_loaded(b)
            matches = b.find_text("Example Domain")
        assert len(matches) >= 1
        first = matches[0]
        # Each match has 1-indexed col / row / end_col coordinates.
        assert first["row"] >= 1
        assert first["col"] >= 1
        assert first["end_col"] > first["col"]

    def test_inspector_grid_dimensions(self, stable_test_url):
        with CarbonylBrowser() as b:
            b.open(stable_test_url)
            _drain_until_loaded(b)
            si = b.inspector()
        assert si.row_count >= 1
        assert si.col_count >= 1


class TestContextManager:
    """#24 — verify resources are released even when the body raises
    against a real PTY-backed browser, not just a mocked one."""

    def test_close_runs_on_exception(self, stable_test_url):
        b = CarbonylBrowser()
        with pytest.raises(RuntimeError, match="boom"):
            with b:
                b.open(stable_test_url)
                b.drain(2.0)
                raise RuntimeError("boom")
        # Child should be reaped by close()
        assert b._child is None or not b._child.isalive()
