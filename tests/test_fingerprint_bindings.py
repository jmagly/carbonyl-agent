"""PyO3 binding smoke tests for the carbonyl-fingerprint Rust crate.

These tests require the extension module to be built and installed:

    maturin develop --manifest-path crates/carbonyl-fingerprint/Cargo.toml --features python

If the module isn't installed, every test in this file is skipped — keeping
``pytest`` green for contributors who don't need the Rust toolchain.

Refs: roctinam/carbonyl-agent#43 (W3A.4)
"""
from __future__ import annotations

import pytest

cf = pytest.importorskip(
    "carbonyl_fingerprint",
    reason=(
        "carbonyl_fingerprint extension not built; run "
        "`maturin develop --manifest-path crates/carbonyl-fingerprint/Cargo.toml "
        "--features python` to enable these tests"
    ),
)


# Canonical valid persona — derived from the SCHEMA.md exemplar in the
# carbonyl-fingerprint-corpus repo. Mirrors the Rust-side fixture in
# crates/carbonyl-fingerprint/src/validator.rs so a round-trip Python
# call exercises the same hard rules.
VALID_PERSONA_TOML = """
[persona]
id = "persona-test-valid"
generator_version = "2026.04.18"
chrome_version = "147.0.7727.94"
chrome_channel = "stable"

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


def test_module_exposes_version_string() -> None:
    """Sanity check: extension built and `__version__` is populated from CARGO_PKG_VERSION."""
    assert isinstance(cf.__version__, str)
    assert cf.__version__  # non-empty


def test_validate_toml_returns_ok_for_canonical_persona() -> None:
    ok, errors = cf.validate_toml(VALID_PERSONA_TOML)
    assert ok is True, f"valid persona reported errors: {errors}"
    assert errors == []


def test_is_valid_toml_returns_true_for_canonical_persona() -> None:
    assert cf.is_valid_toml(VALID_PERSONA_TOML) is True


def test_validate_toml_reports_errors_when_ja4_mutated() -> None:
    bad = VALID_PERSONA_TOML.replace(
        "t13d1516h2_8daaf6152771_02713d6af862",
        "t13d1516h2_DEADBEEF_DEADBEEF",
    )
    ok, errors = cf.validate_toml(bad)
    assert ok is False
    # At least one error should mention JA4. Don't pin exact wording —
    # validator.rs Display impl owns that.
    assert any("ja4" in e.lower() for e in errors), errors


def test_is_valid_toml_returns_false_when_ja4_mutated() -> None:
    bad = VALID_PERSONA_TOML.replace(
        "t13d1516h2_8daaf6152771_02713d6af862",
        "t13d1516h2_DEADBEEF_DEADBEEF",
    )
    assert cf.is_valid_toml(bad) is False


def test_validate_toml_raises_on_malformed_input() -> None:
    with pytest.raises(ValueError, match="persona TOML parse error"):
        cf.validate_toml("not [valid] toml at all = =")


def test_validate_toml_collects_multiple_violations() -> None:
    """Trip rules 1, 2, AND 4 simultaneously; expect ≥3 errors back."""
    bad = (
        VALID_PERSONA_TOML
        # Rule 1: UA no longer contains chrome_version
        .replace("Chrome/147.0.7727.94", "Chrome/146.0.0.0")
        # Rule 2: brand major drifts
        .replace('["Google Chrome", "147"]', '["Google Chrome", "146"]')
        # Rule 4: JA4 mutates
        .replace(
            "t13d1516h2_8daaf6152771_02713d6af862",
            "t13d1516h2_BAD_BAD",
        )
    )
    ok, errors = cf.validate_toml(bad)
    assert ok is False
    assert len(errors) >= 3, errors
