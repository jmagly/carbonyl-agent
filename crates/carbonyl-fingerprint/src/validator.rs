//! Persona consistency validator (W3A.3 — `Refs: roctinam/carbonyl-agent#43`).
//!
//! Implements the "hard rules" from the corpus `SCHEMA.md` — fields that
//! must agree internally for a persona to be plausible. A passing
//! `validate()` is necessary but not sufficient for trust: the sampler
//! (W3A.2) is responsible for joint-distribution plausibility across
//! fields the validator cannot cross-check without corpus data.
//!
//! # API
//!
//! ```ignore
//! use carbonyl_fingerprint::{Persona, validator};
//!
//! let persona: Persona = toml::from_str(persona_toml).unwrap();
//! match validator::validate(&persona) {
//!     Ok(()) => { /* persona is internally consistent */ }
//!     Err(report) => {
//!         for err in report.errors() {
//!             eprintln!("{err}");
//!         }
//!     }
//! }
//! ```
//!
//! `validate()` collects *all* violations rather than stopping at the
//! first; sampler/CI consumers want a complete report.
//!
//! # Coverage (v1)
//!
//! All hard rules from the SCHEMA.md table are enforced *except*
//! deterministic noise-seed derivation (TODO; see entry point):
//!
//! 1. `user_agent.full` contains `browser_version` as a substring
//!    (browser-agnostic — every family embeds its own version in the UA)
//! 2. `ua_ch.brands` contains `["Google Chrome", "<major>"]` matching
//!    `browser_version`'s Chrome major (Chrome-only — gated on
//!    `browser_family == Chrome`; Firefox/Safari don't send UA-CH)
//! 3. `network.ja4` matches the canonical JA4 for the persona's Chrome
//!    major (Chrome-only — per-family JA4 reference tables land with
//!    each family's W3A.6 follow-up PR)
//! 4. `device.hardware_concurrency` ≤ 8 AND `device.device_memory` ≤ 8
//! 5. `platform.os_family` == `user_agent.ua_ch.platform`
//! 6. Linux personas MUST NOT advertise macOS-only fonts
//! 7. Linux personas MUST NOT advertise Windows-only fonts
//! 8. Linux personas MUST NOT advertise an ANGLE/DirectX WebGL renderer
//! 9. `network.http2_akamai` matches the canonical Chrome H2 fingerprint
//! 10. `locale.timezone` is plausible for `accept_language` (en-US, en-GB)

use crate::registry::ChromeRegistry;
use crate::schema::{BrowserFamily, Persona};
use crate::seed;
use thiserror::Error;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// One violation surfaced by `validate()`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ValidationError {
    #[error(
        "user_agent.full does not contain browser_version `{browser_version}` \
         (got `{user_agent_full}`)"
    )]
    UserAgentMissingBrowserVersion {
        browser_version: String,
        user_agent_full: String,
    },

    #[error(
        "browser_version `{0}` is not a valid Chrome version (expected \
         `MAJOR.MINOR.BUILD.PATCH` with numeric major)"
    )]
    ChromeVersionMalformed(String),

    #[error(
        "ua_ch.brands is missing a `[\"Google Chrome\", \"{expected_major}\"]` \
         entry matching browser_version major (got brands: {actual:?})"
    )]
    UaChGoogleChromeBrandMismatch {
        expected_major: u32,
        actual: Vec<(String, String)>,
    },

    #[error(
        "network.ja4 `{actual}` does not match canonical Chrome {major} JA4 \
         `{expected}` (corpus reference)"
    )]
    Ja4Mismatch {
        major: u32,
        expected: String,
        actual: String,
    },

    #[error(
        "no JA4 reference data available for Chrome major `{0}` — extend \
         CHROME_REFERENCES table in validator.rs"
    )]
    UnknownChromeReference(u32),

    #[error(
        "device.{field} = {value} exceeds plausibility bound {max} (SCHEMA.md \
         hardware bounds rule)"
    )]
    HardwareBoundExceeded {
        field: &'static str,
        value: u32,
        max: u32,
    },

    #[error(
        "platform.os_family `{os_family}` does not equal user_agent.ua_ch.platform \
         `{ua_ch_platform}` (SCHEMA.md OS-family equality rule)"
    )]
    PlatformOsMismatch {
        os_family: String,
        ua_ch_platform: String,
    },

    #[error(
        "Linux persona advertises forbidden font `{font}` ({reason}); SCHEMA.md \
         forbids cross-OS font leakage"
    )]
    LinuxForbiddenFont { font: String, reason: &'static str },

    #[error(
        "Linux persona advertises ANGLE/DirectX WebGL renderer `{renderer}` \
         (ANGLE+OpenGL is fine on Linux; ANGLE+DirectX/Direct3D is Windows-only)"
    )]
    LinuxForbiddenWebGlRenderer { renderer: String },

    #[error(
        "network.http2_akamai `{actual}` does not match canonical Chrome {major} \
         H2 fingerprint `{expected}` (corpus reference)"
    )]
    H2AkamaiMismatch {
        major: u32,
        expected: String,
        actual: String,
    },

    #[error(
        "locale.timezone `{timezone}` is implausible for accept_language \
         `{accept_language}` (SCHEMA.md timezone↔locale plausibility rule)"
    )]
    TimezoneLocaleImplausible {
        timezone: String,
        accept_language: String,
    },

    #[error(
        "{kind}.noise_seed for persona `{persona_id}` is non-deterministic: \
         expected {expected} (HKDF-Expand from id) but found {actual} \
         (SCHEMA.md rule H — noise seeds must be derivable from persona id)"
    )]
    NoiseSeedMismatch {
        kind: NoiseSeedKind,
        persona_id: String,
        expected: u64,
        actual: u64,
    },
}

/// Which noise seed failed determinism. Carried in
/// [`ValidationError::NoiseSeedMismatch`] so callers can route fixes
/// (e.g., regenerate the canvas seed only) without parsing the
/// `Display` text.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoiseSeedKind {
    Canvas,
    Audio,
}

impl std::fmt::Display for NoiseSeedKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NoiseSeedKind::Canvas => f.write_str("canvas"),
            NoiseSeedKind::Audio => f.write_str("audio"),
        }
    }
}

/// Aggregate of all violations for a single persona. Empty == valid.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationReport {
    errors: Vec<ValidationError>,
}

impl ValidationReport {
    pub fn errors(&self) -> &[ValidationError] {
        &self.errors
    }

    pub fn is_empty(&self) -> bool {
        self.errors.is_empty()
    }

    pub fn into_errors(self) -> Vec<ValidationError> {
        self.errors
    }
}

impl std::fmt::Display for ValidationReport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(
            f,
            "persona failed validation ({} errors):",
            self.errors.len()
        )?;
        for (i, err) in self.errors.iter().enumerate() {
            writeln!(f, "  {}. {err}", i + 1)?;
        }
        Ok(())
    }
}

impl std::error::Error for ValidationReport {}

// ---------------------------------------------------------------------------
// Reference data lookup
// ---------------------------------------------------------------------------
//
// Reference data lives in [`ChromeRegistry`] (see crate::registry).
// `validate()` uses the inline-default registry (Chrome 148 only — the
// SCHEMA.md exemplar) so it is a no-config drop-in for callers that
// don't have the corpus repo checked out. Callers that want
// multi-major coverage build a registry with
// `ChromeRegistry::from_corpus_dir` and pass it to
// [`validate_with_registry`].

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/// Validate the persona against all v1 hard rules using the inline
/// fallback registry (Chrome 148 only). Drop-in replacement for
/// callers that don't have a corpus directory configured.
///
/// Equivalent to:
/// ```ignore
/// validate_with_registry(&persona, &ChromeRegistry::inline_default())
/// ```
pub fn validate(persona: &Persona) -> Result<(), ValidationReport> {
    let registry = ChromeRegistry::inline_default();
    validate_with_registry(persona, &registry)
}

/// Validate the persona against all v1 hard rules, looking up Chrome
/// reference data (rules 3 / F) in the supplied [`ChromeRegistry`].
/// Use [`ChromeRegistry::from_corpus_dir`] for multi-major coverage.
pub fn validate_with_registry(
    persona: &Persona,
    registry: &ChromeRegistry,
) -> Result<(), ValidationReport> {
    let mut errors: Vec<ValidationError> = Vec::new();

    let p = &persona.persona;

    // Rule 1 (browser-agnostic): user_agent.full must contain
    // browser_version verbatim. Every browser embeds its own version
    // string in the UA — Chrome `Chrome/MAJOR.MINOR.BUILD.PATCH`,
    // Firefox `Firefox/MAJOR.MINOR`, Safari `Version/MAJOR.MINOR`. The
    // substring contract holds across families.
    check_user_agent_contains_browser_version(p, &mut errors);

    // Rules 2/3/F are Chrome-specific:
    //   - UA-CH `Google Chrome` brand
    //   - JA4 reference table (Chrome registry)
    //   - H2 Akamai reference table (Chrome registry)
    // Firefox doesn't send UA-CH; Safari ships a different JA4/H2
    // family. Per-family equivalents land alongside their reference
    // captures in W3A.6 follow-up PRs.
    if p.browser_family == BrowserFamily::Chrome {
        let chrome_major = match parse_chrome_major(&p.browser_version) {
            Some(m) => Some(m),
            None => {
                errors.push(ValidationError::ChromeVersionMalformed(
                    p.browser_version.clone(),
                ));
                None
            }
        };

        if let Some(major) = chrome_major {
            check_ua_ch_brand_matches_major(p, major, &mut errors);
            check_ja4_matches_chrome_reference(p, major, registry, &mut errors);
            check_h2_akamai_matches_chrome_reference(p, major, registry, &mut errors);
        }
    }

    // Rule A: device hardware bounds (hardware_concurrency ≤ 8,
    // device_memory ≤ 8) per SCHEMA.md.
    check_hardware_bounds(p, &mut errors);

    // Rule B: platform.os_family must equal user_agent.ua_ch.platform.
    check_platform_os_family_matches_ua_ch(p, &mut errors);

    // Rule C: Linux personas must not advertise macOS-only fonts.
    check_linux_forbidden_macos_fonts(p, &mut errors);

    // Rule D: Linux personas must not advertise Windows-only fonts.
    check_linux_forbidden_windows_fonts(p, &mut errors);

    // Rule E: Linux personas must not advertise an ANGLE/DirectX WebGL
    // renderer (Windows-only ANGLE backend).
    check_linux_forbidden_webgl_renderer(p, &mut errors);

    // Rule G: locale.timezone ↔ accept_language plausibility. Allowlist
    // covers en-US and en-GB only; other locales pass (out of scope).
    check_timezone_locale_plausibility(p, &mut errors);

    // Rule H: canvas.noise_seed and audio.noise_seed must be
    // deterministically derivable from persona.id. The derivation
    // contract lives in `crate::seed` (HKDF-Expand SHA-256 with a
    // versioned salt and per-purpose info labels).
    check_noise_seeds_deterministic(p, &mut errors);

    if errors.is_empty() {
        Ok(())
    } else {
        Err(ValidationReport { errors })
    }
}

// ---------------------------------------------------------------------------
// Rule implementations
// ---------------------------------------------------------------------------

fn check_user_agent_contains_browser_version(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if !p.user_agent.full.contains(&p.browser_version) {
        errors.push(ValidationError::UserAgentMissingBrowserVersion {
            browser_version: p.browser_version.clone(),
            user_agent_full: p.user_agent.full.clone(),
        });
    }
}

fn check_ua_ch_brand_matches_major(
    p: &crate::schema::PersonaInner,
    expected_major: u32,
    errors: &mut Vec<ValidationError>,
) {
    let expected_str = expected_major.to_string();
    let matched = p
        .user_agent
        .ua_ch
        .brands
        .iter()
        .any(|(name, ver)| name == "Google Chrome" && ver == &expected_str);

    if !matched {
        errors.push(ValidationError::UaChGoogleChromeBrandMismatch {
            expected_major,
            actual: p.user_agent.ua_ch.brands.clone(),
        });
    }
}

fn check_ja4_matches_chrome_reference(
    p: &crate::schema::PersonaInner,
    major: u32,
    registry: &ChromeRegistry,
    errors: &mut Vec<ValidationError>,
) {
    let Some(reference) = registry.lookup(major) else {
        errors.push(ValidationError::UnknownChromeReference(major));
        return;
    };

    if p.network.ja4 != reference.ja4 {
        errors.push(ValidationError::Ja4Mismatch {
            major,
            expected: reference.ja4.clone(),
            actual: p.network.ja4.clone(),
        });
    }
}

fn check_h2_akamai_matches_chrome_reference(
    p: &crate::schema::PersonaInner,
    major: u32,
    registry: &ChromeRegistry,
    errors: &mut Vec<ValidationError>,
) {
    // Don't double-report unknown major — rule 3 already did.
    let Some(reference) = registry.lookup(major) else {
        return;
    };

    if p.network.http2_akamai != reference.h2_akamai {
        errors.push(ValidationError::H2AkamaiMismatch {
            major,
            expected: reference.h2_akamai.clone(),
            actual: p.network.http2_akamai.clone(),
        });
    }
}

/// SCHEMA.md caps `hardware_concurrency` and `device_memory` at 8. Higher
/// values fingerprint as workstation/server-class hardware, which is
/// conspicuous for an automation persona pretending to be a consumer
/// machine. The bound is inclusive.
const MAX_HARDWARE_CONCURRENCY: u32 = 8;
const MAX_DEVICE_MEMORY: u32 = 8;

fn check_hardware_bounds(p: &crate::schema::PersonaInner, errors: &mut Vec<ValidationError>) {
    if p.device.hardware_concurrency > MAX_HARDWARE_CONCURRENCY {
        errors.push(ValidationError::HardwareBoundExceeded {
            field: "hardware_concurrency",
            value: p.device.hardware_concurrency,
            max: MAX_HARDWARE_CONCURRENCY,
        });
    }
    if p.device.device_memory > MAX_DEVICE_MEMORY {
        errors.push(ValidationError::HardwareBoundExceeded {
            field: "device_memory",
            value: p.device.device_memory,
            max: MAX_DEVICE_MEMORY,
        });
    }
}

/// Fonts shipped only with macOS that should never appear on a Linux
/// persona's available list. Sourced from Apple system font inventories.
const MACOS_ONLY_FONTS: &[&str] = &[
    "Helvetica Neue",
    "San Francisco",
    "Lucida Grande",
    "Apple Color Emoji",
    "Menlo",
    "Monaco",
];

fn check_linux_forbidden_macos_fonts(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if !p.platform.os_family.eq_ignore_ascii_case("Linux") {
        return;
    }
    for font in &p.fonts.available {
        if MACOS_ONLY_FONTS
            .iter()
            .any(|f| f.eq_ignore_ascii_case(font))
        {
            errors.push(ValidationError::LinuxForbiddenFont {
                font: font.clone(),
                reason: "macos-only",
            });
        }
    }
}

/// Fonts shipped only with Windows that should never appear on a Linux
/// persona's available list. Sourced from Windows system font inventories.
const WINDOWS_ONLY_FONTS: &[&str] = &[
    "Segoe UI",
    "Calibri",
    "Cambria",
    "Consolas",
    "Tahoma",
    "Trebuchet MS",
    "Microsoft Sans Serif",
    "Verdana",
];

fn check_linux_forbidden_windows_fonts(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if !p.platform.os_family.eq_ignore_ascii_case("Linux") {
        return;
    }
    for font in &p.fonts.available {
        if WINDOWS_ONLY_FONTS
            .iter()
            .any(|f| f.eq_ignore_ascii_case(font))
        {
            errors.push(ValidationError::LinuxForbiddenFont {
                font: font.clone(),
                reason: "windows-only",
            });
        }
    }
}

/// Detect ANGLE-on-DirectX renderer strings. Chrome's ANGLE layer can
/// translate WebGL to multiple backends; on Windows it commonly targets
/// Direct3D 9/11/12, and the renderer string carries that backend's name.
/// On Linux, ANGLE typically targets OpenGL or Vulkan — those are
/// acceptable. Only the Direct3D/DirectX combination is forbidden.
fn is_angle_directx_renderer(renderer: &str) -> bool {
    let lower = renderer.to_ascii_lowercase();
    let has_angle = lower.contains("angle");
    let has_directx =
        lower.contains("directx") || lower.contains("direct3d") || lower.contains("d3d");
    has_angle && has_directx
}

fn check_linux_forbidden_webgl_renderer(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if !p.platform.os_family.eq_ignore_ascii_case("Linux") {
        return;
    }
    if is_angle_directx_renderer(&p.webgl.renderer) {
        errors.push(ValidationError::LinuxForbiddenWebGlRenderer {
            renderer: p.webgl.renderer.clone(),
        });
    }
}

/// Plausible timezones for `accept_language` starting with `en-US`.
/// Drawn from IANA tz database — covers continental US plus Alaska,
/// Hawaii, Arizona (no DST). SCHEMA.md doesn't enumerate the joint
/// distribution; this is a minimal allowlist sufficient to catch the
/// gross mismatches (e.g., en-US + Asia/Tokyo). Other en-* locales,
/// non-English locales, and multi-language Accept-Language headers fall
/// outside scope and pass without check.
const EN_US_TIMEZONES: &[&str] = &[
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
];

const EN_GB_TIMEZONES: &[&str] = &["Europe/London"];

fn check_timezone_locale_plausibility(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    let lang = p.locale.accept_language.as_str();
    let tz = p.locale.timezone.as_str();

    // Match on the leading language tag (before any comma or quality
    // qualifier). e.g. "en-US,en;q=0.9" → "en-US".
    let primary = lang.split([',', ';']).next().unwrap_or("").trim();

    let allowed: Option<&[&str]> = if primary.eq_ignore_ascii_case("en-US") {
        Some(EN_US_TIMEZONES)
    } else if primary.eq_ignore_ascii_case("en-GB") {
        Some(EN_GB_TIMEZONES)
    } else {
        None
    };

    if let Some(allowed_tzs) = allowed {
        if !allowed_tzs.contains(&tz) {
            errors.push(ValidationError::TimezoneLocaleImplausible {
                timezone: tz.to_string(),
                accept_language: lang.to_string(),
            });
        }
    }
}

fn check_platform_os_family_matches_ua_ch(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if p.platform.os_family != p.user_agent.ua_ch.platform {
        errors.push(ValidationError::PlatformOsMismatch {
            os_family: p.platform.os_family.clone(),
            ua_ch_platform: p.user_agent.ua_ch.platform.clone(),
        });
    }
}

fn check_noise_seeds_deterministic(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    let expected_canvas = seed::derive_canvas_noise(&p.id);
    if p.canvas.noise_seed != expected_canvas {
        errors.push(ValidationError::NoiseSeedMismatch {
            kind: NoiseSeedKind::Canvas,
            persona_id: p.id.clone(),
            expected: expected_canvas,
            actual: p.canvas.noise_seed,
        });
    }

    let expected_audio = seed::derive_audio_noise(&p.id);
    if p.audio.noise_seed != expected_audio {
        errors.push(ValidationError::NoiseSeedMismatch {
            kind: NoiseSeedKind::Audio,
            persona_id: p.id.clone(),
            expected: expected_audio,
            actual: p.audio.noise_seed,
        });
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Extract the Chrome major from `MAJOR.MINOR.BUILD.PATCH`, returning
/// `None` for malformed input (no leading numeric segment).
fn parse_chrome_major(version: &str) -> Option<u32> {
    let major_str = version.split('.').next()?;
    if major_str.is_empty() {
        return None;
    }
    major_str.parse::<u32>().ok()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Persona;
    use pretty_assertions::assert_eq;

    /// Canonical valid persona, derived from `SCHEMA.md` §"TOML layout"
    /// in `carbonyl-fingerprint-corpus`. Chrome 148 is the only major in
    /// `CHROME_REFERENCES`, so all v1 fixtures pin to this version.
    const VALID_PERSONA_TOML: &str = r#"
[persona]
id = "persona-test-valid"
generator_version = "2026.04.18"
browser_family = "chrome"
browser_version = "148.0.7778.167"
release_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7778.167 Safari/537.36"

[persona.user_agent.ua_ch]
brands = [["Chromium", "148"], ["Not_A Brand", "8"], ["Google Chrome", "148"]]
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
ja4 = "t13d1516h2_8daaf6152771_773c5fd3846b"
ja4h_template = "po11nn12enus"
http2_akamai = "1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
http3_enabled = false

[persona.behavior]
typing_persona = "normal"
mouse_persona = "desk_mouse_windmouse"

[persona.profile]
user_data_dir = "/tmp/persona-test-valid"
"#;

    fn parse_valid() -> Persona {
        toml::from_str(VALID_PERSONA_TOML).expect("VALID_PERSONA_TOML parses")
    }

    // --------- Rule 1: UA ↔ browser_version ---------

    #[test]
    fn rule1_passes_when_ua_contains_browser_version() {
        let p = parse_valid();
        assert!(validate(&p).is_ok(), "schema-derived persona must validate");
    }

    #[test]
    fn rule1_fails_when_ua_omits_browser_version() {
        let mut p = parse_valid();
        // Replace 148.0.7778.167 with 146.0.0.0 — UA now disagrees with
        // browser_version field.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("148.0.7778.167", "146.0.0.0");

        let report = validate(&p).expect_err("must fail");
        assert!(
            report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::UserAgentMissingBrowserVersion { .. })),
            "expected UserAgentMissingBrowserVersion in {:?}",
            report.errors()
        );
    }

    // --------- Rule 2: UA-CH brands ↔ Chrome major ---------

    #[test]
    fn rule2_fails_when_google_chrome_brand_major_mismatches() {
        let mut p = parse_valid();
        // Persona claims Chrome 148 but UA-CH brand says Google Chrome 146.
        for (name, ver) in p.persona.user_agent.ua_ch.brands.iter_mut() {
            if name == "Google Chrome" {
                *ver = "146".to_string();
            }
        }

        let report = validate(&p).expect_err("must fail");
        assert!(
            report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::UaChGoogleChromeBrandMismatch { .. })),
            "expected UaChGoogleChromeBrandMismatch in {:?}",
            report.errors()
        );
    }

    #[test]
    fn rule2_fails_when_google_chrome_brand_absent() {
        let mut p = parse_valid();
        p.persona
            .user_agent
            .ua_ch
            .brands
            .retain(|(name, _)| name != "Google Chrome");

        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::UaChGoogleChromeBrandMismatch { .. })));
    }

    // --------- Rule 3: JA4 ↔ Chrome reference ---------

    #[test]
    fn rule3_fails_on_ja4_mismatch() {
        let mut p = parse_valid();
        p.persona.network.ja4 = "t13d1516h2_DEADBEEF_DEADBEEF".to_string();

        let report = validate(&p).expect_err("must fail");
        assert!(
            report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::Ja4Mismatch { major: 148, .. })),
            "expected Ja4Mismatch for major 148 in {:?}",
            report.errors()
        );
    }

    #[test]
    fn rule3_fails_when_chrome_major_unknown() {
        let mut p = parse_valid();
        p.persona.browser_version = "999.0.0.0".to_string();
        // Also realign UA so we don't trip rule 1, and brand so we don't
        // trip rule 2. We're testing rule 3 specifically.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("148.0.7778.167", "999.0.0.0");
        for (name, ver) in p.persona.user_agent.ua_ch.brands.iter_mut() {
            if name == "Google Chrome" || name == "Chromium" {
                *ver = "999".to_string();
            }
        }

        let report = validate(&p).expect_err("must fail");
        assert_eq!(
            report.errors(),
            &[ValidationError::UnknownChromeReference(999)]
        );
    }

    // --------- browser_version parsing (Chrome family) ---------

    #[test]
    fn malformed_chrome_version_is_reported_and_skips_dependent_rules() {
        let mut p = parse_valid();
        p.persona.browser_version = "not-a-version".to_string();
        // Realign UA so rule 1 still passes (it does substring match —
        // any UA containing "not-a-version" works; but we just want the
        // malformed-version error to surface without crashing).
        p.persona.user_agent.full =
            "Mozilla/5.0 (...) Chrome/not-a-version Safari/537.36".to_string();

        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::ChromeVersionMalformed(_))));
        // Rules 2 and 3 must NOT have run (we have no major to check).
        assert!(!report.errors().iter().any(|e| matches!(
            e,
            ValidationError::UaChGoogleChromeBrandMismatch { .. }
                | ValidationError::Ja4Mismatch { .. }
                | ValidationError::UnknownChromeReference(_)
        )));
    }

    // --------- Rule A: hardware bounds ---------

    #[test]
    fn rule_a_passes_at_boundary_eight() {
        let p = parse_valid();
        // Fixture pins both fields to 8 (the boundary). Already validates.
        assert_eq!(p.persona.device.hardware_concurrency, 8);
        assert_eq!(p.persona.device.device_memory, 8);
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_a_fails_when_hardware_concurrency_exceeds_eight() {
        let mut p = parse_valid();
        p.persona.device.hardware_concurrency = 16;
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(|e| matches!(
            e,
            ValidationError::HardwareBoundExceeded {
                field: "hardware_concurrency",
                value: 16,
                max: 8,
            }
        )));
    }

    #[test]
    fn rule_a_fails_when_device_memory_exceeds_eight() {
        let mut p = parse_valid();
        p.persona.device.device_memory = 32;
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(|e| matches!(
            e,
            ValidationError::HardwareBoundExceeded {
                field: "device_memory",
                value: 32,
                max: 8,
            }
        )));
    }

    // --------- Rule B: platform.os_family ↔ ua_ch.platform ---------

    #[test]
    fn rule_b_passes_when_os_family_matches_ua_ch_platform() {
        let p = parse_valid();
        // Fixture: both = "Linux".
        assert_eq!(p.persona.platform.os_family, "Linux");
        assert_eq!(p.persona.user_agent.ua_ch.platform, "Linux");
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_b_fails_when_os_family_differs_from_ua_ch_platform() {
        let mut p = parse_valid();
        p.persona.user_agent.ua_ch.platform = "Windows".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(|e| matches!(
            e,
            ValidationError::PlatformOsMismatch { ua_ch_platform, .. } if ua_ch_platform == "Windows"
        )));
    }

    // --------- Rule C: Linux ↛ macOS fonts ---------

    #[test]
    fn rule_c_passes_on_linux_with_neutral_fonts() {
        let p = parse_valid();
        // Fixture has Arial + DejaVu Sans only.
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_c_fails_when_linux_persona_lists_helvetica_neue() {
        let mut p = parse_valid();
        p.persona.fonts.available.push("Helvetica Neue".to_string());
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(|e| matches!(
            e,
            ValidationError::LinuxForbiddenFont { font, reason: "macos-only" } if font == "Helvetica Neue"
        )));
    }

    #[test]
    fn rule_c_fails_for_each_macos_only_font_present() {
        let mut p = parse_valid();
        p.persona.fonts.available.push("Menlo".to_string());
        p.persona.fonts.available.push("San Francisco".to_string());
        let report = validate(&p).expect_err("must fail");
        let macos_fonts: Vec<_> = report
            .errors()
            .iter()
            .filter_map(|e| match e {
                ValidationError::LinuxForbiddenFont {
                    font,
                    reason: "macos-only",
                } => Some(font.clone()),
                _ => None,
            })
            .collect();
        assert!(macos_fonts.contains(&"Menlo".to_string()));
        assert!(macos_fonts.contains(&"San Francisco".to_string()));
    }

    // --------- Rule D: Linux ↛ Windows fonts ---------

    #[test]
    fn rule_d_fails_when_linux_persona_lists_segoe_ui() {
        let mut p = parse_valid();
        p.persona.fonts.available.push("Segoe UI".to_string());
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(|e| matches!(
            e,
            ValidationError::LinuxForbiddenFont { font, reason: "windows-only" } if font == "Segoe UI"
        )));
    }

    #[test]
    fn rule_d_fails_for_each_windows_only_font_present() {
        let mut p = parse_valid();
        p.persona.fonts.available.push("Calibri".to_string());
        p.persona.fonts.available.push("Consolas".to_string());
        let report = validate(&p).expect_err("must fail");
        let win_fonts: Vec<_> = report
            .errors()
            .iter()
            .filter_map(|e| match e {
                ValidationError::LinuxForbiddenFont {
                    font,
                    reason: "windows-only",
                } => Some(font.clone()),
                _ => None,
            })
            .collect();
        assert!(win_fonts.contains(&"Calibri".to_string()));
        assert!(win_fonts.contains(&"Consolas".to_string()));
    }

    #[test]
    fn rule_d_distinguishes_macos_and_windows_reasons() {
        let mut p = parse_valid();
        p.persona.fonts.available.push("Helvetica Neue".to_string()); // macos
        p.persona.fonts.available.push("Tahoma".to_string()); // windows
        let report = validate(&p).expect_err("must fail");
        let mut saw_macos = false;
        let mut saw_windows = false;
        for e in report.errors() {
            if let ValidationError::LinuxForbiddenFont { reason, .. } = e {
                if *reason == "macos-only" {
                    saw_macos = true;
                }
                if *reason == "windows-only" {
                    saw_windows = true;
                }
            }
        }
        assert!(saw_macos && saw_windows);
    }

    // --------- Rule E: Linux ↛ ANGLE/DirectX WebGL ---------

    #[test]
    fn rule_e_passes_with_angle_opengl_on_linux() {
        let p = parse_valid();
        // Fixture renderer: "ANGLE (..., OpenGL 4.6)" — ANGLE+OpenGL is OK.
        assert!(p.persona.webgl.renderer.contains("ANGLE"));
        assert!(p.persona.webgl.renderer.contains("OpenGL"));
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_e_fails_with_angle_direct3d_on_linux() {
        let mut p = parse_valid();
        p.persona.webgl.renderer =
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::LinuxForbiddenWebGlRenderer { .. })));
    }

    #[test]
    fn rule_e_fails_with_angle_directx_on_linux() {
        let mut p = parse_valid();
        p.persona.webgl.renderer = "ANGLE (DirectX 11)".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::LinuxForbiddenWebGlRenderer { .. })));
    }

    #[test]
    fn rule_e_skips_non_linux_personas() {
        let mut p = parse_valid();
        // Switch persona to Windows so rule B doesn't trip; align UA-CH too.
        p.persona.platform.os_family = "Windows".to_string();
        p.persona.user_agent.ua_ch.platform = "Windows".to_string();
        // Now ANGLE+Direct3D is plausible — rule E should NOT fire.
        p.persona.webgl.renderer = "ANGLE (NVIDIA Direct3D11)".to_string();

        let result = validate(&p);
        // We may still hit other errors for the Windows persona (e.g., font
        // checks don't fire on Windows but we haven't asserted Windows
        // plausibility yet). What matters is no LinuxForbiddenWebGlRenderer.
        if let Err(report) = result {
            assert!(!report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::LinuxForbiddenWebGlRenderer { .. })));
        }
    }

    // --------- Rule F: Chrome ↔ H2 Akamai reference ---------

    #[test]
    fn rule_f_passes_with_canonical_h2_akamai() {
        let p = parse_valid();
        // Fixture pins the canonical Chrome 148 H2 fingerprint.
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_f_fails_on_h2_akamai_mismatch() {
        let mut p = parse_valid();
        p.persona.network.http2_akamai =
            "1:65536,2:0,4:6291456,6:262144|15663105|0|p,a,s,m".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::H2AkamaiMismatch { major: 148, .. })));
    }

    #[test]
    fn rule_f_does_not_double_report_unknown_chrome_major() {
        let mut p = parse_valid();
        p.persona.browser_version = "999.0.0.0".to_string();
        // Realign UA + brands so only rules 3/F can fire.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("148.0.7778.167", "999.0.0.0");
        for (name, ver) in p.persona.user_agent.ua_ch.brands.iter_mut() {
            if name == "Google Chrome" || name == "Chromium" {
                *ver = "999".to_string();
            }
        }

        let report = validate(&p).expect_err("must fail");
        // Exactly one UnknownChromeReference, no H2AkamaiMismatch.
        let unknown_count = report
            .errors()
            .iter()
            .filter(|e| matches!(e, ValidationError::UnknownChromeReference(_)))
            .count();
        assert_eq!(unknown_count, 1);
        assert!(!report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::H2AkamaiMismatch { .. })));
    }

    // --------- Rule G: timezone ↔ accept_language plausibility ---------

    #[test]
    fn rule_g_passes_with_en_us_and_us_eastern() {
        let p = parse_valid();
        assert!(p.persona.locale.accept_language.starts_with("en-US"));
        assert_eq!(p.persona.locale.timezone, "America/New_York");
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_g_passes_with_en_us_and_us_pacific() {
        let mut p = parse_valid();
        p.persona.locale.timezone = "America/Los_Angeles".to_string();
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_g_fails_with_en_us_and_tokyo() {
        let mut p = parse_valid();
        p.persona.locale.timezone = "Asia/Tokyo".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report.errors().iter().any(
            |e| matches!(e, ValidationError::TimezoneLocaleImplausible { timezone, .. } if timezone == "Asia/Tokyo")
        ));
    }

    #[test]
    fn rule_g_passes_with_en_gb_and_london() {
        let mut p = parse_valid();
        p.persona.locale.accept_language = "en-GB,en;q=0.9".to_string();
        p.persona.locale.timezone = "Europe/London".to_string();
        assert!(validate(&p).is_ok());
    }

    #[test]
    fn rule_g_fails_with_en_gb_and_paris() {
        let mut p = parse_valid();
        p.persona.locale.accept_language = "en-GB,en;q=0.9".to_string();
        p.persona.locale.timezone = "Europe/Paris".to_string();
        let report = validate(&p).expect_err("must fail");
        assert!(report
            .errors()
            .iter()
            .any(|e| matches!(e, ValidationError::TimezoneLocaleImplausible { .. })));
    }

    #[test]
    fn rule_g_skips_unknown_locales() {
        let mut p = parse_valid();
        // de-DE not in our allowlist; rule must NOT fire regardless of timezone.
        p.persona.locale.accept_language = "de-DE,de;q=0.9".to_string();
        p.persona.locale.timezone = "Asia/Tokyo".to_string();
        let result = validate(&p);
        // Other rules may pass; only assert rule G didn't fire.
        if let Err(report) = result {
            assert!(!report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::TimezoneLocaleImplausible { .. })));
        }
    }

    // --------- Rule H: deterministic noise seeds ---------

    #[test]
    fn rule_h_passes_with_canonically_derived_seeds() {
        // VALID_PERSONA_TOML carries the seeds derived from the persona
        // id "persona-test-valid"; validate() must not fire rule H.
        let p = parse_valid();
        validate(&p).expect("canonical fixture must validate");
    }

    #[test]
    fn rule_h_fails_when_canvas_seed_drifts_from_id() {
        let mut p = parse_valid();
        p.persona.canvas.noise_seed = p.persona.canvas.noise_seed.wrapping_add(1);
        let report = validate(&p).expect_err("mutated canvas seed must fail");
        assert!(
            report.errors().iter().any(|e| matches!(
                e,
                ValidationError::NoiseSeedMismatch {
                    kind: NoiseSeedKind::Canvas,
                    ..
                }
            )),
            "expected NoiseSeedMismatch for canvas, got: {:?}",
            report.errors()
        );
    }

    #[test]
    fn rule_h_fails_when_audio_seed_drifts_from_id() {
        let mut p = parse_valid();
        p.persona.audio.noise_seed = p.persona.audio.noise_seed.wrapping_add(1);
        let report = validate(&p).expect_err("mutated audio seed must fail");
        assert!(
            report.errors().iter().any(|e| matches!(
                e,
                ValidationError::NoiseSeedMismatch {
                    kind: NoiseSeedKind::Audio,
                    ..
                }
            )),
            "expected NoiseSeedMismatch for audio, got: {:?}",
            report.errors()
        );
    }

    #[test]
    fn rule_h_fails_when_id_changes_without_reseeding() {
        // Common tampering scenario: someone edits persona.id but
        // forgets to regenerate seeds. Rule H must catch both seeds
        // simultaneously.
        let mut p = parse_valid();
        p.persona.id = "persona-impersonator".to_string();
        let report = validate(&p).expect_err("id drift must fail");
        let kinds: Vec<NoiseSeedKind> = report
            .errors()
            .iter()
            .filter_map(|e| match e {
                ValidationError::NoiseSeedMismatch { kind, .. } => Some(*kind),
                _ => None,
            })
            .collect();
        assert!(
            kinds.contains(&NoiseSeedKind::Canvas) && kinds.contains(&NoiseSeedKind::Audio),
            "expected both canvas + audio mismatches, got: {kinds:?}"
        );
    }

    // --------- ValidationReport plumbing ---------

    #[test]
    fn report_collects_multiple_errors_in_one_pass() {
        let mut p = parse_valid();
        // Trip rules 1, 2, AND 3 simultaneously.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("148.0.7778.167", "146.0.0.0");
        for (name, ver) in p.persona.user_agent.ua_ch.brands.iter_mut() {
            if name == "Google Chrome" {
                *ver = "146".to_string();
            }
        }
        p.persona.network.ja4 = "t13d1516h2_BAD_BAD".to_string();

        let report = validate(&p).expect_err("must fail");
        assert_eq!(
            report.errors().len(),
            3,
            "expected 3 errors, got: {:?}",
            report.errors()
        );
    }

    #[test]
    fn report_displays_with_numbered_list() {
        let mut p = parse_valid();
        p.persona.network.ja4 = "BAD".to_string();
        let report = validate(&p).expect_err("must fail");
        let s = report.to_string();
        assert!(s.contains("persona failed validation"));
        assert!(s.contains("1. "));
    }

    // --------- Property tests (proptest) ---------

    proptest::proptest! {
        /// Any persona we can construct from `VALID_PERSONA_TOML` and then
        /// mutate the JA4 to an arbitrary non-canonical string must fail
        /// rule 3 and only rule 3 (other fields untouched).
        #[test]
        fn prop_arbitrary_ja4_fails_rule3_only(
            ja4 in "[a-zA-Z0-9_]{3,80}"
        ) {
            // Skip the fluke where proptest happens to sample the canonical JA4.
            proptest::prop_assume!(ja4 != "t13d1516h2_8daaf6152771_773c5fd3846b");

            let mut p = parse_valid();
            p.persona.network.ja4 = ja4.clone();

            let result = validate(&p);
            let report = result.expect_err("mutated JA4 must fail validation");
            proptest::prop_assert!(
                report.errors().iter().all(|e| matches!(e, ValidationError::Ja4Mismatch { .. })),
                "expected only Ja4Mismatch, got: {:?}",
                report.errors()
            );
        }

        /// `parse_chrome_major` survives arbitrary input without panic.
        #[test]
        fn prop_parse_chrome_major_never_panics(s in ".*") {
            let _ = parse_chrome_major(&s);
        }

        /// For any valid Chrome 148 persona with the canonical JA4,
        /// arbitrarily replacing browser_version with a different major
        /// (and nothing else) trips rule 1 (UA mismatch) plus either
        /// rule 2 or rule 3 (or both); the report is non-empty.
        #[test]
        fn prop_chrome_major_drift_always_fails(major in 100u32..200u32) {
            proptest::prop_assume!(major != 148);
            let mut p = parse_valid();
            p.persona.browser_version = format!("{major}.0.0.0");
            let report = validate(&p).expect_err("major drift must fail");
            proptest::prop_assert!(!report.errors().is_empty());
        }
    }
}
