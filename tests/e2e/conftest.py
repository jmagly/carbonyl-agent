"""Shared fixtures and skip logic for the E2E suite (#15).

These tests require a live Carbonyl runtime binary and outbound network
access. They are skipped unless ``CARBONYL_BIN`` points to an executable
or :func:`carbonyl_agent.browser._local_binary` finds one in the default
install location.

Run only the E2E suite::

    pytest tests/e2e/ -m e2e

Skip the E2E suite (default behavior of ``pytest tests/`` is unchanged
because the ``e2e`` marker is opt-in via the ``-m`` selector — but
individual files in this directory also gate on binary availability so
collection never errors out)::

    pytest tests/                # E2E tests collected and skipped
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _binary_available() -> bool:
    if os.environ.get("CARBONYL_BIN"):
        path = Path(os.environ["CARBONYL_BIN"])
        return path.is_file() and os.access(path, os.X_OK)
    from carbonyl_agent.browser import _local_binary
    return _local_binary() is not None


# All tests in tests/e2e/ get the e2e marker automatically and skip
# collectively when no binary is available — saves repeating the
# decorator on every test function.
def pytest_collection_modifyitems(config, items):
    skip_e2e = pytest.mark.skip(
        reason="No Carbonyl binary available — set CARBONYL_BIN or run "
               "`carbonyl-agent install`",
    )
    binary = _binary_available()
    for item in items:
        if "tests/e2e" in str(item.fspath) or "tests\\e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
            if not binary:
                item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def stable_test_url() -> str:
    """Canonical small static page used by E2E tests.

    `example.com` is IETF-reserved, ~600B, never changes — the lowest-
    flake target available. If we ever need to switch (network rules,
    geo-blocking), one fixture override propagates everywhere.
    """
    return "https://example.com"


@pytest.fixture
def short_session_dir(tmp_path) -> Path:
    """Tmp dir for SessionManager / DaemonClient session state in tests
    so a flaky test never leaves behind state under the user's real
    ``~/.local/share/carbonyl/sessions/``."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d
