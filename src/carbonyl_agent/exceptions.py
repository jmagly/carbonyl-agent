"""Exception hierarchy for carbonyl-agent (#23).

Public exception types so callers can match on category instead of
string-matching ``RuntimeError`` messages.

Hierarchy:

.. code-block:: text

    Exception
      └── CarbonylError                         # base for anything we raise
            ├── BrowserCrashed                  # subprocess died unexpectedly
            ├── DaemonConnectionError(RuntimeError)
            │     # socket gone / handshake failed / closed mid-call
            ├── RenderTimeoutError(TimeoutError)
            │     # wait_for_render_settle gave up before idle window closed
            ├── BackendMismatchError(RuntimeError)
            │     # re-based — was bare RuntimeError pre-#23
            └── UinputUnavailableError(RuntimeError)
                  # re-based — was bare RuntimeError pre-#23

Backwards compatibility: all classes that previously inherited from
``RuntimeError`` still do via co-operative multiple inheritance, so any
existing ``except RuntimeError`` block keeps catching them. New code
should prefer the typed exception or :class:`CarbonylError` for a
catch-all.
"""
from __future__ import annotations


class CarbonylError(Exception):
    """Base class for every carbonyl-agent-raised exception.

    Catch this when you want to draw a clean line between SDK errors
    and library-internal exceptions you don't own.
    """


class BrowserCrashed(CarbonylError):
    """The Carbonyl subprocess exited unexpectedly while the SDK was
    driving it (PTY EOF, segfault, OOM kill, etc.).

    Carries optional ``returncode`` and ``stderr_tail`` attributes when
    available so callers can decide whether to restart with the same
    flags or back off.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr_tail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


class DaemonConnectionError(CarbonylError, RuntimeError):
    """The daemon socket is missing, refused the connection, closed
    mid-call, or violated the wire protocol.

    Subclasses ``RuntimeError`` for backwards compatibility — daemon
    code historically raised plain ``RuntimeError`` for these cases and
    callers may have written ``except RuntimeError``.
    """


class RenderTimeoutError(CarbonylError, TimeoutError):
    """Raised when an opt-in ``wait_for_render_settle(..., raise_on_timeout=True)``
    call exhausts its budget without the buffer settling.

    The default behavior of :meth:`CarbonylBrowser.wait_for_render_settle`
    is still to return ``False`` on timeout. Pass ``raise_on_timeout=True``
    to opt into this exception instead.
    """
