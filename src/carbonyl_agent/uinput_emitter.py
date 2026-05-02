"""OS-level keyboard and mouse input via /dev/uinput.

Why this exists: PTY-driven keystrokes and SGR mouse events arrive at
Chromium as synthetic events with ``event.isTrusted = false``. Modern
React forms, SPA frameworks, and bot-detection libraries refuse to
update controlled-input state or fire submit handlers when ``isTrusted``
is false. By emitting events through the kernel input subsystem
(``/dev/uinput``), the events traverse the X server's normal input
pipeline and arrive at the browser as ``isTrusted = true`` — the same
provenance as a physical keyboard and mouse plugged into the host.

This module is the SDK side of ADR-002 rev 2 (see
``roctinam/carbonyl/docs/adr-002-trusted-input-approach.md``). It is
designed to be imported and used from inside the
``carbonyl-agent-qa-runner`` container, where ``/dev/uinput`` is wired
through and an Xorg server with ``evdev``/``libinput`` drivers is
running.

Usage::

    from carbonyl_agent.uinput_emitter import UinputEmitter

    with UinputEmitter() as e:
        e.click(640, 420)
        e.type_text("hello world")
        e.press_key("enter")

Falls through cleanly if ``python-uinput`` is not installed: import
remains successful but instantiation raises a helpful error so the SDK
itself stays importable on hosts without uinput support.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Optional python-uinput import
# ---------------------------------------------------------------------------

try:
    import uinput as _uinput

    _UINPUT_AVAILABLE = True
    _UINPUT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover  - exercised via test mocks
    _uinput = None
    _UINPUT_AVAILABLE = False
    _UINPUT_IMPORT_ERROR = exc


# ---------------------------------------------------------------------------
# Key vocabulary
# ---------------------------------------------------------------------------

# Map of ASCII characters that produce a key without modifiers.
# Each maps to the python-uinput key constant name (string form,
# resolved at runtime via getattr so the dict can be defined without
# python-uinput at import time).
_PRINTABLE_LOWER: dict[str, str] = {c: f"KEY_{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz"}
_PRINTABLE_DIGITS: dict[str, str] = {c: f"KEY_{c}" for c in "0123456789"}
_PRINTABLE_PUNCT: dict[str, str] = {
    " ": "KEY_SPACE",
    "-": "KEY_MINUS",
    "=": "KEY_EQUAL",
    "[": "KEY_LEFTBRACE",
    "]": "KEY_RIGHTBRACE",
    "\\": "KEY_BACKSLASH",
    ";": "KEY_SEMICOLON",
    "'": "KEY_APOSTROPHE",
    ",": "KEY_COMMA",
    ".": "KEY_DOT",
    "/": "KEY_SLASH",
    "`": "KEY_GRAVE",
}

# Shifted variants. Value is (base_key_name, shift_required=True).
_SHIFTED: dict[str, str] = {
    "!": "KEY_1",
    "@": "KEY_2",
    "#": "KEY_3",
    "$": "KEY_4",
    "%": "KEY_5",
    "^": "KEY_6",
    "&": "KEY_7",
    "*": "KEY_8",
    "(": "KEY_9",
    ")": "KEY_0",
    "_": "KEY_MINUS",
    "+": "KEY_EQUAL",
    "{": "KEY_LEFTBRACE",
    "}": "KEY_RIGHTBRACE",
    "|": "KEY_BACKSLASH",
    ":": "KEY_SEMICOLON",
    '"': "KEY_APOSTROPHE",
    "<": "KEY_COMMA",
    ">": "KEY_DOT",
    "?": "KEY_SLASH",
    "~": "KEY_GRAVE",
}

# Named keys — vocabulary matches ``CarbonylBrowser.send_key`` for drop-in
# compatibility. Add to both maps when introducing a new alias.
_NAMED_KEYS: dict[str, str] = {
    "enter": "KEY_ENTER",
    "return": "KEY_ENTER",
    "tab": "KEY_TAB",
    "backspace": "KEY_BACKSPACE",
    "delete": "KEY_DELETE",
    "escape": "KEY_ESC",
    "esc": "KEY_ESC",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "home": "KEY_HOME",
    "end": "KEY_END",
    "pageup": "KEY_PAGEUP",
    "pagedown": "KEY_PAGEDOWN",
    "space": "KEY_SPACE",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UinputUnavailableError(RuntimeError):
    """Raised when ``python-uinput`` is missing or ``/dev/uinput`` is not
    writable. The message includes remediation steps for the most common
    setup failures (kernel module not loaded, user not in input group,
    container missing ``--device=/dev/uinput``).
    """


class UnsupportedKeyError(ValueError):
    """Raised when a character or named key cannot be translated to a
    physical key code (e.g. non-ASCII unicode without an IME)."""


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


class UinputEmitter:
    """Open virtual keyboard and mouse devices, emit input events through
    the kernel input subsystem.

    The class is a context manager. It reserves a unique device-name pair
    on instantiation; collisions across concurrent agents are avoided by
    salting with PID and a per-process counter::

        carbonyl-agent-<pid>-<n>-keyboard
        carbonyl-agent-<pid>-<n>-mouse

    Mouse coordinates are absolute. The mouse device is registered with
    `ABS_X`/`ABS_Y` ranges of ``[0, viewport_max]`` (default 65535 for
    sub-pixel precision); pass ``viewport=(width, height)`` to scale clicks
    in CSS-pixel terms instead of raw absolute units.

    Args:
        device_suffix: Optional string appended to the default device name
            so test fixtures can pin a known name. Default uses the per-pid
            counter.
        viewport: Tuple ``(width, height)`` used to scale absolute mouse
            coordinates. ``(640, 420)`` means ``move_mouse(640, 420)`` lands
            at the bottom-right of the virtual viewport. Default
            ``(65535, 65535)`` (raw absolute units).
        keystroke_delay: Per-keystroke dwell in seconds (down → sleep → up).
            Default 0.005s. Set higher to defeat keystroke-rate fingerprinting.
        device_factory: Internal seam for tests; replace with a callable
            that returns a mock when ``python-uinput`` would otherwise be
            invoked.
    """

    DEFAULT_VIEWPORT: tuple[int, int] = (65535, 65535)

    # Module-level counter so concurrent emitters in one process don't
    # collide on device name even if PID is the same (forks).
    _counter = 0

    def __init__(
        self,
        device_suffix: str | None = None,
        *,
        viewport: tuple[int, int] = DEFAULT_VIEWPORT,
        keystroke_delay: float = 0.005,
        device_factory: Any | None = None,
    ) -> None:
        self.viewport = viewport
        self.keystroke_delay = keystroke_delay
        self._device_factory = device_factory or _real_device_factory
        self._closed = False

        if device_suffix is None:
            UinputEmitter._counter += 1
            device_suffix = f"{os.getpid()}-{UinputEmitter._counter}"
        self._suffix = device_suffix
        self.kbd_name = f"carbonyl-agent-{device_suffix}-keyboard"
        self.mouse_name = f"carbonyl-agent-{device_suffix}-mouse"

        self._kbd: Any = None
        self._mouse: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Allocate the virtual keyboard and mouse devices. Idempotent."""
        if self._kbd is not None:
            return
        self._preflight()
        kbd_events = self._all_keyboard_event_codes()
        mouse_events = self._all_mouse_event_codes()
        try:
            self._kbd = self._device_factory(kbd_events, self.kbd_name)
        except PermissionError as exc:
            raise UinputUnavailableError(
                f"Permission denied opening /dev/uinput as uid={os.getuid()}: "
                f"{exc}\n"
                "  - Add your user to the 'input' group and re-login\n"
                "  - In a container, pass --device=/dev/uinput "
                "and --group-add input"
            ) from exc
        try:
            self._mouse = self._device_factory(mouse_events, self.mouse_name)
        except PermissionError as exc:
            try:
                self._kbd.destroy()
            except Exception:
                pass
            self._kbd = None
            raise UinputUnavailableError(
                f"Permission denied opening /dev/uinput as uid={os.getuid()}: "
                f"{exc}\n"
                "  - Add your user to the 'input' group and re-login\n"
                "  - In a container, pass --device=/dev/uinput "
                "and --group-add input"
            ) from exc
        except Exception:
            # Roll back keyboard if mouse creation fails so we don't leak
            # half-open state.
            try:
                self._kbd.destroy()
            except Exception:
                pass
            self._kbd = None
            raise

    def close(self) -> None:
        """Tear down both devices. Safe to call multiple times."""
        if self._closed:
            return
        for dev_attr in ("_mouse", "_kbd"):
            dev = getattr(self, dev_attr, None)
            if dev is not None:
                try:
                    dev.destroy()
                except Exception:
                    pass
            setattr(self, dev_attr, None)
        self._closed = True

    def __enter__(self) -> "UinputEmitter":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public input API
    # ------------------------------------------------------------------

    def type_text(self, text: str) -> None:
        """Type a string. ASCII printable characters and named whitespace
        (`\n` → Enter, `\t` → Tab) are supported. Non-ASCII characters
        raise :class:`UnsupportedKeyError`.
        """
        self._require_open()
        for ch in text:
            if ch == "\n":
                self.press_key("enter")
                continue
            if ch == "\t":
                self.press_key("tab")
                continue
            self._type_char(ch)

    def press_key(self, name: str) -> None:
        """Press and release a named key (`enter`, `tab`, `escape`, etc.).
        Vocabulary matches :meth:`CarbonylBrowser.send_key`.
        """
        self._require_open()
        key_const = _NAMED_KEYS.get(name.lower())
        if key_const is None:
            raise UnsupportedKeyError(
                f"Unknown key name: {name!r}. Valid: {sorted(_NAMED_KEYS)}"
            )
        self._tap_keyboard(key_const)

    def move_mouse(self, x: int, y: int) -> None:
        """Absolute mouse-move to ``(x, y)`` in viewport units."""
        self._require_open()
        self._abs_move(x, y)

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Move to ``(x, y)`` then press-release the named mouse button.
        Buttons: ``"left"`` (default), ``"right"``, ``"middle"``.
        """
        self._require_open()
        btn_const = _MOUSE_BUTTONS.get(button.lower())
        if btn_const is None:
            raise UnsupportedKeyError(
                f"Unknown button: {button!r}. Valid: {sorted(_MOUSE_BUTTONS)}"
            )
        self._abs_move(x, y)
        self._emit_mouse(self._lookup_event(btn_const), 1)
        self._sync_mouse()
        time.sleep(self.keystroke_delay)
        self._emit_mouse(self._lookup_event(btn_const), 0)
        self._sync_mouse()

    def mouse_path(self, points: list[tuple[int, int]], delay: float = 0.05) -> None:
        """Move through a sequence of ``(x, y)`` points with ``delay``
        seconds between each. Used to feed organic mouse-movement entropy
        to bot-detection scoring (e.g. Akamai Bot Manager).
        """
        self._require_open()
        for x, y in points:
            self._abs_move(x, y)
            time.sleep(delay)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _preflight(self) -> None:
        if not _UINPUT_AVAILABLE:
            raise UinputUnavailableError(
                "python-uinput is not installed. Install it with:\n"
                "    pip install python-uinput\n"
                "Original import error: " + repr(_UINPUT_IMPORT_ERROR)
            )
        dev = Path("/dev/uinput")
        if not dev.exists():
            raise UinputUnavailableError(
                "/dev/uinput does not exist. Load the kernel module:\n"
                "    sudo modprobe uinput\n"
                "Or build a kernel with CONFIG_INPUT_UINPUT=y."
            )
        if not os.access(dev, os.W_OK):
            raise UinputUnavailableError(
                "/dev/uinput is not writable by the current user. Either:\n"
                "  - Add your user to the 'input' group and re-login\n"
                "  - Install the udev rule at "
                "scripts/setup-uinput-host.sh in carbonyl-agent\n"
                "  - In a container, pass --device=/dev/uinput "
                "and --group-add input"
            )

    def _all_keyboard_event_codes(self) -> list[Any]:
        """Every printable + named key, plus shift modifiers, registered
        on the virtual keyboard so any later key emission is acceptable.
        """
        codes: set[Any] = {
            self._lookup_event("KEY_LEFTSHIFT"),
            self._lookup_event("KEY_RIGHTSHIFT"),
        }
        for table in (_PRINTABLE_LOWER, _PRINTABLE_DIGITS, _PRINTABLE_PUNCT,
                      _SHIFTED, _NAMED_KEYS):
            for name in table.values():
                codes.add(self._lookup_event(name))
        return sorted(codes, key=lambda e: tuple(e) if isinstance(e, tuple) else (0,))

    def _all_mouse_event_codes(self) -> list[Any]:
        if _uinput is None:  # safety net; _preflight already covered this
            return []
        codes: list[Any] = []
        for btn in _MOUSE_BUTTONS.values():
            codes.append(self._lookup_event(btn))
        max_x, max_y = self.viewport
        codes.append(_uinput.ABS_X + (0, max_x, 0, 0))
        codes.append(_uinput.ABS_Y + (0, max_y, 0, 0))
        return codes

    def _lookup_event(self, const_name: str) -> Any:
        if _uinput is None:
            raise UinputUnavailableError("python-uinput unavailable")
        try:
            return getattr(_uinput, const_name)
        except AttributeError as exc:
            raise UnsupportedKeyError(
                f"python-uinput has no event constant {const_name!r}"
            ) from exc

    def _type_char(self, ch: str) -> None:
        if ch in _PRINTABLE_LOWER:
            self._tap_keyboard(_PRINTABLE_LOWER[ch])
        elif ch in _PRINTABLE_DIGITS:
            self._tap_keyboard(_PRINTABLE_DIGITS[ch])
        elif ch in _PRINTABLE_PUNCT:
            self._tap_keyboard(_PRINTABLE_PUNCT[ch])
        elif ch.isalpha() and ch.isupper() and ch.lower() in _PRINTABLE_LOWER:
            # Uppercase ASCII letter — shift + the lowercase key.
            self._tap_keyboard(_PRINTABLE_LOWER[ch.lower()], shift=True)
        elif ch in _SHIFTED:
            self._tap_keyboard(_SHIFTED[ch], shift=True)
        else:
            raise UnsupportedKeyError(
                f"Cannot translate character {ch!r} (codepoint {ord(ch):#x}) "
                "to a physical key. Non-ASCII text typically requires an IME."
            )

    def _tap_keyboard(self, key_const: str, *, shift: bool = False) -> None:
        if shift:
            self._emit_keyboard(self._lookup_event("KEY_LEFTSHIFT"), 1)
            self._sync_keyboard()
        self._emit_keyboard(self._lookup_event(key_const), 1)
        self._sync_keyboard()
        time.sleep(self.keystroke_delay)
        self._emit_keyboard(self._lookup_event(key_const), 0)
        self._sync_keyboard()
        if shift:
            self._emit_keyboard(self._lookup_event("KEY_LEFTSHIFT"), 0)
            self._sync_keyboard()

    def _abs_move(self, x: int, y: int) -> None:
        max_x, max_y = self.viewport
        clamped_x = max(0, min(max_x, x))
        clamped_y = max(0, min(max_y, y))
        if _uinput is None:
            raise UinputUnavailableError("python-uinput unavailable")
        # mouse.emit takes (event_tuple, value, syn=False)
        self._mouse.emit(_uinput.ABS_X, clamped_x, syn=False)
        self._mouse.emit(_uinput.ABS_Y, clamped_y, syn=False)
        self._sync_mouse()

    def _emit_keyboard(self, ev: Any, value: int) -> None:
        self._kbd.emit(ev, value, syn=False)

    def _sync_keyboard(self) -> None:
        if _uinput is None:
            return
        self._kbd.syn()

    def _emit_mouse(self, ev: Any, value: int) -> None:
        self._mouse.emit(ev, value, syn=False)

    def _sync_mouse(self) -> None:
        if _uinput is None:
            return
        self._mouse.syn()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "UinputEmitter is closed; create a new instance to emit again."
            )
        if self._kbd is None:
            self.open()


# ---------------------------------------------------------------------------
# Mouse buttons
# ---------------------------------------------------------------------------

_MOUSE_BUTTONS: dict[str, str] = {
    "left": "BTN_LEFT",
    "right": "BTN_RIGHT",
    "middle": "BTN_MIDDLE",
}


# ---------------------------------------------------------------------------
# Real-uinput device factory
# ---------------------------------------------------------------------------


def _real_device_factory(events: list[Any], name: str) -> Any:
    """Construct a real :class:`uinput.Device`. Replaced in tests with a
    factory that returns a mock object."""
    if _uinput is None:
        raise UinputUnavailableError("python-uinput unavailable")
    return _uinput.Device(events, name=name)
