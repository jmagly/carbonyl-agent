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
    DEFAULT_SOCKET_DIR,
    BackendMismatchError,
    DaemonClient,
    daemon_status,
    is_daemon_live,
    sock_path,
    start_daemon,
    stop_daemon,
)
from carbonyl_agent.exceptions import (  # noqa: F401
    BrowserCrashed,
    CarbonylError,
    DaemonConnectionError,
    RenderTimeoutError,
)
from carbonyl_agent.persona_apply import (  # noqa: F401
    Persona,
    PersonaValidationError,
    persona_to_chromium_flags,
)
from carbonyl_agent.profile import (  # noqa: F401
    PersonaMeta,
    ProfileManager,
    list_personas,
)
from carbonyl_agent.screen_inspector import ScreenInspector  # noqa: F401
from carbonyl_agent.session import SessionManager  # noqa: F401
from carbonyl_agent.uinput_emitter import (  # noqa: F401
    UinputEmitter,
    UinputUnavailableError,
    UnsupportedKeyError,
)

__all__ = [
    "CarbonylBrowser",
    "SessionManager",
    "ScreenInspector",
    "DaemonClient",
    "start_daemon",
    "stop_daemon",
    "daemon_status",
    "is_daemon_live",
    "sock_path",
    "DEFAULT_SOCKET_DIR",
    "BackendMismatchError",
    # Exception hierarchy (#23)
    "CarbonylError",
    "BrowserCrashed",
    "DaemonConnectionError",
    "RenderTimeoutError",
    # Chromium flag groups (composable)
    "DEFAULT_HEADLESS_FLAGS",
    "BASE_CHROMIUM_FLAGS",
    "ANTI_BOT_FLAGS",
    "ANTI_FEDCM_FLAGS",
    "ANTI_ONETAP_FLAGS",
    # Trusted input backend (#36)
    "UinputEmitter",
    "UinputUnavailableError",
    "UnsupportedKeyError",
    # Per-persona profile management (#41)
    "ProfileManager",
    "PersonaMeta",
    "list_personas",
    # Persona → Chromium translator (W3C, #45)
    "Persona",
    "PersonaValidationError",
    "persona_to_chromium_flags",
]
