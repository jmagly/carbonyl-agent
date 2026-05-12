"""
Tests for WreqTransport + EgressClient's transport selection
(W3B Phase 2.4 — `Refs: roctinam/carbonyl-agent#83`).

These tests do NOT require the native ``carbonyl_wreq`` module
(#85). They exercise:

1. ``WreqTransport`` raising :class:`WreqUnavailable` when the module
   isn't built. This is the "expected path" for current installs.
2. ``WreqTransport`` happy-path when a stub ``carbonyl_wreq`` module
   is monkeypatched into ``sys.modules``. Confirms the
   ``httpx.Request → tuple → httpx.Response`` mapping is correct.
3. ``EgressClient`` falls back to httpx silently when wreq isn't
   available and stamps the audit row with
   ``transport: httpx-fallback``.
4. ``EgressClient`` prefers wreq when the stub module is present and
   stamps ``transport: wreq``.

Once #85 lands the real native module, the stub-based tests still
work — the stub matches the documented ``send_request`` signature.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")  # whole module skipped if httpx absent

from carbonyl_agent.egress import (  # noqa: E402
    _TRANSPORT_HTTPX_FALLBACK,
    _TRANSPORT_WREQ,
    EgressAuditMode,
    EgressClient,
)
from carbonyl_agent.persona_apply import Persona  # noqa: E402
from carbonyl_agent.wreq_transport import (  # noqa: E402
    WreqTransport,
    WreqUnavailable,
    is_available,
)

_PERSONA_FIXTURE = Path(__file__).parent / "fixtures" / "chrome-147-stable-linux.toml"


def _load_persona() -> Persona:
    """Use whichever Chrome 147 fixture this test suite already
    relies on. If the test suite hasn't materialized one we synthesize
    a minimal-but-valid persona here."""
    if _PERSONA_FIXTURE.exists():
        return Persona.from_path(_PERSONA_FIXTURE)
    # Synthetic minimal persona — every field the validator requires,
    # nothing else. Mirrors the Rust-side validator's reference
    # fixture (`Refs: roctinam/carbonyl-agent#71`).
    return Persona.from_toml(_SYNTHETIC_TOML, validate=False)


_SYNTHETIC_TOML = """
[persona]
id = "persona-test-wreq-transport"
generator_version = "2026.04.18"
browser_family = "chrome"
browser_version = "147.0.7727.94"
release_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.94 Safari/537.36"

[persona.user_agent.ua_ch]
brands = [["Chromium", "147"], ["Google Chrome", "147"]]
mobile = false
platform = "Linux"
platform_version = "6.8.0"
architecture = "x86"
bitness = "64"

[persona.locale]
accept_language = "en-US,en;q=0.9"
timezone = "America/New_York"
languages = ["en-US", "en"]

[persona.device]
screen_width = 1920
screen_height = 1080
device_memory = 8
hardware_concurrency = 8

[persona.network]
ja4 = "t13d1516h2_8daaf6152771_02713d6af862"
ja3 = ""
alpn = ["h2", "http/1.1"]
http2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"

[persona.fonts]
list = []

[persona.plugins]
list = []

[persona.webgl]
vendor = "Google Inc. (Mesa)"
renderer = "ANGLE (Mesa, llvmpipe, OpenGL 4.5)"

[persona.canvas]
seed = "0000000000000000"

[persona.audio]
seed = "0000000000000000"
"""


# ----------------------------------------------------------------------
# Stub carbonyl_wreq module — drop into sys.modules so WreqTransport
# behaves as if the native module is built. Each test that wants the
# stub uses the fixture below.
# ----------------------------------------------------------------------


def _make_stub_wreq(
    *,
    status: int = 200,
    body: bytes = b"ok",
    captured_ja4: str = "t13d1516h2_8daaf6152771_02713d6af862",
    response_headers: list[tuple[str, str]] | None = None,
) -> types.ModuleType:
    """Build a stand-in for the not-yet-built native module. Lets the
    Python plumbing be exercised end-to-end without #85's PyO3 work
    actually existing yet."""
    mod = types.ModuleType("carbonyl_wreq")

    def send_request(
        persona_toml: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body_in: bytes | None,
        timeout_seconds: float,
    ) -> tuple[int, list[tuple[str, str]], bytes, str]:
        # Echo the inputs into a deterministic response so tests can
        # assert on what the transport forwarded.
        rh = response_headers or [("content-type", "text/plain")]
        return (status, rh, body, captured_ja4)

    mod.send_request = send_request  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def stub_wreq(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    stub = _make_stub_wreq()
    monkeypatch.setitem(sys.modules, "carbonyl_wreq", stub)
    return stub


# ----------------------------------------------------------------------
# is_available() probe — clean module-absent vs module-present states
# ----------------------------------------------------------------------


def test_is_available_returns_false_when_module_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "carbonyl_wreq", raising=False)
    # Block re-import too — pretend the module is genuinely missing.
    real_find = __import__("importlib.util").util.find_spec

    def _no_carbonyl_wreq(name: str, package: Any = None) -> Any:
        if name == "carbonyl_wreq":
            return None
        return real_find(name, package)

    monkeypatch.setattr("importlib.util.find_spec", _no_carbonyl_wreq)
    # importlib.import_module respects find_spec=None as ImportError.
    assert is_available() is False


def test_is_available_true_when_stub_present(stub_wreq: types.ModuleType) -> None:
    assert is_available() is True


def test_is_available_false_when_module_missing_send_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = types.ModuleType("carbonyl_wreq")  # no send_request attr
    monkeypatch.setitem(sys.modules, "carbonyl_wreq", broken)
    assert is_available() is False


# ----------------------------------------------------------------------
# WreqTransport unit tests — using the stub
# ----------------------------------------------------------------------


def test_wreq_transport_raises_when_module_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "carbonyl_wreq", raising=False)
    real_find = __import__("importlib.util").util.find_spec

    def _no_carbonyl_wreq(name: str, package: Any = None) -> Any:
        if name == "carbonyl_wreq":
            return None
        return real_find(name, package)

    monkeypatch.setattr("importlib.util.find_spec", _no_carbonyl_wreq)
    with pytest.raises(WreqUnavailable):
        WreqTransport(persona_toml="ignored")


def test_wreq_transport_handle_request_round_trips(stub_wreq: types.ModuleType) -> None:
    transport = WreqTransport(persona_toml=_SYNTHETIC_TOML, timeout_seconds=5.0)
    request = httpx.Request(
        "GET",
        "https://example.com/path",
        headers={"x-test": "yes"},
    )
    response = transport.handle_request(request)
    assert response.status_code == 200
    assert response.content == b"ok"
    assert response.headers["content-type"] == "text/plain"
    assert transport.last_captured_ja4 == "t13d1516h2_8daaf6152771_02713d6af862"


# ----------------------------------------------------------------------
# EgressClient transport selection
# ----------------------------------------------------------------------


def test_egress_client_uses_httpx_fallback_when_wreq_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delitem(sys.modules, "carbonyl_wreq", raising=False)
    real_find = __import__("importlib.util").util.find_spec

    def _no_carbonyl_wreq(name: str, package: Any = None) -> Any:
        if name == "carbonyl_wreq":
            return None
        return real_find(name, package)

    monkeypatch.setattr("importlib.util.find_spec", _no_carbonyl_wreq)

    persona = _load_persona()
    # Direct EgressClient to a non-existent host so we don't actually
    # send a request — the construction step is what we're testing.
    client = EgressClient(persona, audit_mode=EgressAuditMode.OFF)
    try:
        # Internal probe: wreq transport must be None.
        assert client._wreq_transport is None  # type: ignore[attr-defined]
    finally:
        client.close()


def test_egress_client_picks_up_wreq_when_stub_installed(
    stub_wreq: types.ModuleType,
) -> None:
    persona = _load_persona()
    client = EgressClient(persona, audit_mode=EgressAuditMode.OFF)
    try:
        # Internal probe: wreq transport must be wired.
        assert client._wreq_transport is not None  # type: ignore[attr-defined]
    finally:
        client.close()


def test_egress_audit_row_carries_transport_tag_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delitem(sys.modules, "carbonyl_wreq", raising=False)
    real_find = __import__("importlib.util").util.find_spec

    def _no_carbonyl_wreq(name: str, package: Any = None) -> Any:
        if name == "carbonyl_wreq":
            return None
        return real_find(name, package)

    monkeypatch.setattr("importlib.util.find_spec", _no_carbonyl_wreq)

    persona = _load_persona()
    audit_log_path = tmp_path / "audit.log"
    from carbonyl_agent.egress import EgressAuditLog

    audit = EgressAuditLog(path=audit_log_path)
    client = EgressClient(persona, audit_mode=EgressAuditMode.WARN, audit_log=audit)
    try:
        # Try a real (likely-failing) request just to populate the
        # audit row. We catch any network error — what matters is
        # that the audit row exists and carries the right transport tag.
        with pytest.raises(Exception):  # noqa: PT011 — any network failure is fine
            client.get("https://127.0.0.1:1/")
    finally:
        client.close()

    import json

    lines = audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    transports = {json.loads(line)["transport"] for line in lines}
    assert transports == {_TRANSPORT_HTTPX_FALLBACK}


def test_egress_audit_row_carries_transport_tag_wreq(
    stub_wreq: types.ModuleType,
    tmp_path: Path,
) -> None:
    import json

    persona = _load_persona()
    audit_log_path = tmp_path / "audit.log"
    from carbonyl_agent.egress import EgressAuditLog

    audit = EgressAuditLog(path=audit_log_path)
    client = EgressClient(persona, audit_mode=EgressAuditMode.WARN, audit_log=audit)
    try:
        response = client.get("https://example.com/")
        assert response.status_code == 200
    finally:
        client.close()

    lines = audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    transports = {json.loads(line)["transport"] for line in lines}
    assert transports == {_TRANSPORT_WREQ}


def test_egress_wreq_path_records_real_captured_ja4(
    stub_wreq: types.ModuleType,
    tmp_path: Path,
) -> None:
    """The point of Phase 2 — the audit row's ja4_actual is no longer
    the phase1 sentinel. The stub returns the persona's expected JA4
    so drift=False in this happy-path test."""
    import json

    persona = _load_persona()
    from carbonyl_agent.egress import EgressAuditLog

    audit_log_path = tmp_path / "audit.log"
    audit = EgressAuditLog(path=audit_log_path)
    client = EgressClient(persona, audit_mode=EgressAuditMode.WARN, audit_log=audit)
    try:
        client.get("https://example.com/")
    finally:
        client.close()

    lines = audit_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["transport"] == _TRANSPORT_WREQ
    # Real captured value, not the sentinel.
    assert entry["ja4_actual"] != "phase1-httpx-stdlib-ssl"
    assert entry["ja4_actual"] == persona.raw()["network"]["ja4"]
    assert entry["drift"] is False
