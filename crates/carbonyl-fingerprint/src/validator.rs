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
//! Three rules from the SCHEMA.md table are enforced:
//!
//! 1. `user_agent.full` contains `chrome_version` as a substring
//! 2. `ua_ch.brands` contains `["Google Chrome", "<major>"]` matching
//!    `chrome_version`'s major
//! 3. `network.ja4` matches the canonical JA4 for `chrome_version`'s
//!    major (looked up in the inline reference table below)
//!
//! Remaining rules (platform/UA-CH equality, OS↔WebGL, OS↔fonts,
//! locale↔timezone, hardware bounds, deterministic noise seeds) land in
//! follow-up commits on this issue.

use crate::schema::Persona;
use thiserror::Error;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// One violation surfaced by `validate()`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ValidationError {
    #[error(
        "user_agent.full does not contain chrome_version `{chrome_version}` \
         (got `{user_agent_full}`)"
    )]
    UserAgentMissingChromeVersion {
        chrome_version: String,
        user_agent_full: String,
    },

    #[error(
        "chrome_version `{0}` is not a valid SemVer-ish version (expected \
         `MAJOR.MINOR.BUILD.PATCH` with numeric major)"
    )]
    ChromeVersionMalformed(String),

    #[error(
        "ua_ch.brands is missing a `[\"Google Chrome\", \"{expected_major}\"]` \
         entry matching chrome_version major (got brands: {actual:?})"
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
// Reference data — Chrome stable (canonical JA4 / H2 Akamai)
// ---------------------------------------------------------------------------
//
// Populated from real captures via tls.peet.ws / Wireshark. Only versions
// with verified ground truth are listed; unknown majors fail closed via
// `UnknownChromeReference`.
//
// Sources:
//   - Chrome 147: SCHEMA.md exemplar in carbonyl-fingerprint-corpus
//
// To extend: add a row, run conformance tests in W3B (#62) against the
// real Chrome build to confirm, then commit. Do not invent JA4 values.

struct ChromeReference {
    major: u32,
    ja4: &'static str,
    // `h2_akamai` and `alpn` will be added as the H2 rule lands.
}

const CHROME_REFERENCES: &[ChromeReference] = &[ChromeReference {
    major: 147,
    ja4: "t13d1516h2_8daaf6152771_02713d6af862",
}];

fn lookup_chrome_reference(major: u32) -> Option<&'static ChromeReference> {
    CHROME_REFERENCES.iter().find(|r| r.major == major)
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/// Validate the persona against all v1 hard rules. Returns `Ok(())` iff
/// every rule passes; `Err(report)` carries the full set of violations.
pub fn validate(persona: &Persona) -> Result<(), ValidationReport> {
    let mut errors: Vec<ValidationError> = Vec::new();

    let p = &persona.persona;

    // Parse Chrome major up-front; downstream rules need it. If parsing
    // fails we record the malformed-version error and skip rules that
    // depend on it (rules 2 and 3).
    let chrome_major = match parse_chrome_major(&p.chrome_version) {
        Some(m) => Some(m),
        None => {
            errors.push(ValidationError::ChromeVersionMalformed(
                p.chrome_version.clone(),
            ));
            None
        }
    };

    // Rule 1: user_agent.full must contain chrome_version verbatim.
    check_user_agent_contains_chrome_version(p, &mut errors);

    // Rule 2: UA-CH brands must include ["Google Chrome", "<major>"].
    if let Some(major) = chrome_major {
        check_ua_ch_brand_matches_major(p, major, &mut errors);
    }

    // Rule 3: network.ja4 must match canonical JA4 for chrome major.
    if let Some(major) = chrome_major {
        check_ja4_matches_chrome_reference(p, major, &mut errors);
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(ValidationReport { errors })
    }
}

// ---------------------------------------------------------------------------
// Rule implementations
// ---------------------------------------------------------------------------

fn check_user_agent_contains_chrome_version(
    p: &crate::schema::PersonaInner,
    errors: &mut Vec<ValidationError>,
) {
    if !p.user_agent.full.contains(&p.chrome_version) {
        errors.push(ValidationError::UserAgentMissingChromeVersion {
            chrome_version: p.chrome_version.clone(),
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
    errors: &mut Vec<ValidationError>,
) {
    let Some(reference) = lookup_chrome_reference(major) else {
        errors.push(ValidationError::UnknownChromeReference(major));
        return;
    };

    if p.network.ja4 != reference.ja4 {
        errors.push(ValidationError::Ja4Mismatch {
            major,
            expected: reference.ja4.to_string(),
            actual: p.network.ja4.clone(),
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
    /// in `carbonyl-fingerprint-corpus`. Chrome 147 is the only major in
    /// `CHROME_REFERENCES`, so all v1 fixtures pin to this version.
    const VALID_PERSONA_TOML: &str = r#"
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
noise_seed = 2134389534

[persona.audio]
noise_seed = 729608453

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
"#;

    fn parse_valid() -> Persona {
        toml::from_str(VALID_PERSONA_TOML).expect("VALID_PERSONA_TOML parses")
    }

    // --------- Rule 1: UA ↔ chrome_version ---------

    #[test]
    fn rule1_passes_when_ua_contains_chrome_version() {
        let p = parse_valid();
        assert!(validate(&p).is_ok(), "schema-derived persona must validate");
    }

    #[test]
    fn rule1_fails_when_ua_omits_chrome_version() {
        let mut p = parse_valid();
        // Replace 147.0.7727.94 with 146.0.0.0 — UA now disagrees with
        // chrome_version field.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("147.0.7727.94", "146.0.0.0");

        let report = validate(&p).expect_err("must fail");
        assert!(
            report
                .errors()
                .iter()
                .any(|e| matches!(e, ValidationError::UserAgentMissingChromeVersion { .. })),
            "expected UserAgentMissingChromeVersion in {:?}",
            report.errors()
        );
    }

    // --------- Rule 2: UA-CH brands ↔ Chrome major ---------

    #[test]
    fn rule2_fails_when_google_chrome_brand_major_mismatches() {
        let mut p = parse_valid();
        // Persona claims Chrome 147 but UA-CH brand says Google Chrome 146.
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
                .any(|e| matches!(e, ValidationError::Ja4Mismatch { major: 147, .. })),
            "expected Ja4Mismatch for major 147 in {:?}",
            report.errors()
        );
    }

    #[test]
    fn rule3_fails_when_chrome_major_unknown() {
        let mut p = parse_valid();
        p.persona.chrome_version = "999.0.0.0".to_string();
        // Also realign UA so we don't trip rule 1, and brand so we don't
        // trip rule 2. We're testing rule 3 specifically.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("147.0.7727.94", "999.0.0.0");
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

    // --------- chrome_version parsing ---------

    #[test]
    fn malformed_chrome_version_is_reported_and_skips_dependent_rules() {
        let mut p = parse_valid();
        p.persona.chrome_version = "not-a-version".to_string();
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

    // --------- ValidationReport plumbing ---------

    #[test]
    fn report_collects_multiple_errors_in_one_pass() {
        let mut p = parse_valid();
        // Trip rules 1, 2, AND 3 simultaneously.
        p.persona.user_agent.full = p
            .persona
            .user_agent
            .full
            .replace("147.0.7727.94", "146.0.0.0");
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
            proptest::prop_assume!(ja4 != "t13d1516h2_8daaf6152771_02713d6af862");

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

        /// For any valid Chrome 147 persona with the canonical JA4,
        /// arbitrarily replacing chrome_version with a different major
        /// (and nothing else) trips rule 1 (UA mismatch) plus either
        /// rule 2 or rule 3 (or both); the report is non-empty.
        #[test]
        fn prop_chrome_major_drift_always_fails(major in 100u32..200u32) {
            proptest::prop_assume!(major != 147);
            let mut p = parse_valid();
            p.persona.chrome_version = format!("{major}.0.0.0");
            let report = validate(&p).expect_err("major drift must fail");
            proptest::prop_assert!(!report.errors().is_empty());
        }
    }
}
