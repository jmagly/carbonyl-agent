#!/usr/bin/env python3
"""
Carbonyl browser automation layer.

Spawns Carbonyl in a PTY (local binary or Docker fallback), sends
keystrokes, and returns the rendered screen as plain text via pyte.

Binary search order:
    1. CARBONYL_BIN env var (explicit path)
    2. ~/.local/share/carbonyl/bin/<triple>/carbonyl  (installed via `carbonyl-agent install`)
    3. `carbonyl` on $PATH
    4. Docker fallback: docker run fathyb/carbonyl

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
from typing import Any

import pexpect
import pyte

# Terminal dimensions Carbonyl will render to
COLS = 220
ROWS = 50

# Default install location used by `carbonyl-agent install`
_DEFAULT_INSTALL_DIR = Path.home() / ".local" / "share" / "carbonyl" / "bin"

# Docker fallback: require explicit opt-in and use a pinned digest.
# To update the digest, pull the latest image and run:
#   docker inspect --format='{{index .RepoDigests 0}}' fathyb/carbonyl
_DOCKER_IMAGE_DIGEST = "fathyb/carbonyl@sha256:564733cd0e7c4ed82e3eb872df511092f84e848afd807f1c1db82e43c867aab0"
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
ANTI_FEDCM_FLAGS: list[str] = [
    "--disable-features=FedCm,FedCmAuthz,FedCmButtonMode,FedCmIdPRegistration",
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


class CarbonylBrowser:
    def __init__(
        self,
        cols: int = COLS,
        rows: int = ROWS,
        session: str | None = None,
        *,
        extra_flags: list[str] | None = None,
        base_flags: list[str] | None = None,
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
            extra_flags: Additional Chromium command-line flags to append.
                     Compose from published flag groups for common scenarios::

                         from carbonyl_agent.browser import (
                             ANTI_FEDCM_FLAGS,   # disable Google One Tap
                         )

                         b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS)

            base_flags: Completely replace the default flag set
                     (``DEFAULT_HEADLESS_FLAGS``). Rarely needed — prefer
                     ``extra_flags`` for additive changes.
        """
        self.cols = cols
        self.rows = rows
        self._session = session
        self._screen: Any = pyte.Screen(cols, rows)
        self._stream: Any = pyte.ByteStream(self._screen)
        self._child: Any | None = None
        self._daemon_client: Any | None = None
        self._flags: list[str] = list(
            base_flags if base_flags is not None else DEFAULT_HEADLESS_FLAGS
        )
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
            sm.clean_stale_lock(self._session)
            profile = sm.profile_dir(self._session)
            args.append(f"--user-data-dir={profile}")
            log(f"session: {self._session!r}  profile: {profile}")

        args.append(url)

        if binary:
            lib_dir = str(binary.parent)
            env = {**os.environ, "LD_LIBRARY_PATH": lib_dir}
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

    def send(self, text: str) -> None:
        """Type text into the browser (encodes as UTF-8 bytes)."""
        if self._daemon_client:
            self._daemon_client.send(text)
            return
        assert self._child is not None
        self._child.send(text.encode("utf-8"))

    def mouse_move(self, col: int, row: int) -> None:
        """
        Send a mouse-move event at terminal cell (col, row).

        Uses SGR button code 32 (0x20 = MouseMove mask, no button pressed).
        Carbonyl translates this into a DOM ``mousemove`` event delivered to
        the page — essential for sites that require mouse-movement entropy
        before accepting interaction (e.g. Akamai Bot Manager sensor).
        """
        if self._daemon_client:
            self._daemon_client.mouse_move(col, row)
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
        """Send a left-click at terminal cell (col, row) using SGR mouse protocol."""
        if self._daemon_client:
            self._daemon_client.click(col, row)
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
        """Send a named key sequence."""
        if self._daemon_client:
            self._daemon_client.send_key(key)
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
        is in use) to let Chromium flush session cookies to disk, then
        SIGKILL if it doesn't exit within ``graceful_timeout`` seconds.
        """
        if self._daemon_client:
            self._daemon_client.close_daemon()
            self._daemon_client = None
            return
        if self._child:
            try:
                import signal as _signal
                if self._child.isalive():
                    pgid = os.getpgid(self._child.pid)
                    if self._session and graceful_timeout > 0:
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
    print(f"[carbonyl] {msg}", file=sys.stderr, flush=True)


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
