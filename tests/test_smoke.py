"""
Smoke tests for carbonyl-agent.

These tests exercise the package imports and non-browser utilities without
requiring a live Carbonyl binary (unit-level) or with one (integration-level,
skipped if binary is absent).
"""

import pytest

# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------

def test_package_imports():
    from carbonyl_agent import CarbonylBrowser, ScreenInspector, SessionManager
    assert CarbonylBrowser is not None
    assert SessionManager is not None
    assert ScreenInspector is not None


def test_daemon_imports():
    from carbonyl_agent.daemon import DaemonClient
    assert DaemonClient is not None


def test_install_imports():
    from carbonyl_agent.install import _platform_triple
    triple = _platform_triple()
    assert "-" in triple  # e.g. x86_64-unknown-linux-gnu


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def test_local_binary_returns_none_or_path():
    from carbonyl_agent.browser import _local_binary
    result = _local_binary()
    if result is not None:
        assert result.is_file()
        import os
        assert os.access(result, os.X_OK)


# ---------------------------------------------------------------------------
# ScreenInspector (no browser needed)
# ---------------------------------------------------------------------------

def test_screen_inspector_empty():
    from carbonyl_agent.screen_inspector import ScreenInspector
    si = ScreenInspector([])
    assert si.row_count == 1   # max_row defaults to 1 for empty input
    assert si.col_count == 0


def test_screen_inspector_single_row():
    from carbonyl_agent.screen_inspector import ScreenInspector
    si = ScreenInspector([{"row": 1, "text": "hello world"}])
    assert si.row_count == 1
    assert si.col_count >= len("hello world")
    assert si.line(1) == "hello world"


# ---------------------------------------------------------------------------
# Integration tests (require live binary)
# ---------------------------------------------------------------------------

def _binary_available() -> bool:
    from carbonyl_agent.browser import _local_binary
    return _local_binary() is not None


@pytest.mark.skipif(not _binary_available(), reason="carbonyl binary not installed")
def test_open_and_page_text():
    from carbonyl_agent import CarbonylBrowser
    b = CarbonylBrowser()
    b.open("https://example.com")
    b.drain(8.0)
    text = b.page_text()
    b.close()
    assert "example" in text.lower(), f"Expected 'example' in page text, got: {text[:200]}"
