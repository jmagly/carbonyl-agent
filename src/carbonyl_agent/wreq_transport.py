"""
WreqTransport — httpx.BaseTransport wrapper around the Rust ``wreq``
backend (W3B Phase 2.4 — `Refs: roctinam/carbonyl-agent#83`).

The transport delegates each request to ``carbonyl_wreq.send_request``,
which is built from ``crates/carbonyl-wreq`` via ``maturin develop
--features python`` (#85). When that native module isn't importable the
transport raises :class:`WreqUnavailable` — :class:`EgressClient`
catches that and falls back to the Phase 1 httpx path.

# Why a thin wrapper instead of httpx directly

The whole point of Phase 2 is that the on-wire TLS fingerprint matches
the persona. ``httpx`` uses Python's stdlib SSL backend, whose JA4 has
nothing to do with any browser. ``wreq`` uses BoringSSL with browser-
emulating ClientHello generation — its JA4 reflects the persona. The
transport boundary stays at httpx so callers don't have to learn a new
API; the bytes on the wire change underneath.

# Returned audit data

In addition to the standard httpx response, ``send_request`` returns
the wire-captured JA4. We stash that on the response object so
``EgressClient`` can record it in the audit row.
"""

from __future__ import annotations

import contextlib
import importlib
import typing as t

if t.TYPE_CHECKING:
    import httpx


class WreqUnavailable(ImportError):
    """Raised when the ``carbonyl_wreq`` native module isn't importable.

    The most common cause: the user installed ``carbonyl-agent[wreq]``
    but hasn't run ``maturin develop --manifest-path
    crates/carbonyl-wreq/Cargo.toml --features python`` to build the
    native module. Phase 2.5 (#84) wires the build into pip install.

    EgressClient (#83) catches this and falls back to the httpx
    stdlib-SSL path, marking the audit row with
    ``transport: httpx-fallback`` so consumers can tell the persona's
    wire fingerprint is NOT in effect.
    """


def is_available() -> bool:
    """Cheap import probe — returns True iff ``carbonyl_wreq`` is
    importable AND exposes the expected ``send_request`` entry point.

    Importable-but-incomplete (e.g. version skew between the Rust crate
    and this Python module) is treated as "not available" so the
    fallback path engages cleanly.
    """
    try:
        mod = importlib.import_module("carbonyl_wreq")
    except ImportError:
        return False
    return hasattr(mod, "send_request")


def _load_send_request() -> t.Callable[..., t.Any]:
    """Resolve ``carbonyl_wreq.send_request`` or raise :class:`WreqUnavailable`."""
    try:
        mod = importlib.import_module("carbonyl_wreq")
    except ImportError as e:
        raise WreqUnavailable(
            "carbonyl_wreq native module not importable. "
            "Build with: maturin develop --manifest-path "
            "crates/carbonyl-wreq/Cargo.toml --features python"
        ) from e
    send = getattr(mod, "send_request", None)
    if send is None:
        raise WreqUnavailable(
            "carbonyl_wreq imported but lacks `send_request` — "
            "version skew between Rust crate and Python wrapper. "
            "Rebuild via maturin develop --features python"
        )
    return send  # type: ignore[no-any-return]


class WreqTransport:
    """Sync httpx.BaseTransport delegating to the wreq backend.

    We don't inherit from ``httpx.BaseTransport`` at class definition
    time because httpx is an optional dep — importing the symbol at
    module load would force every consumer to install httpx. The duck-
    typed protocol is the only contract httpx actually relies on:
    a ``handle_request(httpx.Request) -> httpx.Response`` method.
    """

    def __init__(self, persona_toml: str, timeout_seconds: float = 30.0) -> None:
        """Create a transport bound to a single persona.

        :param persona_toml: The persona TOML body (`Persona.raw_toml()`).
            wreq parses this on every request — the persona is the
            canonical source of truth for browser_family, ja4,
            http2_akamai, and headers. Re-parsing per request keeps the
            Rust side stateless and lets the Python side mutate persona
            fields between requests if needed.
        :param timeout_seconds: Overall request timeout. wreq's
            BoringSSL stack honors this; the httpx-level timeout
            settings are ignored when this transport is in use.

        Raises :class:`WreqUnavailable` if the native module isn't
        present.
        """
        self._send_request = _load_send_request()
        self._persona_toml = persona_toml
        self._timeout_seconds = timeout_seconds
        # Latest captured JA4 from `handle_request`. EgressClient reads
        # this immediately after each call to populate the audit row.
        self._last_captured_ja4: str | None = None

    @property
    def last_captured_ja4(self) -> str | None:
        """JA4 captured from the most recent request, or ``None`` before
        the first call. Updated synchronously inside ``handle_request``."""
        return self._last_captured_ja4

    def handle_request(self, request: "httpx.Request") -> "httpx.Response":
        import httpx  # local import — egress is the only consumer

        headers = {k.decode(): v.decode() for k, v in request.headers.raw}
        body: bytes | None = None
        if request.content:
            body = bytes(request.content)

        status, response_headers, response_body, captured_ja4 = self._send_request(
            self._persona_toml,
            request.method,
            str(request.url),
            headers,
            body,
            self._timeout_seconds,
        )

        self._last_captured_ja4 = captured_ja4

        # httpx.Response expects headers as a list of (str, str) tuples
        # OR an httpx.Headers instance — passing the raw list is fine.
        return httpx.Response(
            status_code=int(status),
            headers=list(response_headers),
            content=bytes(response_body),
            request=request,
        )

    def close(self) -> None:
        """No persistent state to release. wreq's connection pool is
        owned by the Rust side and lives for the process lifetime; the
        embedded tokio runtime is reused across transports. A future
        refactor (#85 follow-up) may expose explicit close semantics.
        """
        with contextlib.suppress(Exception):
            # Future-proofing for when the native module exposes a
            # per-transport close hook.
            pass

    def __enter__(self) -> "WreqTransport":
        return self

    def __exit__(self, *_args: t.Any) -> None:
        self.close()
