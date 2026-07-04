#!/usr/bin/env python3
"""
Carbonyl browser automation layer.

Spawns Carbonyl in a PTY (local binary or Docker fallback), sends
keystrokes, and returns the rendered screen as plain text via pyte.

Binary search order:
    1. CARBONYL_BIN env var (explicit path)
    2. ~/.local/share/carbonyl/bin/<triple>/carbonyl  (installed via `carbonyl-agent install`)
    3. `carbonyl` on $PATH
    4. Docker fallback: docker run ghcr.io/jmagly/carbonyl

Usage:
    python -m carbonyl_agent.browser search "search term"
    python -m carbonyl_agent.browser open https://example.com --wait 10
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pexpect
import pyte

from carbonyl_agent._logging import get_logger

if TYPE_CHECKING:
    # `Persona as _PersonaType` mirrors the local import inside
    # `__init__` (kept lazy at runtime to dodge the carbonyl-fingerprint
    # Rust dep when the typed-Persona path isn't exercised). pdoc and
    # other static-introspection tools need the name at module scope
    # to resolve the `_PersonaType | None` attribute annotation.
    #   Refs: roctinam/carbonyl-agent#96
    from carbonyl_agent.persona_apply import Persona  # noqa: F401
    from carbonyl_agent.persona_apply import Persona as _PersonaType  # noqa: F401

_log = get_logger(__name__)

# Terminal dimensions Carbonyl will render to
COLS = 220
ROWS = 50

# Default install location used by `carbonyl-agent install`
_DEFAULT_INSTALL_DIR = Path.home() / ".local" / "share" / "carbonyl" / "bin"

# Docker fallback: require explicit opt-in and use a pinned digest.
# Image is the maintained runtime container published by the jmagly/carbonyl
# fork (ghcr.io/jmagly/carbonyl, since carbonyl v0.2.0-alpha.10); the original
# fathyb/carbonyl image has been inactive since early 2023.
# To update the digest, pull the latest image and run:
#   docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/jmagly/carbonyl
_DOCKER_IMAGE_DIGEST = "ghcr.io/jmagly/carbonyl@sha256:26d990c3e36bb685a2deb3f8a998a441ad606aa4b2469e30e0bfdfffb4712532"
_DOCKER_FALLBACK_ENV = "CARBONYL_ALLOW_DOCKER"

# ---------------------------------------------------------------------------
# Chromium flag groups
# ---------------------------------------------------------------------------
# These are published as public module constants so agents can compose the
# exact set of flags they need:
#
#   from carbonyl_agent.browser import (
#       DEFAULT_HEADLESS_FLAGS, ANTI_BOT_FLAGS, ANTI_FEDCM_FLAGS,
#       ANTI_ONETAP_FLAGS,
#   )
#
#   # Default baseline (applied automatically):
#   b = CarbonylBrowser()
#
#   # Add FedCM/One Tap suppression for sites that aggressively overlay
#   # Google Sign-In (e.g. X, LinkedIn, many publishers):
#   b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS)
#
#   # Compose multiple groups:
#   b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS + MY_CUSTOM_FLAGS)
#
# The `extra_flags` list is appended to DEFAULT_HEADLESS_FLAGS in the order
# given. To completely replace the default set, pass `base_flags=[...]`.

# Baseline: suppress first-run noise, sync, keychain, and file-picker prompts.
# Applied to every CarbonylBrowser by default.
BASE_CHROMIUM_FLAGS: list[str] = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--password-store=basic",
    "--use-mock-keychain",
]

# Anti-bot-detection: spoof UA, suppress webdriver markers, disable HTTP/2
# (whose SETTINGS frame is a server-side fingerprint for Akamai et al).
ANTI_BOT_FLAGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "--disable-http2",
]

# Disable Federated Credential Management (FedCM) and Google One Tap.
# Use for sites that aggressively overlay Google Sign-In on top of their
# own login form (X/Twitter, LinkedIn, many publishers). Without this,
# overlays steal focus and scripted typing lands in the wrong input.
#
# Belt-and-suspenders: the feature flags handle modern Chromium, and the
# host-resolver rule blackholes Google's One Tap accounts endpoint for
# Chromium builds (like Carbonyl's) that don't honor the feature flag.
ANTI_FEDCM_FLAGS: list[str] = [
    "--disable-features=FedCm,FedCmAuthz,FedCmButtonMode,FedCmIdPRegistration,FederatedCredentialManagement,DigitalIdentityCredentials",
    "--host-resolver-rules=MAP accounts.google.com ~NOTFOUND",
]

# Alias: "One Tap" is the common name for the overlay this blocks.
ANTI_ONETAP_FLAGS: list[str] = ANTI_FEDCM_FLAGS

# Default flags applied when no overrides are given.
DEFAULT_HEADLESS_FLAGS: list[str] = BASE_CHROMIUM_FLAGS + ANTI_BOT_FLAGS

# Backwards-compat alias (internal).
_HEADLESS_FLAGS = DEFAULT_HEADLESS_FLAGS

def _session_manager() -> Any:
    """Import and return a SessionManager."""
    from carbonyl_agent.session import SessionManager
    return SessionManager()


def _platform_triple() -> str:
    """Return the current platform triple (e.g. x86_64-unknown-linux-gnu)."""
    machine = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip().lower()
    if system == "darwin":
        vendor, os_name = "apple", "darwin"
    else:
        vendor, os_name = "unknown", "linux-gnu"
    return f"{machine}-{vendor}-{os_name}"


def _local_binary() -> Path | None:
    """Return path to a usable carbonyl binary.

    Search order:
    1. CARBONYL_BIN env var
    2. ~/.local/share/carbonyl/bin/<triple>/carbonyl  (installed by carbonyl-agent install)
    3. `carbonyl` on $PATH
    """
    # 1. Explicit override
    env_bin = os.environ.get("CARBONYL_BIN")
    if env_bin:
        p = Path(env_bin)
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 2. Standard install location
    triple = _platform_triple()
    installed = _DEFAULT_INSTALL_DIR / triple / "carbonyl"
    if installed.is_file() and os.access(installed, os.X_OK):
        return installed

    # 3. PATH
    which = subprocess.run(["which", "carbonyl"], capture_output=True, text=True)
    if which.returncode == 0:
        p = Path(which.stdout.strip())
        if p.is_file() and os.access(p, os.X_OK):
            return p

    return None

# Unicode ranges that are graphical block/box characters Carbonyl uses for
# pixel-level rendering. These are not page text — strip them for agents.
_BLOCK_CHARS = re.compile(
    r"[\u2500-\u257F"   # Box Drawing
    r"\u2580-\u259F"   # Block Elements (▀▄█▌▐░▒▓ etc.)
    r"\u25A0-\u25FF"   # Geometric Shapes
    r"\uFFFD]"         # Replacement char
)


def _is_text_char(ch: str) -> bool:
    """Return True for printable non-block characters."""
    if _BLOCK_CHARS.match(ch):
        return False
    cat = unicodedata.category(ch)
    # Keep letters, numbers, punctuation, symbols, spaces
    return cat[0] in ("L", "N", "P", "S", "Z") or ch == " "


def extract_text(screen: Any) -> str:
    """
    Pull readable text out of a pyte screen, filtering out the block/quad
    characters Carbonyl uses for graphical rendering.
    Returns lines with leading/trailing whitespace stripped, blank lines
    collapsed, result trimmed.
    """
    lines = []
    for row_idx in sorted(screen.buffer.keys()):
        row = screen.buffer[row_idx]
        raw = "".join(char.data for char in row.values())
        # Keep only text characters
        text = "".join(ch if _is_text_char(ch) else " " for ch in raw)
        # Collapse runs of spaces
        text = re.sub(r" {2,}", "  ", text).strip()
        if text:
            lines.append(text)
    # Deduplicate consecutive identical lines (artifact of rendering)
    deduped: list[str] = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)
    return "\n".join(deduped)


def _render_settle_loop(
    drain_fn: Any,
    page_text_fn: Any,
    timeout: float,
    idle_ms: int,
    poll_ms: int,
) -> bool:
    """Shared polling loop for ``wait_for_render_settle``.

    Both :meth:`CarbonylBrowser.wait_for_render_settle` and
    :meth:`carbonyl_agent.daemon.DaemonClient.wait_for_render_settle`
    delegate here so the settle semantics live in one place (#50).

    ``drain_fn(seconds)`` pumps newly-arriving bytes into the screen
    buffer for one poll interval. ``page_text_fn()`` returns the
    current rendered text whose hash is the stability signal.
    """
    if poll_ms <= 0 or idle_ms <= 0 or timeout <= 0:
        raise ValueError("poll_ms, idle_ms, and timeout must all be > 0")
    deadline = time.time() + timeout
    last_hash: int | None = None
    stable_since: float | None = None
    idle_s = idle_ms / 1000.0
    poll_s = poll_ms / 1000.0
    while time.time() < deadline:
        drain_fn(poll_s)
        current = hash(page_text_fn())
        now = time.time()
        if current == last_hash:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= idle_s:
                return True
        else:
            last_hash = current
            stable_since = now
    return False


class CarbonylBrowser:
    def __init__(
        self,
        cols: int = COLS,
        rows: int = ROWS,
        session: str | None = None,
        *,
        viewport: tuple[int, int] | None = None,
        extra_flags: list[str] | None = None,
        base_flags: list[str] | None = None,
        input_backend: str = "pty",
        persona: "str | Persona | None" = None,
        profiles_dir: str | None = None,
    ):
        """
        Args:
            cols, rows: Terminal dimensions Carbonyl renders to.
            session: Named session to use for persistent state. If given,
                     the session's profile directory is passed as
                     ``--user-data-dir`` to Chromium, preserving cookies,
                     localStorage, and IndexedDB across browser restarts.

                     If a daemon is already running for this session (started
                     via ``automation/daemon.py start <session>``), ``open()``
                     will reconnect to the live process over a Unix socket
                     instead of spawning a new browser. Call ``disconnect()``
                     to release the socket while leaving the browser running.

                     Create/manage sessions with ``automation/session.py``
                     or ``SessionManager``.
            viewport: Consumer-controlled CSS viewport as ``(width, height)``
                     in CSS pixels. When set, Blink lays out the page against
                     this exact viewport and rasters to matching physical
                     pixels (DSF = 1.0), so the terminal samples an
                     upper-left window of a predictable layout rather than
                     the upper-left of an inflated ``cells × scale`` layout.

                     Requires Carbonyl build with ``--viewport`` support
                     (runtime tag >= ``runtime-2026-04-17-viewport``). Falls
                     through cleanly on older runtimes (env var is ignored).

                     Pass ``viewport=(1280, 800)`` for a typical desktop
                     layout. Terminals smaller than the viewport sample the
                     upper-left portion; larger terminals see the full page.
            extra_flags: Additional Chromium command-line flags to append.
                     Compose from published flag groups for common scenarios::

                         from carbonyl_agent.browser import (
                             ANTI_FEDCM_FLAGS,   # disable Google One Tap
                         )

                         b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS)

            base_flags: Completely replace the default flag set
                     (``DEFAULT_HEADLESS_FLAGS``). Rarely needed — prefer
                     ``extra_flags`` for additive changes.
            input_backend: How keystrokes and mouse events reach Chromium.

                     - ``"pty"`` (default) — events are written to the PTY
                       and synthesised by Carbonyl in-process. Cheap, works
                       anywhere a terminal works, but the events arrive at
                       JavaScript with ``event.isTrusted = false`` so
                       React-controlled inputs and bot detection libraries
                       reject them silently.
                     - ``"uinput"`` — events are emitted via
                       ``/dev/uinput`` to a virtual HID device the kernel
                       routes through Xorg into the browser. Events arrive
                       with ``event.isTrusted = true``, indistinguishable
                       from a physical keyboard/mouse. Required for
                       scripted login on modern SPAs (X, LinkedIn, etc.).
                       Requires the carbonyl-agent-qa-runner container or
                       an equivalent Xorg + uinput environment; see
                       ``roctinam/carbonyl/docs/runtime-modes.md`` Mode 2
                       and ADR-002 rev 2 for the rationale.

                     The viewport coordinates passed to ``click()`` /
                     ``mouse_move()`` are scaled to the CSS viewport
                     declared via the ``viewport`` argument when uinput
                     is in use, so call sites are interchangeable
                     between backends.
        """
        if input_backend not in ("pty", "uinput"):
            raise ValueError(
                f"input_backend must be 'pty' or 'uinput', got {input_backend!r}"
            )
        if persona is not None and session is not None:
            raise ValueError(
                "persona= and session= are mutually exclusive; persona is the "
                "persona-keyed profile API, session is the legacy SessionManager API."
            )

        # Persona may be either a profile-name string (legacy / W1.4) or a
        # typed `carbonyl_agent.persona_apply.Persona` (W3C). The string
        # form drives ProfileManager only; the typed form additionally
        # injects Chromium flags via persona_to_chromium_flags() and
        # auto-derives the profile name from `persona.id`.
        from carbonyl_agent.persona_apply import (
            Persona as _PersonaType,
        )
        from carbonyl_agent.persona_apply import (
            persona_to_chromium_flags as _persona_flags,
        )

        self._persona_obj: _PersonaType | None = None
        if isinstance(persona, _PersonaType):
            self._persona_obj = persona
            persona_name: str | None = persona.id
        else:
            persona_name = persona

        self.cols = cols
        self.rows = rows
        self._session = session
        self._persona = persona_name
        self._profiles_dir = profiles_dir
        self._profile_manager: Any | None = None
        # Default viewport to the persona's screen dimensions when not
        # explicitly set. Persona-driven layout otherwise defeats half
        # the point of declaring screen_width/height in the schema.
        if viewport is None and self._persona_obj is not None:
            viewport = self._persona_obj.viewport
        self._viewport = viewport
        self.input_backend = input_backend
        self._screen: Any = pyte.Screen(cols, rows)
        self._stream: Any = pyte.ByteStream(self._screen)
        self._child: Any | None = None
        self._daemon_client: Any | None = None
        self._uinput_emitter: Any | None = None

        # Flag composition order:
        #   1. base_flags (or DEFAULT_HEADLESS_FLAGS)
        #   2. persona-derived flags — UA, --lang, --accept-lang, DPR
        #      (override the static UA in DEFAULT_HEADLESS_FLAGS)
        #   3. extra_flags — caller's last word
        self._flags: list[str] = list(
            base_flags if base_flags is not None else DEFAULT_HEADLESS_FLAGS
        )
        if self._persona_obj is not None:
            self._flags = self._flags + _persona_flags(self._persona_obj)
        if extra_flags:
            self._flags = self._flags + list(extra_flags)

    def open(self, url: str) -> None:
        # If a daemon is already running for this session, reconnect to it
        # instead of spawning a new browser process.
        if self._session:
            from carbonyl_agent.daemon import DaemonClient, is_daemon_live
            if is_daemon_live(self._session):
                log(f"reconnecting to live daemon for session {self._session!r}")
                client = DaemonClient(self._session)
                client.connect()
                self._daemon_client = client
                # Navigate to the requested URL in the running browser.
                # Skip navigate for sentinel values used purely to reconnect.
                if url and url not in ("about:blank", ""):
                    client.navigate(url)
                return

        binary = _local_binary()
        args = ["--fps=5", "--no-sandbox"] + self._flags

        if self._session:
            sm = _session_manager()
            if not sm.exists(self._session):
                sm.create(self._session)
            profile = sm.profile_dir(self._session)
            args.append(f"--user-data-dir={profile}")
            log(f"session: {self._session!r}  profile: {profile}")

        if self._persona:
            from carbonyl_agent.profile import ProfileManager

            pm = ProfileManager(self._persona, profiles_dir=self._profiles_dir)
            pm.acquire_lock()
            self._profile_manager = pm
            pm.record_input_backend(self.input_backend)
            args.append(f"--user-data-dir={pm.profile_dir}")
            log(f"persona: {self._persona!r}  profile: {pm.profile_dir}")

        # In persona/session mode, shorten the cookie SQLite commit interval
        # so cookies persist within the 5 s graceful_timeout instead of the
        # upstream 30 s default. Requires the Carbonyl runtime patch
        # `0026-Add-opt-in-eager-cookie-SQLite-flush-via-CLI.patch` (carbonyl#69);
        # ignored as an unknown switch by older runtimes.
        if self._session or self._persona:
            args.append("--carbonyl-cookie-flush-interval-ms=1000")

        args.append(url)

        if binary:
            lib_dir = str(binary.parent)
            env = {**os.environ, "LD_LIBRARY_PATH": lib_dir}
            if self._viewport is not None:
                vw, vh = self._viewport
                env["CARBONYL_VIEWPORT"] = f"{vw}x{vh}"
                log(f"viewport override: {vw}x{vh}")
            log(f"using local binary: {binary}")
            self._child = pexpect.spawn(
                str(binary), args,
                dimensions=(self.rows, self.cols),
                timeout=90,
                encoding=None,
                env=env,
                cwd=str(binary.parent),
            )
        else:
            if os.environ.get(_DOCKER_FALLBACK_ENV) != "1":
                raise RuntimeError(
                    "No local Carbonyl binary found. Install one with `carbonyl-agent install`, "
                    "or set CARBONYL_ALLOW_DOCKER=1 to allow Docker fallback."
                )
            log("local binary not found, falling back to Docker image")
            # Docker: mount session profile if provided; drop SDK-supplied flags
            # (they're already baked into the image entrypoint).
            flag_str = " ".join(
                a for a in args
                if not a.startswith("--user-data-dir")
                and a not in self._flags
            )
            vol = ""
            if self._session:
                sm = _session_manager()
                profile = sm.profile_dir(self._session)
                vol = f"-v {profile}:/data/profile "
                flag_str += " --user-data-dir=/data/profile"
            cmd = f"docker run --rm -it {vol}{_DOCKER_IMAGE_DIGEST} {flag_str}"
            self._child = pexpect.spawn(
                "bash", ["-c", cmd],
                dimensions=(self.rows, self.cols),
                timeout=90,
                encoding=None,
            )

    def drain(self, seconds: float) -> None:
        """Read output for `seconds`, feeding bytes into the screen buffer."""
        if self._daemon_client:
            self._daemon_client.drain(seconds)
            return
        assert self._child is not None
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                chunk = self._child.read_nonblocking(size=8192, timeout=0.1)
                self._stream.feed(chunk)
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break

    def wait_for_render_settle(
        self,
        timeout: float = 5.0,
        idle_ms: int = 200,
        poll_ms: int = 50,
        *,
        raise_on_timeout: bool = False,
    ) -> bool:
        """Wait until the rendered terminal buffer has been stable for
        ``idle_ms`` continuous milliseconds, or until ``timeout`` elapses.

        Returns ``True`` if the buffer settled, ``False`` if ``timeout``
        was hit first. Designed for visual-capture tests (#48) where a
        wall-clock ``drain()`` is too racy: callers can do
        ``b.open(url); b.wait_for_render_settle(); scrot(...)`` and
        deterministically capture a stable frame.

        The "settled" signal is the hash of :meth:`page_text` not changing
        across successive ``poll_ms`` polls. Each poll also pumps the PTY
        so newly-arriving bytes get folded into the screen — there is no
        need to call :meth:`drain` first.

        Daemon-connected mode polls via the daemon's ``page_text`` RPC.

        Tuning:

        - ``timeout`` — overall budget. Default 5s is generous for cold
          page loads; CI may pass 10s for known-slow fixtures.
        - ``idle_ms`` — how long the buffer must be unchanged to count
          as settled. Default 200ms catches Carbonyl's 5fps render
          cadence (one frame every ~200ms). Lower values risk
          declaring the page settled mid-paint.
        - ``poll_ms`` — sampling interval. Smaller = more responsive
          but more CPU. Default 50ms gives 4 samples per ``idle_ms``
          window at the default settings.
        - ``raise_on_timeout`` — when ``True``, raise
          :class:`carbonyl_agent.exceptions.RenderTimeoutError` on
          timeout instead of returning ``False`` (#23). Useful when the
          caller wants exception-based control flow rather than checking
          a boolean.
        """
        ok = _render_settle_loop(
            drain_fn=self.drain,
            page_text_fn=self.page_text,
            timeout=timeout,
            idle_ms=idle_ms,
            poll_ms=poll_ms,
        )
        if not ok and raise_on_timeout:
            from carbonyl_agent.exceptions import RenderTimeoutError
            raise RenderTimeoutError(
                f"page did not settle within {timeout}s "
                f"(idle_ms={idle_ms}, poll_ms={poll_ms})"
            )
        return ok

    def _ensure_uinput(self) -> Any:
        """Lazy-create the UinputEmitter. Called only when input_backend == 'uinput'."""
        if self._uinput_emitter is None:
            from carbonyl_agent.uinput_emitter import UinputEmitter
            # Use the consumer-supplied viewport for absolute mouse scaling
            # so click(col, row) → (col * 2, row * 4) physical px lands at the
            # intended CSS coordinate. Cell-to-pixel ratio matches Carbonyl's
            # rendering convention (2x4 px per cell).
            vp_x = self._viewport[0] if self._viewport else max(self.cols * 2, 1)
            vp_y = self._viewport[1] if self._viewport else max(self.rows * 4, 1)
            self._uinput_emitter = UinputEmitter(viewport=(vp_x, vp_y))
            self._uinput_emitter.open()
        return self._uinput_emitter

    def _cell_to_viewport_px(self, col: int, row: int) -> tuple[int, int]:
        """Convert 1-indexed terminal (col, row) to viewport-absolute pixel
        coordinates suitable for uinput's ABS_X/ABS_Y. Honours the
        consumer-supplied viewport so the mouse lands at the same CSS
        location regardless of input backend."""
        # Each terminal cell maps to 2x4 CSS pixels in Carbonyl's quadrant
        # rendering convention (see roctinam/carbonyl#37).
        return (col * 2, row * 4)

    def send(self, text: str) -> None:
        """Type text into the browser.

        Routing:
        - daemon-connected: forwards to daemon
        - input_backend="uinput": emits via /dev/uinput (isTrusted=true,
          kernel-level realism — preferred for bot-detection-grade tests)
        - input_backend="pty": writes UTF-8 bytes to the PTY. Carbonyl's
          parser turns these into Event::KeyPress, which the libcarbonyl
          bridge forwards via RenderWidgetHost::ForwardKeyboardEvent —
          the same API DevTools Input.dispatchKeyEvent uses, producing
          isTrusted=true in the DOM. Browser-injected pattern signature
          may be detectable by sophisticated bot defenses; uinput beats
          PTY for hostile-traffic adversaries but PTY is trusted enough
          for everything that doesn't fingerprint kernel-level input
          timing.
        """
        if self._daemon_client:
            self._daemon_client.send(text)
            return
        if self.input_backend == "uinput":
            self._ensure_uinput().type_text(text)
            return
        assert self._child is not None
        self._child.send(text.encode("utf-8"))

    def mouse_move(self, col: int, row: int) -> None:
        """
        Send a mouse-move event at terminal cell (col, row).

        Routing:
        - daemon-connected: forwards to daemon
        - input_backend="uinput": EV_ABS via /dev/uinput (isTrusted=true,
          kernel-level realism)
        - input_backend="pty": SGR mouse code 32 escape. Carbonyl parses
          the SGR into Event::MouseMove and the bridge forwards it via
          RenderWidgetHost::ForwardMouseEvent → isTrusted=true mousemove
          in the DOM. Essential for sites that require movement entropy
          before accepting interaction (e.g. Akamai Bot Manager sensor)
          and trusted enough for any consumer that doesn't fingerprint
          kernel-level pointer timing.
        """
        if self._daemon_client:
            self._daemon_client.mouse_move(col, row)
            return
        if self.input_backend == "uinput":
            x, y = self._cell_to_viewport_px(col, row)
            self._ensure_uinput().move_mouse(x, y)
            return
        assert self._child is not None
        self._child.send(f"\x1b[<32;{col};{row}M".encode())

    def mouse_path(
        self,
        points: list[tuple[int, int]],
        delay: float = 0.05,
    ) -> None:
        """
        Move the mouse through a list of (col, row) waypoints with a short
        delay between each, producing organic-looking movement telemetry.

        Example::

            browser.mouse_path([(10,10),(50,20),(80,15),(120,30)], delay=0.06)
        """
        for col, row in points:
            self.mouse_move(col, row)
            time.sleep(delay)

    def click(self, col: int, row: int) -> None:
        """Send a left-click at terminal cell (col, row).

        Routing:
        - daemon-connected: forwards to daemon
        - input_backend="uinput": EV_KEY BTN_LEFT via /dev/uinput
          (isTrusted=true, kernel-level realism). React-controlled
          buttons fire onClick.
        - input_backend="pty": SGR mouse protocol press+release. Carbonyl
          parses these into Event::MouseDown/MouseUp, the bridge forwards
          them via RenderWidgetHost::ForwardMouseEvent → isTrusted=true
          mousedown/mouseup/click in the DOM. Use uinput when the
          consumer adversary fingerprints input-timing patterns, PTY for
          everything else.
        """
        if self._daemon_client:
            self._daemon_client.click(col, row)
            return
        if self.input_backend == "uinput":
            x, y = self._cell_to_viewport_px(col, row)
            self._ensure_uinput().click(x, y)
            return
        assert self._child is not None
        press   = f"\x1b[<0;{col};{row}M".encode()
        release = f"\x1b[<0;{col};{row}m".encode()
        self._child.send(press)
        self._child.send(release)

    def click_on(self, text: str, offset_col: int = 0, occurrence: int = 0) -> tuple[int, int] | None:
        """
        Find ``text`` on screen and click its center (or offset from center).

        Works in both direct and daemon-connected modes.

        Args:
            text:        Text to search for.
            offset_col:  Column offset relative to the center of the found text
                         (positive = right, negative = left).
            occurrence:  Which occurrence to click (0 = first, 1 = second, …).

        Returns:
            ``(col, row)`` of the click point (1-indexed), or ``None`` if not found.
        """
        matches = self.find_text(text)
        if not matches or occurrence >= len(matches):
            return None
        m = matches[occurrence]
        # Click center of matched text span
        center = m["col"] + (len(text) - 1) // 2 + offset_col
        self.click(center, m["row"])
        return (center, m["row"])

    # click_text is the preferred name; click_on is kept for compatibility.
    click_text = click_on

    def find_at_row(self, text: str, row: int) -> dict[str, int] | None:
        """
        Find ``text`` on a specific row (1-indexed).

        Returns the first ``{"col", "row", "end_col"}`` match on that row,
        or ``None`` if not found. Useful when the same text appears multiple
        times but you know which row the target element is on.
        """
        for m in self.find_text(text):
            if m["row"] == row:
                return m
        return None

    def click_at_row(self, text: str, row: int, offset_col: int = 0) -> tuple[int, int] | None:
        """
        Find ``text`` on a specific row and click its center.

        Returns ``(col, row)`` of the click point, or ``None`` if not found.
        """
        m = self.find_at_row(text, row)
        if m is None:
            return None
        center = m["col"] + (len(text) - 1) // 2 + offset_col
        self.click(center, m["row"])
        return (center, m["row"])

    def send_key(self, key: str) -> None:
        """Send a named key.

        Routing:
        - daemon-connected: forwards to daemon
        - input_backend="uinput": EV_KEY via /dev/uinput (isTrusted=true,
          kernel-level realism)
        - input_backend="pty": ANSI escape sequence over the PTY. Carbonyl
          parses these into Event::KeyPress and forwards via
          RenderWidgetHost::ForwardKeyboardEvent → isTrusted=true
          keydown/keyup in the DOM.
        """
        if self._daemon_client:
            self._daemon_client.send_key(key)
            return
        if self.input_backend == "uinput":
            self._ensure_uinput().press_key(key)
            return
        keys = {
            "enter":     b"\r",
            "tab":       b"\t",
            "backspace": b"\x7f",
            "up":        b"\x1b[A",
            "down":      b"\x1b[B",
            "left":      b"\x1b[D",
            "right":     b"\x1b[C",
            "escape":    b"\x1b",
        }
        seq = keys.get(key.lower())
        if seq is None:
            raise ValueError(f"Unknown key: {key!r}. Valid: {list(keys)}")
        assert self._child is not None
        self._child.send(seq)

    def navigate(self, url: str) -> None:
        """
        Navigate to `url` by editing the Carbonyl address bar directly.

        Carbonyl nav bar layout (row 0 in Carbonyl = terminal row 1):
          col 0-2   [❮] back   → mouse_down x in 0..=2
          col 3-5   [❯] forward → mouse_down x in 3..=5
          col 6-8   [↻] refresh → mouse_down x in 6..=8
          col 9     [
          col 10    space
          col 11+   URL field  → cursor = x - 11

        Clicking at terminal (col, row=1) with col >= 12 focuses the URL bar.
        Arrow keys (via ANSI sequences) move the cursor within the URL.
        """
        if self._daemon_client:
            self._daemon_client.navigate(url)
            return
        assert self._child is not None
        # 1. Click at col=12 row=1 → Carbonyl x=11 → cursor pos 0 in URL field
        self.click(12, 1)
        # 2. Jump cursor to end of current URL (Down arrow = \x1b[B = 0x12 internally)
        self._child.send(b"\x1b[B")
        # 3. Backspace entire URL (200 chars is more than any URL we'd see)
        self._child.send(b"\x7f" * 250)
        # 4. Type new URL
        self._child.send(url.encode("ascii"))
        # 5. Press Enter to navigate
        self._child.send(b"\r")

    def nav_bar_url(self) -> str:
        """Extract the URL shown in Carbonyl's navigation bar, if visible."""
        if self._daemon_client:
            result: str = self._daemon_client.nav_bar_url()
            return result
        text = self.page_text()
        m = re.search(r"https?://[^\s\]]+", text)
        return m.group(0) if m else ""

    def page_text(self) -> str:
        """Return current screen as clean readable text."""
        if self._daemon_client:
            result: str = self._daemon_client.page_text()
            return result
        return extract_text(self._screen)

    def find_text(self, text: str) -> list[dict[str, int]]:
        """
        Find all occurrences of ``text`` in the raw terminal buffer.

        Returns a list of dicts (all values 1-indexed, matching terminal/SGR
        convention so coordinates can be passed directly to ``click()``):

        .. code-block:: python

            [{"col": int, "row": int, "end_col": int}, ...]

        ``col`` is the column of the first character of the match.
        ``end_col`` is the column of the last character (inclusive).

        Works in both direct and daemon-connected modes.
        """
        if self._daemon_client:
            result: list[dict[str, int]] = self._daemon_client.find_text(text)
            return result
        results: list[dict[str, int]] = []
        for row_idx in sorted(self._screen.buffer.keys()):
            row = self._screen.buffer[row_idx]
            line = "".join(c.data for c in row.values())
            start = 0
            while True:
                idx = line.find(text, start)
                if idx == -1:
                    break
                results.append({
                    "col": idx + 1,               # 1-indexed start col
                    "row": row_idx + 1,            # 1-indexed row
                    "end_col": idx + len(text),    # 1-indexed end col (inclusive)
                })
                start = idx + 1
        return results

    def raw_lines(self) -> list[dict[str, Any]]:
        """
        Return the raw screen buffer as ``[{"row": int, "text": str}, ...]``.
        Works in both direct and daemon-connected modes.
        """
        if self._daemon_client:
            result: list[dict[str, Any]] = self._daemon_client.raw_lines()
            return result
        lines: list[dict[str, Any]] = []
        for row_idx in sorted(self._screen.buffer.keys()):
            row = self._screen.buffer[row_idx]
            lines.append({"row": row_idx + 1,
                          "text": "".join(c.data for c in row.values())})
        return lines

    def inspector(self) -> Any:
        """
        Return a ``ScreenInspector`` for the current screen state.

        Convenience wrapper around ``raw_lines()`` — imports
        ``automation.screen_inspector`` lazily so browser.py has no hard dep.

        Example::

            si = browser.inspector()
            si.print_grid(marks=[(46, 45)])
            print(si.annotate(marks=[(46, 45)]))
        """
        from carbonyl_agent.screen_inspector import ScreenInspector
        return ScreenInspector(self.raw_lines())

    def reconnect(self) -> bool:
        """
        Connect to a live daemon for this session without navigating.
        Returns True if a daemon was found and connected, False otherwise.
        Use this instead of ``open()`` when you want to observe the current
        browser state without changing the URL.
        """
        if not self._session:
            return False
        self.open("about:blank")  # triggers daemon check
        return self._daemon_client is not None

    def disconnect(self) -> None:
        """
        Disconnect from a live daemon without stopping it.
        The browser keeps running; the next ``open()`` with the same session
        will reconnect. Use ``close()`` to actually stop the browser.
        """
        if self._daemon_client:
            self._daemon_client.disconnect()
            self._daemon_client = None
            log(f"disconnected from daemon (session {self._session!r} still running)")

    def close(self, graceful_timeout: float = 5.0) -> None:
        """
        Shut down the browser.

        If connected to a daemon, sends a ``close`` command which stops
        the daemon process and the browser it holds.

        For directly-spawned browsers, sends SIGTERM first (when a session
        or persona is in use) to let Chromium flush in-memory state to
        disk, then SIGKILL if it doesn't exit within ``graceful_timeout``
        seconds.

        **Persistence timing under graceful shutdown** (#51):

        - **localStorage** flushes within ~5 s — leveldb writes are
          synchronous on ``setItem``, so the default ``graceful_timeout``
          of 5 s is sufficient.
        - **Cookies**: in persona/session mode the SDK passes
          ``--carbonyl-cookie-flush-interval-ms=1000`` (carbonyl#69),
          shortening the SQLite commit cycle from 30 s to 1 s so cookies
          persist within the default ``graceful_timeout``. On older
          runtimes that lack the patch, the flag is a no-op and the
          original 30 s window applies — drain ≥ 30 s before
          ``close()`` in that case.

        Visual-capture / persistence tests should hold the browser open
        for the appropriate window above before calling ``close()``.
        """
        # Tear down the uinput emitter first so its virtual devices are
        # destroyed even if Chromium shutdown errors.
        if self._uinput_emitter is not None:
            try:
                self._uinput_emitter.close()
            except Exception:
                pass
            self._uinput_emitter = None
        if self._daemon_client:
            self._daemon_client.close_daemon()
            self._daemon_client = None
            self._release_profile()
            return
        if self._child:
            try:
                import signal as _signal
                if self._child.isalive():
                    pgid = os.getpgid(self._child.pid)
                    if (self._session or self._persona) and graceful_timeout > 0:
                        # Graceful shutdown: SIGTERM → wait → SIGKILL
                        try:
                            os.killpg(pgid, _signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        deadline = time.time() + graceful_timeout
                        while time.time() < deadline and self._child.isalive():
                            time.sleep(0.2)
                    # Force kill anything still alive
                    try:
                        os.killpg(pgid, _signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self._child.terminate(force=True)
            except Exception:
                pass
        self._release_profile()

    def __enter__(self) -> "CarbonylBrowser":
        """Context manager entry — returns ``self``.

        Enables ``with CarbonylBrowser(...) as b:`` so ``close()`` runs on
        exception or normal exit (#24). Browser is NOT auto-opened — call
        :meth:`open` or :meth:`navigate` inside the block.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Persona profile passthrough
    # ------------------------------------------------------------------

    @property
    def persona(self) -> "Persona | None":
        """The typed :class:`carbonyl_agent.persona_apply.Persona` this
        browser was constructed with, or ``None`` if no typed persona was
        passed (the string-form ``persona="name"`` for profile keying does
        not populate this property).
        """
        return self._persona_obj

    def egress(self, **kwargs: Any) -> Any:
        """Return a persona-bound :class:`carbonyl_agent.egress.EgressClient`
        for HTTP traffic outside the browser (W3B #44).

        The returned client carries this browser's persona and, when a
        profile is active, its cookie jar path (so cookies set in either
        surface are visible to the other on the next session). Keyword
        arguments forward to :class:`EgressClient` — typically
        ``audit_mode=`` and ``timeout=``.

        Raises :class:`RuntimeError` when this browser was not constructed
        with a typed :class:`Persona`. The string-form ``persona="name"``
        profile-keying path does NOT populate :attr:`persona`, so it
        can't drive egress fingerprinting — pass a real
        :class:`Persona` object to enable egress.

        Example::

            from carbonyl_agent import CarbonylBrowser, Persona

            p = Persona.from_path("personas/ghost-01.toml")
            b = CarbonylBrowser(persona=p)
            r = b.egress().get("https://api.example.com/v1/me")
        """
        if self._persona_obj is None:
            raise RuntimeError(
                "browser.egress() requires a typed Persona — construct with "
                "CarbonylBrowser(persona=Persona.from_path(...)). The string "
                "form persona='name' is for profile-name keying only."
            )
        from carbonyl_agent.egress import EgressClient

        # Resolve cookie jar path from the active profile, if any. The
        # profile manager owns the user-data-dir; we co-locate the
        # egress cookie jar alongside Chromium's cookies SQLite so
        # operators see all cookie state in one place.
        cookie_jar_path = kwargs.pop("cookie_jar_path", None)
        if cookie_jar_path is None and self._profile_manager is not None:
            try:
                profile_dir = Path(self._profile_manager.profile_dir)
                cookie_jar_path = profile_dir / "egress-cookies.jsonl"
            except Exception:  # noqa: BLE001 — best-effort cookie jar resolution
                cookie_jar_path = None

        return EgressClient(
            self._persona_obj,
            cookie_jar_path=cookie_jar_path,
            **kwargs,
        )

    def _release_profile(self) -> None:
        if self._profile_manager is not None:
            try:
                self._profile_manager.release_lock()
            except Exception:
                pass
            self._profile_manager = None

    def _ensure_profile_manager(self) -> Any:
        if not self._persona:
            raise RuntimeError(
                "purge_profile/export_profile/import_profile require persona= "
                "to have been passed at construction."
            )
        from carbonyl_agent.profile import ProfileManager

        return self._profile_manager or ProfileManager(
            self._persona, profiles_dir=self._profiles_dir
        )

    def purge_profile(self) -> None:
        """Wipe this persona's stored browser state.

        Refuses while the browser is open — call :meth:`close` first.
        """
        if self._child or self._daemon_client:
            raise RuntimeError(
                "Cannot purge an open profile; call close() first."
            )
        self._ensure_profile_manager().purge_profile()

    def export_profile(self, path: str) -> str:
        """Write the persona's profile state to ``path`` as a tar.gz."""
        return str(self._ensure_profile_manager().export_profile(path))

    def import_profile(self, path: str) -> None:
        """Replace the persona's profile state from a tar.gz at ``path``.

        Refuses while the browser is open — call :meth:`close` first.
        """
        if self._child or self._daemon_client:
            raise RuntimeError(
                "Cannot import into an open profile; call close() first."
            )
        self._ensure_profile_manager().import_profile(path)


def search_duckduckgo(
    query: str,
    wait_load: float = 8.0,
    wait_results: float = 12.0,
) -> str:
    """
    Open DuckDuckGo, type `query` into the autofocused search box,
    submit, and return the results page as clean text.
    """
    browser = CarbonylBrowser()
    try:
        log("opening https://duckduckgo.com ...")
        browser.open("https://duckduckgo.com")

        log(f"waiting {wait_load}s for page load ...")
        browser.drain(wait_load)

        # DuckDuckGo autofocuses the search box — type directly
        log(f"typing: {query!r}")
        browser.send(query)
        browser.drain(1.5)   # let autocomplete settle

        log("submitting (Enter) ...")
        browser.send_key("enter")

        log(f"waiting {wait_results}s for results ...")
        browser.drain(wait_results)

        url = browser.nav_bar_url()
        log(f"current URL: {url}")

        return browser.page_text()
    finally:
        browser.close()


def log(msg: str) -> None:
    """Backwards-compatible INFO-level shim (#14).

    Existing call sites continue to work; new code should call the
    module logger directly (``_log.debug(...)`` etc.) for finer level
    control. Configure verbosity with ``CARBONYL_DEBUG=1`` or
    ``CARBONYL_LOG_LEVEL=DEBUG``.
    """
    _log.info(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Carbonyl browser automation")
    sub = parser.add_subparsers(dest="cmd")

    sp = sub.add_parser("search", help="Search DuckDuckGo and print results as text")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--wait-load", type=float, default=8.0, metavar="SEC")
    sp.add_argument("--wait-results", type=float, default=12.0, metavar="SEC")

    op = sub.add_parser("open", help="Open a URL and print page as text")
    op.add_argument("url")
    op.add_argument("--wait", type=float, default=10.0, metavar="SEC")

    args = parser.parse_args()

    if args.cmd == "search":
        print(search_duckduckgo(args.query, args.wait_load, args.wait_results))
    elif args.cmd == "open":
        browser = CarbonylBrowser()
        try:
            browser.open(args.url)
            browser.drain(args.wait)
            print(browser.page_text())
        finally:
            browser.close()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
