"""Tests for carbonyl_agent.persona_apply (W3C — #45 partial).

Covers the pure-translator surface:
- Persona load + validation (against the Rust validator via PyO3)
- persona_to_chromium_flags() field-by-field mapping
- CarbonylBrowser persona-typed integration (flag composition + viewport
  default + persona property exposure). Does NOT spawn a browser — flag
  composition is verified directly against the constructed instance.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from carbonyl_agent import (
    CarbonylBrowser,
    Persona,
    PersonaValidationError,
    persona_to_chromium_flags,
)

# --- Reference persona (matches the schema-v2 inline fixture in
# crates/carbonyl-fingerprint/src/validator.rs::tests::VALID_PERSONA_TOML).
# Keep this in sync with the Rust fixture so a divergence between the two
# enforcement layers fails loudly. -----------------------------------------

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


# --- Persona construction ---------------------------------------------------


def test_from_toml_parses_and_validates() -> None:
    p = Persona.from_toml(VALID_TOML)
    assert p.id == "persona-test-valid"
    assert p.browser_family == "chrome"
    assert p.browser_version == "147.0.7727.94"
    assert p.release_channel == "stable"


def test_from_toml_raises_on_validation_failure() -> None:
    # Trip rule 1 by changing the version field without updating the UA —
    # the substring contract breaks.
    bad = VALID_TOML.replace(
        'browser_version = "147.0.7727.94"',
        'browser_version = "146.0.0.0"',
    )
    with pytest.raises(PersonaValidationError) as exc:
        Persona.from_toml(bad)
    assert exc.value.errors, "validator must surface at least one error"
    assert any("browser_version" in e for e in exc.value.errors)


def test_from_toml_skips_validation_when_requested() -> None:
    # Same broken persona — but with validate=False, construction succeeds
    # and the caller takes responsibility.
    bad = VALID_TOML.replace(
        'browser_version = "147.0.7727.94"',
        'browser_version = "146.0.0.0"',
    )
    p = Persona.from_toml(bad, validate=False)
    assert p.browser_version == "146.0.0.0"


def test_from_path_round_trips_a_file(tmp_path: Path) -> None:
    f = tmp_path / "p.toml"
    f.write_text(VALID_TOML)
    p = Persona.from_path(f)
    assert p.id == "persona-test-valid"


def test_persona_eq_and_repr() -> None:
    a = Persona.from_toml(VALID_TOML)
    b = Persona.from_toml(VALID_TOML)
    assert a == b
    assert "persona-test-valid" in repr(a)
    assert "chrome" in repr(a)
    assert "147.0.7727.94" in repr(a)


def test_raw_returns_unwrapped_inner() -> None:
    p = Persona.from_toml(VALID_TOML)
    raw = p.raw()
    # Inner table — `id` is at the top level, not under `["persona"]`.
    assert raw["id"] == "persona-test-valid"
    assert raw["network"]["ja4"] == "t13d1516h2_8daaf6152771_02713d6af862"


# --- Field accessors --------------------------------------------------------


def test_user_agent_full_property() -> None:
    p = Persona.from_toml(VALID_TOML)
    assert p.user_agent_full.startswith("Mozilla/5.0 ")
    assert "Chrome/147.0.7727.94" in p.user_agent_full


def test_locale_properties() -> None:
    p = Persona.from_toml(VALID_TOML)
    assert p.accept_language == "en-US,en;q=0.9"
    assert p.primary_language == "en-US"
    assert p.timezone == "America/New_York"


def test_device_properties() -> None:
    p = Persona.from_toml(VALID_TOML)
    assert p.device_pixel_ratio == 1.0
    assert p.screen_width == 1920
    assert p.screen_height == 1080
    assert p.viewport == (1920, 1080)


# --- persona_to_chromium_flags ---------------------------------------------


def test_flags_emit_user_agent() -> None:
    p = Persona.from_toml(VALID_TOML)
    flags = persona_to_chromium_flags(p)
    ua_flag = next((f for f in flags if f.startswith("--user-agent=")), None)
    assert ua_flag is not None
    assert ua_flag == f"--user-agent={p.user_agent_full}"


def test_flags_emit_lang_and_accept_lang() -> None:
    p = Persona.from_toml(VALID_TOML)
    flags = persona_to_chromium_flags(p)
    assert "--lang=en-US" in flags
    assert "--accept-lang=en-US,en;q=0.9" in flags


def test_flags_omit_dpr_when_unity() -> None:
    p = Persona.from_toml(VALID_TOML)
    # Fixture has device_pixel_ratio = 1.0 → flag must not be emitted
    # (it would be a no-op against Chromium's own default).
    flags = persona_to_chromium_flags(p)
    assert not any(f.startswith("--force-device-scale-factor=") for f in flags)


def test_flags_emit_dpr_when_non_unity() -> None:
    raised = VALID_TOML.replace(
        "device_pixel_ratio = 1.0",
        "device_pixel_ratio = 2.0",
    )
    p = Persona.from_toml(raised)
    flags = persona_to_chromium_flags(p)
    assert "--force-device-scale-factor=2.0" in flags


def test_flags_returned_in_stable_order() -> None:
    p = Persona.from_toml(VALID_TOML)
    flags = persona_to_chromium_flags(p)
    # UA first, then --lang, then --accept-lang. Stable ordering keeps
    # diffs in test fixtures and command-line snapshots predictable.
    assert flags[0].startswith("--user-agent=")
    assert flags[1].startswith("--lang=")
    assert flags[2].startswith("--accept-lang=")


# --- CarbonylBrowser integration -------------------------------------------


def test_browser_accepts_typed_persona() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p)
    assert b.persona is p
    # The string-form persona property continues to expose the id —
    # ProfileManager wiring hasn't changed.
    assert b._persona == "persona-test-valid"


def test_browser_persona_string_does_not_populate_typed_property() -> None:
    b = CarbonylBrowser(persona="just-a-name")
    assert b.persona is None
    assert b._persona == "just-a-name"


def test_browser_no_persona_leaves_typed_property_none() -> None:
    b = CarbonylBrowser()
    assert b.persona is None


def test_browser_appends_persona_flags_after_base() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p)
    flags = b._flags
    # Base flags appear first.
    base_idx = flags.index("--no-first-run")
    # Persona UA appears after base.
    persona_ua = f"--user-agent={p.user_agent_full}"
    assert persona_ua in flags
    assert flags.index(persona_ua) > base_idx


def test_browser_persona_flags_override_base_user_agent() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p)
    # The base set's static Firefox UA comes first; the persona's Chrome
    # UA comes later. Chromium honors the last --user-agent on the
    # command line, so the persona wins.
    ua_flags = [f for f in b._flags if f.startswith("--user-agent=")]
    assert len(ua_flags) == 2
    assert "Firefox/122.0" in ua_flags[0]  # base
    assert "Chrome/147.0.7727.94" in ua_flags[1]  # persona — last wins


def test_browser_extra_flags_override_persona_flags() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(
        persona=p,
        extra_flags=["--user-agent=CustomAgent/1.0"],
    )
    ua_flags = [f for f in b._flags if f.startswith("--user-agent=")]
    # base (Firefox), persona (Chrome), extra (CustomAgent) — last wins.
    assert ua_flags[-1] == "--user-agent=CustomAgent/1.0"


def test_browser_viewport_defaults_to_persona_screen_dims() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p)
    assert b._viewport == (1920, 1080)


def test_browser_explicit_viewport_overrides_persona_screen_dims() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p, viewport=(1280, 800))
    assert b._viewport == (1280, 800)


def test_browser_typed_persona_and_session_are_mutually_exclusive() -> None:
    p = Persona.from_toml(VALID_TOML)
    with pytest.raises(ValueError, match="mutually exclusive"):
        CarbonylBrowser(persona=p, session="legacy-session")


def test_browser_base_flags_replaces_default_set() -> None:
    p = Persona.from_toml(VALID_TOML)
    b = CarbonylBrowser(persona=p, base_flags=["--no-sandbox"])
    # base_flags wipes DEFAULT_HEADLESS_FLAGS; persona flags still append.
    assert b._flags[0] == "--no-sandbox"
    assert any(f.startswith("--user-agent=") for f in b._flags)
    # The Firefox spoof from ANTI_BOT_FLAGS must NOT be present —
    # base_flags excluded it.
    assert not any("Firefox/122.0" in f for f in b._flags)


def test_persona_flags_match_expected_pattern_against_real_template() -> None:
    """Sanity check: regex the persona UA out of the emitted flag and confirm
    it matches the template's own UA. Catches any future regression where
    the translator silently mangles characters in the UA string."""
    p = Persona.from_toml(VALID_TOML)
    flags = persona_to_chromium_flags(p)
    ua_flag = next(f for f in flags if f.startswith("--user-agent="))
    m = re.match(r"^--user-agent=(.+)$", ua_flag)
    assert m is not None
    assert m.group(1) == p.user_agent_full
