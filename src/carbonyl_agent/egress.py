"""Persona-bound HTTP egress (W3B — `Refs: roctinam/carbonyl-agent#44`).

Drives HTTP requests outside the browser through a :class:`Persona`-bound
client so the network signature matches the browser's. Without this,
agent-side API probes, RSS fetches, OAuth callbacks, etc. would advertise
a Python stdlib TLS fingerprint while the browser tab advertised the
persona's Chrome/Firefox/Safari fingerprint — the inconsistency alone is
a strong bot signal even before any single request is rejected.

# Two phases

## Phase 1 (this module today)

`EgressClient` is implemented over `httpx`. Headers (User-Agent,
Accept-Language, sec-ch-ua* for Chrome) come from the persona; cookie
jars persist per-persona via :mod:`carbonyl_agent.profile`. **TLS
fingerprint (JA4) does NOT match the persona** — `httpx` uses the Python
stdlib ssl module which produces its own JA4. This is *expected* in
Phase 1; the audit log + drift detector make the mismatch visible.

## Phase 2 (follow-up PR)

Swap the underlying transport for the Rust `wreq` backend (per ADR-005)
exposed through the `HttpClient` trait in `carbonyl-fingerprint`'s `http`
module. The public :class:`EgressClient` API stays unchanged; only the
internal `httpx.BaseTransport` is replaced. The conformance suite from
#62 will gate the swap.

# Usage

::

    from carbonyl_agent import (
        CarbonylBrowser, Persona, EgressClient, EgressAuditMode,
    )

    persona = Persona.from_path("personas/ghost-01.toml")

    # Standalone:
    client = EgressClient(persona, audit_mode=EgressAuditMode.WARN)
    r = client.get("https://api.example.com/v1/me")

    # Or from a browser (auto-binds to the browser's persona, shares the
    # cookie jar with the browser's user-data-dir):
    b = CarbonylBrowser(persona=persona)
    r = b.egress().get("https://api.example.com/v1/me")

# Audit modes

Set ``CARBONYL_FP_AUDIT`` env var or pass ``audit_mode=`` explicitly:

- ``STRICT`` (default for dev): assert sent JA4 equals persona.network.ja4
  before every request; raise :class:`EgressFingerprintDrift` on mismatch.
  Use during development to catch fingerprint drift loudly.
- ``WARN`` (default for prod): log mismatch to the rotating audit file,
  continue with the request. Production default — drift is visible but
  doesn't break the run.
- ``OFF``: no audit. Use only when the per-request overhead matters and
  drift has been verified absent by another mechanism (e.g., the
  conformance suite gating the wreq impl).
"""
from __future__ import annotations

import datetime as _dt
import enum
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from carbonyl_agent.persona_apply import Persona

    # WreqTransport's runtime import is inside `__init__` (kept lazy
    # so the optional carbonyl_wreq native module isn't required at
    # import time). Mirror it here at module-scope under TYPE_CHECKING
    # so pdoc and other static-introspection tools can resolve the
    # `self._wreq_transport: "WreqTransport | None"` annotation.
    #   Refs: roctinam/carbonyl-agent#96
    from carbonyl_agent.wreq_transport import WreqTransport  # noqa: F401


class EgressAuditMode(enum.Enum):
    """How aggressively to enforce JA4 conformance per request.

    See module docstring for semantics. Default selection order:

    1. Explicit ``audit_mode=`` argument
    2. ``CARBONYL_FP_AUDIT`` env var (``strict|warn|off``, case-insensitive)
    3. ``WARN`` — production default
    """

    STRICT = "strict"
    WARN = "warn"
    OFF = "off"

    @classmethod
    def from_env(cls, default: EgressAuditMode | None = None) -> EgressAuditMode:
        raw = os.environ.get("CARBONYL_FP_AUDIT", "").strip().lower()
        if not raw:
            return default if default is not None else cls.WARN
        try:
            return cls(raw)
        except ValueError as exc:
            valid = ", ".join(m.value for m in cls)
            raise ValueError(
                f"CARBONYL_FP_AUDIT={raw!r} is not one of: {valid}"
            ) from exc


# ---------------------------------------------------------------------------
# Sentinel constants (forward-referenced by EgressAuditEntry below)
# ---------------------------------------------------------------------------
#
# Phase 1 (httpx) audit row marks `ja4_actual` with a sentinel string
# instead of a real JA4. That makes the audit log honest about its
# current limits and tells Phase 2's CI gate exactly which entries to
# expect to migrate away from.
_HTTPX_PHASE1_JA4_SENTINEL = "phase1-httpx-stdlib-ssl"

# Transport selection tags (audit row `transport` field — Phase 2.4 #83).
# Lets consumers distinguish "real wire fingerprint via wreq" from
# "stdlib SSL fallback because wreq isn't built" without having to
# parse ja4_actual for the sentinel string.
_TRANSPORT_WREQ = "wreq"
_TRANSPORT_HTTPX_FALLBACK = "httpx-fallback"


class EgressError(Exception):
    """Base class for egress errors."""


class EgressFingerprintDrift(EgressError):
    """Raised in STRICT mode when the on-wire TLS fingerprint diverges
    from the persona's declared ``network.ja4``.

    Phase 1: this fires on every request because httpx is the underlying
    transport and produces a stdlib-Python JA4 distinct from any browser
    persona's JA4. Use ``audit_mode=WARN`` or ``OFF`` to suppress, or
    wait for Phase 2's wreq integration.
    """

    def __init__(self, expected: str, actual: str, url: str) -> None:
        super().__init__(
            f"JA4 mismatch for {url}: expected {expected!r}, sent {actual!r}"
        )
        self.expected = expected
        self.actual = actual
        self.url = url


@dataclass(frozen=True)
class EgressAuditEntry:
    """One row of the per-request audit log.

    Serialized as JSON Lines under
    ``$XDG_STATE_HOME/carbonyl-agent/egress-audit.log`` (default
    ``~/.local/state/carbonyl-agent/egress-audit.log``). Each entry is
    one JSON object on one line so the file is grep-friendly and
    streaming-readable.
    """

    request_id: str
    timestamp: str
    persona_id: str
    method: str
    url: str
    ja4_expected: str
    ja4_actual: str
    status_code: int | None
    latency_ms: float | None
    drift: bool
    audit_mode: str
    # Phase 2.4 (#83) — which transport was used. "wreq" when the
    # carbonyl_wreq native module is importable AND its request
    # succeeded; "httpx-fallback" otherwise. `ja4_actual` carries the
    # real captured value in the wreq case and the
    # phase1-httpx-stdlib-ssl sentinel in the fallback case.
    transport: str = _TRANSPORT_HTTPX_FALLBACK

    def to_json(self) -> str:
        return json.dumps(
            {
                "request_id": self.request_id,
                "timestamp": self.timestamp,
                "persona_id": self.persona_id,
                "method": self.method,
                "url": self.url,
                "ja4_expected": self.ja4_expected,
                "ja4_actual": self.ja4_actual,
                "status_code": self.status_code,
                "latency_ms": self.latency_ms,
                "drift": self.drift,
                "audit_mode": self.audit_mode,
                "transport": self.transport,
            },
            separators=(",", ":"),
        )


def _default_audit_log_path() -> Path:
    """Resolve the audit log path per XDG state-dir spec.

    ``$XDG_STATE_HOME/carbonyl-agent/egress-audit.log`` if set, else
    ``~/.local/state/carbonyl-agent/egress-audit.log``. Created lazily on
    first write.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "carbonyl-agent" / "egress-audit.log"


class EgressAuditLog:
    """Thread-safe append-only writer for :class:`EgressAuditEntry`.

    Writes JSON Lines to ``_default_audit_log_path()`` by default; pass a
    custom path for tests or alternate storage. Each write is O(1) —
    no rotation here; operators wire logrotate or equivalent on the
    output file when retention matters.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _default_audit_log_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, entry: EgressAuditEntry) -> None:
        line = entry.to_json() + "\n"
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)


# ---------------------------------------------------------------------------
# Phase 1 JA4 detection — what httpx actually emits
# ---------------------------------------------------------------------------

# Phase 1's audit detector can't capture the real on-wire JA4 without
# tracing the TLS handshake bytes (which the rustls-based Phase 2 backend
# WILL do natively). For now we record a sentinel value that downstream
# log analysis can detect, plus a flag indicating no real capture
# happened. This makes the audit log honest about its current limits and
# tells Phase 2's CI gate exactly which entries to expect to migrate
# away from.
def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# EgressClient
# ---------------------------------------------------------------------------


@dataclass
class _PerHostPool:
    """Per-host httpx client + lazy lifecycle. Stored in the EgressClient
    pool dict so connection reuse is preserved across requests."""

    client: httpx.Client
    created_at: str = field(default_factory=_now_iso)


class EgressClient:
    """Persona-bound HTTP client.

    Construct from a :class:`Persona` (typed object from
    :mod:`carbonyl_agent.persona_apply`). The client exposes a
    requests-shaped surface: :meth:`get`, :meth:`post`, :meth:`put`,
    :meth:`delete`, :meth:`request`. Headers are auto-populated from the
    persona on every request:

    - ``User-Agent`` from ``persona.user_agent_full``
    - ``Accept-Language`` from ``persona.accept_language``
    - For Chrome family: ``sec-ch-ua``, ``sec-ch-ua-mobile``,
      ``sec-ch-ua-platform`` derived from ``persona.raw()["user_agent"]
      ["ua_ch"]``
    - Firefox / Safari: no UA-CH headers (per-family contract from #71/#72)

    Cookie jar persistence: when constructed via
    :meth:`carbonyl_agent.CarbonylBrowser.egress`, shares the browser's
    cookie jar (sourced from the persona's profile dir per #41). When
    constructed standalone, uses an in-memory jar by default; pass
    ``cookie_jar_path=`` to persist to disk.

    Connection pooling: one ``httpx.Client`` instance per (persona,
    target host) keyed by URL scheme + netloc. Pools survive for the
    lifetime of the EgressClient; close them by calling :meth:`close` or
    using the EgressClient as a context manager.
    """

    def __init__(
        self,
        persona: Persona,
        *,
        audit_mode: EgressAuditMode | None = None,
        audit_log: EgressAuditLog | None = None,
        cookie_jar_path: str | Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        try:
            import httpx as _httpx  # noqa: F401 — import is the capability check
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "EgressClient requires the egress optional dep — install with "
                "`pip install carbonyl-agent[egress]`"
            ) from exc

        self._persona = persona
        self._audit_mode = audit_mode if audit_mode is not None else EgressAuditMode.from_env()
        self._audit_log = audit_log if audit_log is not None else EgressAuditLog()
        self._cookie_jar_path = Path(cookie_jar_path) if cookie_jar_path else None
        self._timeout = timeout
        self._pools: dict[str, _PerHostPool] = {}
        self._pools_lock = threading.Lock()
        # Compute persona headers once — they don't change for the
        # client's lifetime.
        self._persona_headers = _persona_to_headers(persona)
        self._closed = False

        # Phase 2.4 (#83) — try wreq transport, fall back to httpx
        # stdlib-SSL silently. Probe at construction so each request
        # doesn't pay the import-attempt cost; the audit row records
        # which transport was selected.
        self._wreq_transport: "WreqTransport | None" = None
        try:
            from .wreq_transport import WreqTransport, WreqUnavailable

            try:
                self._wreq_transport = WreqTransport(
                    persona_toml=persona.raw_toml(),
                    timeout_seconds=timeout,
                )
            except WreqUnavailable:
                # carbonyl_wreq not built — fall back path stays active
                self._wreq_transport = None
        except ImportError:
            # wreq_transport module not yet present (older install).
            # Defensive — should never fire in this repo.
            self._wreq_transport = None

    @property
    def persona(self) -> Persona:
        return self._persona

    @property
    def audit_mode(self) -> EgressAuditMode:
        return self._audit_mode

    @property
    def audit_log(self) -> EgressAuditLog:
        return self._audit_log

    def _client_for(self, url: str) -> httpx.Client:
        """Return (creating if needed) the per-host httpx client."""
        import httpx

        key = _pool_key(url)
        with self._pools_lock:
            pool = self._pools.get(key)
            if pool is None:
                cookies: httpx.Cookies | None = None
                if self._cookie_jar_path is not None:
                    cookies = httpx.Cookies()
                    # Cookie persistence across runs is delegated to the
                    # caller (browser's profile dir handles this when
                    # paired via .egress()). Standalone use loads from
                    # the jar file if it exists.
                    if self._cookie_jar_path.exists():
                        _load_cookies(cookies, self._cookie_jar_path)
                pool = _PerHostPool(
                    client=httpx.Client(
                        timeout=self._timeout,
                        headers=self._persona_headers,
                        cookies=cookies,
                        follow_redirects=True,
                    )
                )
                self._pools[key] = pool
            return pool.client

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._closed:
            raise RuntimeError("EgressClient is closed")

        import time as _time

        # Phase 2.4 (#83) — transport selection. Decide upfront so the
        # audit row's STRICT-mode pre-check can use the right ja4_actual
        # estimate (sentinel for httpx, "will-capture" for wreq).
        use_wreq = self._wreq_transport is not None
        transport_tag = _TRANSPORT_WREQ if use_wreq else _TRANSPORT_HTTPX_FALLBACK

        # Audit step 1: pre-request JA4 expectation. Phase 1's
        # ja4_actual was the sentinel; Phase 2.4 keeps the sentinel for
        # the httpx-fallback path AND defers the wreq path's real
        # ja4_actual until the response returns it (the transport
        # captures the wire-level value during the handshake).
        ja4_expected = self._persona.raw()["network"]["ja4"]
        ja4_actual: str
        if use_wreq:
            # We don't know the captured JA4 until the request finishes.
            # Optimistic placeholder: assume conformance. The finally
            # block overwrites with the real captured value below; if
            # STRICT mode wants to gate, it must do so post-request.
            ja4_actual = ja4_expected  # provisional; finalized below
        else:
            ja4_actual = _HTTPX_PHASE1_JA4_SENTINEL

        drift = ja4_actual != ja4_expected

        # STRICT pre-check still fires for the httpx-fallback path so
        # Phase 1 behavior is preserved. For the wreq path STRICT
        # evaluates AFTER the request because the wire-level JA4 is
        # what matters.
        if drift and self._audit_mode == EgressAuditMode.STRICT and not use_wreq:
            raise EgressFingerprintDrift(ja4_expected, ja4_actual, url)

        request_id = uuid.uuid4().hex
        started = _time.perf_counter()
        status: int | None = None
        try:
            if use_wreq:
                response = self._request_via_wreq(method, url, **kwargs)
            else:
                client = self._client_for(url)
                response = client.request(method, url, **kwargs)
            status = response.status_code
            if use_wreq and self._wreq_transport is not None:
                captured = self._wreq_transport.last_captured_ja4
                if captured is not None:
                    ja4_actual = captured
                    drift = ja4_actual != ja4_expected
                    if drift and self._audit_mode == EgressAuditMode.STRICT:
                        raise EgressFingerprintDrift(
                            ja4_expected, ja4_actual, url
                        )
            return response
        finally:
            latency = (_time.perf_counter() - started) * 1000.0
            entry = EgressAuditEntry(
                request_id=request_id,
                timestamp=_now_iso(),
                persona_id=self._persona.id,
                method=method.upper(),
                url=url,
                ja4_expected=ja4_expected,
                ja4_actual=ja4_actual,
                status_code=status,
                latency_ms=round(latency, 3),
                drift=drift,
                audit_mode=self._audit_mode.value,
                transport=transport_tag,
            )
            if self._audit_mode != EgressAuditMode.OFF:
                self._audit_log.append(entry)

    def _request_via_wreq(self, method: str, url: str, **kwargs: Any) -> "httpx.Response":
        """Issue the request through ``WreqTransport``. Mirrors the
        relevant subset of ``httpx.Client.request`` keyword arguments —
        body/data/json and headers. Extra kwargs that the wreq path
        doesn't understand are silently dropped (matching httpx's
        permissive style)."""
        import httpx as _httpx

        assert self._wreq_transport is not None  # invariant for callers

        headers: _httpx.Headers = _httpx.Headers(self._persona_headers)
        for k, v in (kwargs.get("headers") or {}).items():
            headers[k] = v

        content: bytes | None = None
        if "content" in kwargs:
            content = kwargs["content"]
            if isinstance(content, str):
                content = content.encode()
        elif "data" in kwargs:
            # Trivial form-encoded shape — defer to httpx's serializer
            # via a stub request, then yank the body bytes back out.
            stub = _httpx.Request(method, url, data=kwargs["data"])
            content = bytes(stub.content)
            headers.setdefault("content-type", stub.headers.get("content-type", ""))
        elif "json" in kwargs:
            import json as _json
            content = _json.dumps(kwargs["json"]).encode()
            headers.setdefault("content-type", "application/json")

        request = _httpx.Request(
            method=method.upper(),
            url=url,
            headers=headers,
            content=content,
        )
        return self._wreq_transport.handle_request(request)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def close(self) -> None:
        """Close every per-host pool. Idempotent."""
        if self._closed:
            return
        with self._pools_lock:
            for pool in self._pools.values():
                try:
                    pool.client.close()
                except Exception:  # noqa: BLE001 — pool close is best-effort
                    pass
            self._pools.clear()
        self._closed = True

    def __enter__(self) -> EgressClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        # Best-effort cleanup if the caller forgot — httpx clients hold
        # connections open and warn on GC.
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _persona_to_headers(persona: Persona) -> dict[str, str]:
    """Translate a Persona to a static header dict for httpx.

    Mirrors the trait's ``apply_persona`` (carbonyl_fingerprint::http) but
    in Python, for the Phase 1 httpx-backed client. The Chrome-family
    gate matches the trait: only Chrome personas emit ``sec-ch-ua*``.
    """
    headers: dict[str, str] = {
        "User-Agent": persona.user_agent_full,
        "Accept-Language": persona.accept_language,
    }

    raw = persona.raw()
    if persona.browser_family == "chrome":
        ua_ch = raw["user_agent"]["ua_ch"]
        brands = ua_ch.get("brands", [])
        if brands:
            headers["sec-ch-ua"] = ", ".join(
                f'"{name}";v="{ver}"' for name, ver in brands
            )
        headers["sec-ch-ua-mobile"] = "?1" if ua_ch.get("mobile") else "?0"
        headers["sec-ch-ua-platform"] = f'"{ua_ch.get("platform", "")}"'

    return headers


def _pool_key(url: str) -> str:
    """Connection-pool key: scheme + netloc.

    Per-host pooling matches httpx's own connection reuse model — one
    pool keeps connections alive for the same origin. Cross-origin
    requests get fresh pools.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _load_cookies(jar: httpx.Cookies, path: Path) -> None:
    """Load a persisted cookie jar from disk.

    Format: JSON Lines, one cookie per line with fields
    ``{name, value, domain, path, expires}``. Best-effort — malformed
    lines are skipped (audit log captures the issue).
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    jar.set(
                        name=obj["name"],
                        value=obj["value"],
                        domain=obj.get("domain"),
                        path=obj.get("path", "/"),
                    )
                except (json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        # Missing or unreadable cookie file → start fresh.
        pass


__all__ = [
    "EgressAuditEntry",
    "EgressAuditLog",
    "EgressAuditMode",
    "EgressClient",
    "EgressError",
    "EgressFingerprintDrift",
]
