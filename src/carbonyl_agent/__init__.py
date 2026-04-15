from __future__ import annotations

"""
carbonyl-agent — Python automation SDK for the Carbonyl headless browser.

Usage::

    from carbonyl_agent import (
        CarbonylBrowser, SessionManager, ScreenInspector,
        DaemonClient, start_daemon, stop_daemon,
    )

Quick start::

    b = CarbonylBrowser()
    b.open("https://example.com")
    b.drain(5)
    print(b.page_text())
    b.close()
"""

from carbonyl_agent.browser import (  # noqa: F401
    ANTI_BOT_FLAGS,
    ANTI_FEDCM_FLAGS,
    ANTI_ONETAP_FLAGS,
    BASE_CHROMIUM_FLAGS,
    DEFAULT_HEADLESS_FLAGS,
    CarbonylBrowser,
)
from carbonyl_agent.daemon import (  # noqa: F401
    DaemonClient,
    daemon_status,
    is_daemon_live,
    start_daemon,
    stop_daemon,
)
from carbonyl_agent.screen_inspector import ScreenInspector  # noqa: F401
from carbonyl_agent.session import SessionManager  # noqa: F401

__all__ = [
    "CarbonylBrowser",
    "SessionManager",
    "ScreenInspector",
    "DaemonClient",
    "start_daemon",
    "stop_daemon",
    "daemon_status",
    "is_daemon_live",
    # Chromium flag groups (composable)
    "DEFAULT_HEADLESS_FLAGS",
    "BASE_CHROMIUM_FLAGS",
    "ANTI_BOT_FLAGS",
    "ANTI_FEDCM_FLAGS",
    "ANTI_ONETAP_FLAGS",
]
