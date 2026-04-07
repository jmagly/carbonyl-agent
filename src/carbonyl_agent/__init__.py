"""
carbonyl-agent — Python automation SDK for the Carbonyl headless browser.

Usage::

    from carbonyl_agent import CarbonylBrowser, SessionManager, ScreenInspector
    from carbonyl_agent.daemon import DaemonClient, start_daemon, stop_daemon

Quick start::

    b = CarbonylBrowser()
    b.open("https://example.com")
    b.drain(5)
    print(b.page_text())
    b.close()
"""

from carbonyl_agent.browser import CarbonylBrowser  # noqa: F401
from carbonyl_agent.session import SessionManager  # noqa: F401
from carbonyl_agent.screen_inspector import ScreenInspector  # noqa: F401

__all__ = ["CarbonylBrowser", "SessionManager", "ScreenInspector"]
