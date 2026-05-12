"""Tests for carbonyl_agent.egress (W3B #44 Phase 1).

Covers:
- EgressAuditMode env resolution
- EgressAuditEntry serialization
- EgressAuditLog append + path resolution
- _persona_to_headers — UA, Accept-Language, per-family sec-ch-ua gating
- EgressClient request flow + audit emission (mocked transport — no
  network)
- EgressClient STRICT vs WARN vs OFF behavior on drift
- CarbonylBrowser.egress() helper wiring + cookie-jar path resolution
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip the whole file if httpx isn't available (egress optional dep).
pytest.importorskip("httpx", reason="egress tests require [egress] optional dep")
# Skip if the PyO3 extension isn't built (persona validation requires it).
pytest.importorskip(
    "carbonyl_fingerprint",
    reason="egress tests use Persona which validates via the carbonyl_fingerprint extension",
)

import httpx  # noqa: E402

from carbonyl_agent import (  # noqa: E402
    CarbonylBrowser,
    EgressAuditEntry,
    EgressAuditLog,
    EgressAuditMode,
    EgressClient,
    EgressFingerprintDrift,
    Persona,
)
from carbonyl_agent.egress import _persona_to_headers  # noqa: E402

# Use the same canonical persona as test_persona_apply for cross-test
# consistency. Trimmed to the minimum egress needs to read.
VALID_TOML = """\
[persona]
id = "persona-test-valid"
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
brands = [["Chromium", "147"], ["Not_A Brand", "8"], ["Google Chrome", "147"]]
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
color_depth = 24
device_pixel_ratio = 1.0
hardware_concurrency = 8
device_memory = 8
max_touch_points = 0

[persona.webgl]
vendor = "Google Inc. (Intel)"
renderer = "ANGLE (Intel, Mesa Intel(R) UHD Graphics, OpenGL 4.6)"
vendor_unmasked = "Intel Inc."
renderer_unmasked = "Intel(R) UHD Graphics"

[persona.canvas]
noise_seed = 2449990554173590707

[persona.audio]
noise_seed = 524190668593274066

[persona.fonts]
available = ["Arial", "DejaVu Sans"]

[persona.network]
ja4 = "t13d1516h2_8daaf6152771_02713d6af862"
ja4h_template = "po11nn12enus"
http2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
http3_enabled = false

[persona.behavior]
typing_persona = "normal"
mouse_persona = "desk_mouse_windmouse"

[persona.profile]
user_data_dir = "/tmp/persona-test-valid"
"""


@pytest.fixture
def chrome_persona() -> Persona:
    return Persona.from_toml(VALID_TOML)


@pytest.fixture
def firefox_persona() -> Persona:
    toml = (
        VALID_TOML.replace('browser_family = "chrome"', 'browser_family = "firefox"')
        .replace('browser_version = "147.0.7727.94"', 'browser_version = "150.0"')
        .replace(
            "Chrome/147.0.7727.94 Safari/537.36",
            "Firefox/150.0",
        )
        .replace(
            'brands = [["Chromium", "147"], ["Not_A Brand", "8"], ["Google Chrome", "147"]]',
            "brands = []",
        )
    )
    return Persona.from_toml(toml, validate=False)  # skip validator on the mutated form


# --- EgressAuditMode ------------------------------------------------------


def test_audit_mode_from_env_defaults_to_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARBONYL_FP_AUDIT", raising=False)
    assert EgressAuditMode.from_env() == EgressAuditMode.WARN


def test_audit_mode_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT", "strict")
    assert EgressAuditMode.from_env() == EgressAuditMode.STRICT
    monkeypatch.setenv("CARBONYL_FP_AUDIT", "OFF")
    assert EgressAuditMode.from_env() == EgressAuditMode.OFF


def test_audit_mode_from_env_explicit_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARBONYL_FP_AUDIT", raising=False)
    assert EgressAuditMode.from_env(EgressAuditMode.OFF) == EgressAuditMode.OFF


def test_audit_mode_from_env_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT", "bogus")
    with pytest.raises(ValueError, match="CARBONYL_FP_AUDIT"):
        EgressAuditMode.from_env()


# --- EgressAuditEntry serialization ---------------------------------------


def test_audit_entry_serializes_to_compact_json() -> None:
    e = EgressAuditEntry(
        request_id="abc123",
        timestamp="2026-05-12T00:00:00+00:00",
        persona_id="persona-test-valid",
        method="GET",
        url="https://example.com/api",
        ja4_expected="t13d1516h2_8daaf6152771_02713d6af862",
        ja4_actual="phase1-httpx-stdlib-ssl",
        status_code=200,
        latency_ms=12.5,
        drift=True,
        audit_mode="warn",
    )
    parsed = json.loads(e.to_json())
    assert parsed["request_id"] == "abc123"
    assert parsed["drift"] is True
    assert parsed["status_code"] == 200


# --- EgressAuditLog -------------------------------------------------------


def test_audit_log_creates_parent_dir_and_appends(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "audit.log"
    log = EgressAuditLog(log_path)
    e = EgressAuditEntry(
        request_id="abc",
        timestamp="2026-05-12T00:00:00+00:00",
        persona_id="p1",
        method="GET",
        url="https://example.com",
        ja4_expected="x",
        ja4_actual="y",
        status_code=200,
        latency_ms=1.0,
        drift=True,
        audit_mode="warn",
    )
    log.append(e)
    log.append(e)
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["request_id"] == "abc"


def test_audit_log_default_path_under_xdg_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    from carbonyl_agent.egress import _default_audit_log_path

    assert _default_audit_log_path() == tmp_path / "carbonyl-agent" / "egress-audit.log"


# --- _persona_to_headers --------------------------------------------------


def test_persona_to_headers_chrome_emits_full_sec_ch_ua(chrome_persona: Persona) -> None:
    h = _persona_to_headers(chrome_persona)
    assert h["User-Agent"] == chrome_persona.user_agent_full
    assert h["Accept-Language"] == chrome_persona.accept_language
    assert h["sec-ch-ua-mobile"] == "?0"
    assert h["sec-ch-ua-platform"] == '"Linux"'
    assert "Chromium" in h["sec-ch-ua"]
    assert "Google Chrome" in h["sec-ch-ua"]


def test_persona_to_headers_firefox_excludes_sec_ch_ua(firefox_persona: Persona) -> None:
    h = _persona_to_headers(firefox_persona)
    assert h["User-Agent"] == firefox_persona.user_agent_full
    assert h["Accept-Language"] == firefox_persona.accept_language
    # Firefox must not advertise UA-CH.
    for key in h:
        assert not key.startswith("sec-ch-ua"), (
            f"Firefox persona emitted forbidden UA-CH header: {key}"
        )


# --- EgressClient request flow (mocked transport) -------------------------


def _make_client_with_mock_transport(
    persona: Persona,
    audit_log_path: Path,
    audit_mode: EgressAuditMode,
    handler: callable,
) -> EgressClient:
    """Construct an EgressClient and replace its httpx pools with a
    MockTransport-backed client. Bypasses the per-host pool factory for
    test determinism — every request goes through the mock."""
    log = EgressAuditLog(audit_log_path)
    client = EgressClient(persona, audit_mode=audit_mode, audit_log=log)

    transport = httpx.MockTransport(handler)
    mock_client = httpx.Client(
        transport=transport,
        headers=client._persona_headers,
        follow_redirects=True,
    )

    # Replace the pool factory so every URL routes to the mock.
    from carbonyl_agent.egress import _PerHostPool

    def fake_client_for(self: EgressClient, url: str) -> httpx.Client:  # noqa: ARG001
        return mock_client

    client._client_for = fake_client_for.__get__(client, EgressClient)  # type: ignore[method-assign]
    client._pools["mock"] = _PerHostPool(client=mock_client)
    return client


def test_client_get_records_audit_entry_with_drift(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.log"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _make_client_with_mock_transport(
        chrome_persona, log_path, EgressAuditMode.WARN, handler
    )
    r = client.get("https://example.com/api")
    assert r.status_code == 200

    # Audit log got one drift entry (Phase 1 always drifts — sentinel JA4
    # never matches the persona's JA4).
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert len(entries) == 1
    e = entries[0]
    assert e["persona_id"] == "persona-test-valid"
    assert e["method"] == "GET"
    assert e["url"] == "https://example.com/api"
    assert e["status_code"] == 200
    assert e["drift"] is True
    assert e["ja4_expected"] == chrome_persona.raw()["network"]["ja4"]
    assert e["ja4_actual"] == "phase1-httpx-stdlib-ssl"
    assert e["audit_mode"] == "warn"


def test_client_strict_mode_raises_on_drift(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.log"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = _make_client_with_mock_transport(
        chrome_persona, log_path, EgressAuditMode.STRICT, handler
    )
    with pytest.raises(EgressFingerprintDrift) as exc:
        client.get("https://example.com/")
    assert exc.value.url == "https://example.com/"
    assert exc.value.expected == chrome_persona.raw()["network"]["ja4"]


def test_client_off_mode_skips_audit_log(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.log"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = _make_client_with_mock_transport(
        chrome_persona, log_path, EgressAuditMode.OFF, handler
    )
    r = client.get("https://example.com/")
    assert r.status_code == 200
    assert not log_path.exists(), "OFF mode must not write the audit log"


def test_client_sends_persona_headers(chrome_persona: Persona, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured.update(dict(req.headers))
        return httpx.Response(200)

    client = _make_client_with_mock_transport(
        chrome_persona, tmp_path / "a.log", EgressAuditMode.OFF, handler
    )
    client.get("https://example.com/")

    # User-Agent and Accept-Language always.
    assert captured["user-agent"] == chrome_persona.user_agent_full
    assert captured["accept-language"] == chrome_persona.accept_language
    # Chrome family: sec-ch-ua-mobile/platform present.
    assert captured.get("sec-ch-ua-mobile") == "?0"
    assert captured.get("sec-ch-ua-platform") == '"Linux"'


def test_client_post_records_method_correctly(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.log"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(201)

    client = _make_client_with_mock_transport(
        chrome_persona, log_path, EgressAuditMode.WARN, handler
    )
    client.post("https://example.com/", json={"k": "v"})
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    assert entries[0]["method"] == "POST"
    assert entries[0]["status_code"] == 201


def test_client_context_manager_closes_pools(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log = EgressAuditLog(tmp_path / "audit.log")
    with EgressClient(chrome_persona, audit_mode=EgressAuditMode.OFF, audit_log=log) as c:
        assert not c._closed
    assert c._closed


def test_client_closed_then_request_raises(
    chrome_persona: Persona, tmp_path: Path
) -> None:
    log = EgressAuditLog(tmp_path / "audit.log")
    c = EgressClient(chrome_persona, audit_mode=EgressAuditMode.OFF, audit_log=log)
    c.close()
    with pytest.raises(RuntimeError, match="closed"):
        c.get("https://example.com/")


# --- CarbonylBrowser.egress() ---------------------------------------------


def test_browser_egress_requires_typed_persona() -> None:
    b = CarbonylBrowser()  # no persona
    with pytest.raises(RuntimeError, match="typed Persona"):
        b.egress()


def test_browser_egress_string_persona_does_not_qualify() -> None:
    # String-form persona is just a profile name, not a typed Persona.
    # egress() must reject it explicitly.
    b = CarbonylBrowser(persona="some-profile-name")
    with pytest.raises(RuntimeError, match="typed Persona"):
        b.egress()


def test_browser_egress_returns_persona_bound_client(chrome_persona: Persona) -> None:
    b = CarbonylBrowser(persona=chrome_persona)
    c = b.egress(audit_mode=EgressAuditMode.OFF)
    assert isinstance(c, EgressClient)
    assert c.persona is chrome_persona
    assert c.audit_mode == EgressAuditMode.OFF
    c.close()


def test_browser_egress_forwards_kwargs(chrome_persona: Persona, tmp_path: Path) -> None:
    b = CarbonylBrowser(persona=chrome_persona)
    custom_log = EgressAuditLog(tmp_path / "custom-audit.log")
    c = b.egress(audit_mode=EgressAuditMode.WARN, audit_log=custom_log, timeout=5.0)
    assert c.audit_log is custom_log
    c.close()
