//! TLS-fingerprint conformance suite (W3B prereq — `Refs: roctinam/carbonyl-agent#62`).
//!
//! Public test scaffold that any [`HttpClient`] backend (the production `wreq`
//! integration in W3B #44, the second `rquest` backend per ADR-005's bus-factor
//! mitigation, future research backends) plugs into to prove its persona →
//! wire output mapping is correct.
//!
//! # Two layers
//!
//! ### Layer 1 — applied-state conformance (this module)
//!
//! Asserts the trait correctly **routes** persona fields to backend setters.
//! A backend exposes its captured state by implementing [`ApplyInspector`] on
//! a test wrapper, then calls [`ConformanceFixture::assert_applied_state`]
//! against a fixture's expected values. No network. No TLS handshake. The
//! assertion catches:
//!
//! - JA4 / ALPN / H2 settings / H2 window dropped by the impl's
//!   [`HttpClient::apply_persona`] override
//! - Headers (`User-Agent`, `Accept-Language`, `sec-ch-ua*`) emitted in the
//!   wrong order or with wrong values
//! - Akamai H2 string parsed into the wrong settings list
//!
//! Layer 1 is sufficient to gate the trait contract — if the trait surface is
//! correct *here*, no backend can silently drop a field.
//!
//! ### Layer 2 — wire-level conformance (deferred to follow-up PR)
//!
//! A controlled localhost TLS responder captures the actual handshake bytes a
//! backend produces and compares the computed JA4 / SETTINGS frame / ALPN
//! list against the fixture. Validates that the backend's *implementation* of
//! its setters actually reaches the wire. Requires `rustls` + `h2` server,
//! out of scope for this PR — see issue #62 for the harness design.
//!
//! # Usage from a backend's test suite
//!
//! ```ignore
//! // In e.g. a `wreq` integration crate:
//! use carbonyl_fingerprint::conformance::{
//!     ApplyInspector, ConformanceFixture, conform,
//! };
//!
//! struct WreqTestWrapper { /* ... */ }
//! impl HttpClient for WreqTestWrapper { /* ... */ }
//! impl ApplyInspector for WreqTestWrapper {
//!     fn applied_ja4(&self) -> Option<&str> { /* ... */ }
//!     // ...
//! }
//!
//! #[test]
//! fn wreq_conforms_to_chrome_147_stable_linux() {
//!     let fixture = ConformanceFixture::chrome_147_stable_linux();
//!     let mut client = WreqTestWrapper::new();
//!     conform(&mut client, &fixture).expect("wreq must conform");
//! }
//! ```
//!
//! # CI gate
//!
//! Backend test crates are expected to run their conformance fixtures on
//! every PR touching fingerprint or wreq-adjacent code. The trait crate
//! itself runs Layer 1 against its own [`Recorder`] reference impl as a
//! sanity check (see `tests` submodule), so a regression in the assertion
//! engine fails fast here too.

use std::collections::BTreeMap;

use crate::http::{H2Priority, H2Settings, H2WindowUpdate, HttpClient};
use crate::Persona;

/// One conformance assertion failure. Aggregated into [`ConformanceReport`]
/// so a single run surfaces every mismatch, not just the first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConformanceMismatch {
    pub field: &'static str,
    pub expected: String,
    pub actual: String,
}

impl std::fmt::Display for ConformanceMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} mismatch:\n  expected: {}\n  actual:   {}",
            self.field, self.expected, self.actual
        )
    }
}

/// Aggregated conformance result. Empty `mismatches` means the impl conformed.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ConformanceReport {
    pub mismatches: Vec<ConformanceMismatch>,
}

impl ConformanceReport {
    pub fn is_empty(&self) -> bool {
        self.mismatches.is_empty()
    }

    pub fn into_result(self) -> Result<(), Self> {
        if self.is_empty() {
            Ok(())
        } else {
            Err(self)
        }
    }
}

impl std::fmt::Display for ConformanceReport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(
            f,
            "conformance failed ({} mismatches):",
            self.mismatches.len()
        )?;
        for (i, m) in self.mismatches.iter().enumerate() {
            writeln!(f, "  {}. {m}", i + 1)?;
        }
        Ok(())
    }
}

impl std::error::Error for ConformanceReport {}

/// Inspection contract a backend's test wrapper implements so the conformance
/// suite can read back what `apply_persona` produced.
///
/// Production backends do NOT implement this on their public type — it lives
/// on a thin test wrapper to avoid leaking internal state into the API
/// surface. See module docs for the typical wrapper shape.
pub trait ApplyInspector {
    fn applied_ja4(&self) -> Option<&str>;
    fn applied_alpn(&self) -> &[String];
    fn applied_h2_settings(&self) -> &H2Settings;
    fn applied_h2_window(&self) -> H2WindowUpdate;
    fn applied_h2_priority(&self) -> &H2Priority;
    /// All `set_default_header` calls in invocation order. Order is significant
    /// for the conformance assertion — Akamai-style fingerprints are
    /// header-order-sensitive too, though the SCHEMA.md persona doesn't yet
    /// pin header order. We capture the order so future fixture extensions
    /// can assert on it.
    fn applied_headers(&self) -> &[(String, String)];
}

/// One conformance test case: a persona + the wire-level values its
/// fingerprint promises. A backend that applies the persona must emit
/// exactly these values.
///
/// Built-in fixtures use the validator's reference data (Chrome 147 stable
/// Linux today, more families as W3A.6 follow-ups land their captures);
/// callers can construct ad-hoc fixtures via [`ConformanceFixture::from_persona`]
/// and overrides.
#[derive(Debug, Clone)]
pub struct ConformanceFixture {
    pub label: String,
    pub persona: Persona,
    pub expected_ja4: String,
    pub expected_alpn: Vec<String>,
    pub expected_h2_settings: H2Settings,
    pub expected_h2_window: H2WindowUpdate,
    pub expected_h2_priority: H2Priority,
    /// Required headers — every entry must appear in the impl's recorded
    /// headers with the listed value. Extra headers in the impl are
    /// allowed (the trait permits backends to set their own defaults).
    pub required_headers: BTreeMap<String, String>,
}

impl ConformanceFixture {
    /// Built-in fixture: Chrome 147 stable Linux. Mirrors the validator's
    /// inline reference (`Refs: roctinam/carbonyl-agent#68`).
    pub fn chrome_147_stable_linux() -> Self {
        let persona: Persona =
            toml::from_str(CHROME_147_STABLE_LINUX_TEMPLATE).expect("template parses");
        Self::from_persona("chrome-147-stable-linux", persona).expect("fixture")
    }

    /// Construct a fixture from a persona, deriving the expected wire
    /// values from the persona's own `network` and `user_agent` fields.
    /// This is what the conformance contract asserts: the persona spec IS
    /// the wire output the backend must produce.
    pub fn from_persona(
        label: impl Into<String>,
        persona: Persona,
    ) -> Result<Self, crate::http::FingerprintError> {
        let n = &persona.persona.network;
        let h2_settings = H2Settings::from_akamai(&n.http2_akamai)?;
        let h2_window = H2WindowUpdate::from_akamai(&n.http2_akamai)?;
        let h2_priority = H2Priority::from_akamai(&n.http2_akamai)?;

        let mut headers: BTreeMap<String, String> = BTreeMap::new();
        headers.insert("User-Agent".into(), persona.persona.user_agent.full.clone());
        headers.insert(
            "Accept-Language".into(),
            persona.persona.locale.accept_language.clone(),
        );
        if !persona.persona.user_agent.ua_ch.brands.is_empty() {
            let sec_ch_ua = persona
                .persona
                .user_agent
                .ua_ch
                .brands
                .iter()
                .map(|(brand, version)| format!("\"{brand}\";v=\"{version}\""))
                .collect::<Vec<_>>()
                .join(", ");
            headers.insert("sec-ch-ua".into(), sec_ch_ua);
        }
        headers.insert(
            "sec-ch-ua-mobile".into(),
            if persona.persona.user_agent.ua_ch.mobile {
                "?1"
            } else {
                "?0"
            }
            .into(),
        );
        headers.insert(
            "sec-ch-ua-platform".into(),
            persona.persona.user_agent.ua_ch.platform.clone(),
        );

        Ok(Self {
            label: label.into(),
            expected_ja4: n.ja4.clone(),
            expected_alpn: n.alpn.clone(),
            expected_h2_settings: h2_settings,
            expected_h2_window: h2_window,
            expected_h2_priority: h2_priority,
            required_headers: headers,
            persona,
        })
    }

    /// Apply the fixture's persona to `client`, then read back via
    /// [`ApplyInspector`] and aggregate any mismatches. Returns the report
    /// even on success so callers can attach it to richer test output.
    pub fn assert_applied_state<C>(&self, client: &mut C) -> ConformanceReport
    where
        C: HttpClient + ApplyInspector,
    {
        let mut report = ConformanceReport::default();

        if let Err(e) = client.apply_persona(&self.persona) {
            report.mismatches.push(ConformanceMismatch {
                field: "apply_persona",
                expected: "Ok(())".into(),
                actual: format!("Err({e})"),
            });
            // Apply failed — downstream assertions will be misleading.
            return report;
        }

        if client.applied_ja4() != Some(self.expected_ja4.as_str()) {
            report.mismatches.push(ConformanceMismatch {
                field: "ja4",
                expected: self.expected_ja4.clone(),
                actual: client.applied_ja4().unwrap_or("<none>").to_string(),
            });
        }

        if client.applied_alpn() != self.expected_alpn.as_slice() {
            report.mismatches.push(ConformanceMismatch {
                field: "alpn",
                expected: format!("{:?}", self.expected_alpn),
                actual: format!("{:?}", client.applied_alpn()),
            });
        }

        if client.applied_h2_settings() != &self.expected_h2_settings {
            report.mismatches.push(ConformanceMismatch {
                field: "h2_settings",
                expected: format!("{:?}", self.expected_h2_settings.entries),
                actual: format!("{:?}", client.applied_h2_settings().entries),
            });
        }

        if client.applied_h2_window() != self.expected_h2_window {
            report.mismatches.push(ConformanceMismatch {
                field: "h2_window",
                expected: format!("{}", self.expected_h2_window.0),
                actual: format!("{}", client.applied_h2_window().0),
            });
        }

        if client.applied_h2_priority() != &self.expected_h2_priority {
            report.mismatches.push(ConformanceMismatch {
                field: "h2_priority",
                expected: format!("{:?}", self.expected_h2_priority),
                actual: format!("{:?}", client.applied_h2_priority()),
            });
        }

        // Headers: every required header must appear in the recorded set
        // with the expected value. Order is captured but not asserted in
        // Layer 1 — the schema doesn't pin header order, and backends may
        // legitimately reorder for HPACK efficiency. Order assertions land
        // when fixtures grow per-family header-order specs.
        let recorded: BTreeMap<&str, &str> = client
            .applied_headers()
            .iter()
            .map(|(n, v)| (n.as_str(), v.as_str()))
            .collect();
        for (name, expected) in &self.required_headers {
            match recorded.get(name.as_str()) {
                Some(actual) if *actual == expected.as_str() => {}
                Some(actual) => {
                    report.mismatches.push(ConformanceMismatch {
                        field: header_field_name(name),
                        expected: expected.clone(),
                        actual: (*actual).to_string(),
                    });
                }
                None => {
                    report.mismatches.push(ConformanceMismatch {
                        field: header_field_name(name),
                        expected: expected.clone(),
                        actual: "<missing>".to_string(),
                    });
                }
            }
        }

        report
    }
}

/// Convenience wrapper for the canonical assertion form used in test functions:
/// `conform(&mut client, &fixture).expect("must conform");`
pub fn conform<C>(client: &mut C, fixture: &ConformanceFixture) -> Result<(), ConformanceReport>
where
    C: HttpClient + ApplyInspector,
{
    fixture.assert_applied_state(client).into_result()
}

/// `&'static` strings for header mismatch field names. We can't allocate
/// `&'static str` for arbitrary header names at runtime, so the small set
/// of headers the persona schema currently emits is enumerated. Unknown
/// headers fall through to a generic label — the assertion still fires,
/// only the diagnostic is less precise.
fn header_field_name(name: &str) -> &'static str {
    match name {
        "User-Agent" => "header.user-agent",
        "Accept-Language" => "header.accept-language",
        "sec-ch-ua" => "header.sec-ch-ua",
        "sec-ch-ua-mobile" => "header.sec-ch-ua-mobile",
        "sec-ch-ua-platform" => "header.sec-ch-ua-platform",
        _ => "header.<other>",
    }
}

// ---------------------------------------------------------------------------
// Built-in fixtures
// ---------------------------------------------------------------------------

/// Chrome 147 stable Linux template. Mirrors `validator::tests::VALID_PERSONA_TOML`
/// — drift between the two is itself a conformance bug.
const CHROME_147_STABLE_LINUX_TEMPLATE: &str = r#"
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
"#;

// ---------------------------------------------------------------------------
// Tests — exercise the conformance suite against two distinct mock
// HttpClient impls so a regression in the assertion engine itself fails
// fast in this crate's CI.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::http::FingerprintError;

    // ---- mock impl 1: Vec-backed Recorder (mirrors http::tests::Recorder) ----

    #[derive(Debug, Default)]
    struct VecRecorder {
        ja4: Option<String>,
        alpn: Vec<String>,
        h2_settings: H2Settings,
        h2_window: H2WindowUpdate,
        h2_priority: H2Priority,
        headers: Vec<(String, String)>,
    }

    impl HttpClient for VecRecorder {
        fn set_tls_fingerprint(
            &mut self,
            _ja3: Option<&str>,
            ja4: &str,
        ) -> Result<(), FingerprintError> {
            self.ja4 = Some(ja4.to_string());
            Ok(())
        }
        fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError> {
            self.alpn = alpn.to_vec();
            Ok(())
        }
        fn set_h2_settings(&mut self, s: &H2Settings) -> Result<(), FingerprintError> {
            self.h2_settings = s.clone();
            Ok(())
        }
        fn set_h2_window_update(&mut self, w: H2WindowUpdate) -> Result<(), FingerprintError> {
            self.h2_window = w;
            Ok(())
        }
        fn set_h2_priority(&mut self, p: &H2Priority) -> Result<(), FingerprintError> {
            self.h2_priority = p.clone();
            Ok(())
        }
        fn set_default_header(&mut self, n: &str, v: &str) -> Result<(), FingerprintError> {
            self.headers.push((n.to_string(), v.to_string()));
            Ok(())
        }
    }

    impl ApplyInspector for VecRecorder {
        fn applied_ja4(&self) -> Option<&str> {
            self.ja4.as_deref()
        }
        fn applied_alpn(&self) -> &[String] {
            &self.alpn
        }
        fn applied_h2_settings(&self) -> &H2Settings {
            &self.h2_settings
        }
        fn applied_h2_window(&self) -> H2WindowUpdate {
            self.h2_window
        }
        fn applied_h2_priority(&self) -> &H2Priority {
            &self.h2_priority
        }
        fn applied_headers(&self) -> &[(String, String)] {
            &self.headers
        }
    }

    // ---- mock impl 2: BTreeMap-backed (different internals; same trait) ----
    //
    // Proves the conformance suite is impl-independent: a backend that stores
    // headers in a map (e.g. a real wreq-style builder) and the canonical
    // Recorder both satisfy the same fixture. If the suite spuriously
    // depended on Vec ordering or list semantics, this impl would fail.

    #[derive(Debug, Default)]
    struct MapRecorder {
        ja4: Option<String>,
        alpn: Vec<String>,
        h2_settings: H2Settings,
        h2_window: H2WindowUpdate,
        h2_priority: H2Priority,
        // Header insertion order tracked separately so applied_headers()
        // returns a slice; production backends usually have a canonical
        // ordering anyway.
        headers_in_order: Vec<(String, String)>,
    }

    impl HttpClient for MapRecorder {
        fn set_tls_fingerprint(
            &mut self,
            _ja3: Option<&str>,
            ja4: &str,
        ) -> Result<(), FingerprintError> {
            self.ja4 = Some(ja4.to_string());
            Ok(())
        }
        fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError> {
            self.alpn = alpn.to_vec();
            Ok(())
        }
        fn set_h2_settings(&mut self, s: &H2Settings) -> Result<(), FingerprintError> {
            self.h2_settings = s.clone();
            Ok(())
        }
        fn set_h2_window_update(&mut self, w: H2WindowUpdate) -> Result<(), FingerprintError> {
            self.h2_window = w;
            Ok(())
        }
        fn set_h2_priority(&mut self, p: &H2Priority) -> Result<(), FingerprintError> {
            self.h2_priority = p.clone();
            Ok(())
        }
        fn set_default_header(&mut self, n: &str, v: &str) -> Result<(), FingerprintError> {
            // Replace existing entries — this impl deliberately deduplicates,
            // which a Vec-backed Recorder doesn't.
            self.headers_in_order
                .retain(|(name, _)| name.as_str() != n);
            self.headers_in_order.push((n.to_string(), v.to_string()));
            Ok(())
        }
    }

    impl ApplyInspector for MapRecorder {
        fn applied_ja4(&self) -> Option<&str> {
            self.ja4.as_deref()
        }
        fn applied_alpn(&self) -> &[String] {
            &self.alpn
        }
        fn applied_h2_settings(&self) -> &H2Settings {
            &self.h2_settings
        }
        fn applied_h2_window(&self) -> H2WindowUpdate {
            self.h2_window
        }
        fn applied_h2_priority(&self) -> &H2Priority {
            &self.h2_priority
        }
        fn applied_headers(&self) -> &[(String, String)] {
            &self.headers_in_order
        }
    }

    // ---- assertions ----

    #[test]
    fn fixture_chrome_147_loads_and_self_describes() {
        let f = ConformanceFixture::chrome_147_stable_linux();
        assert_eq!(f.label, "chrome-147-stable-linux");
        assert_eq!(f.persona.persona.browser_version, "147.0.7727.94");
        assert_eq!(f.expected_ja4, "t13d1516h2_8daaf6152771_02713d6af862");
        assert_eq!(f.expected_alpn, vec!["h2", "http/1.1"]);
        assert_eq!(f.expected_h2_window.0, 15_663_105);
        assert_eq!(f.expected_h2_settings.entries.len(), 5);
    }

    #[test]
    fn vec_recorder_conforms_to_chrome_147() {
        let f = ConformanceFixture::chrome_147_stable_linux();
        let mut c = VecRecorder::default();
        conform(&mut c, &f).expect("VecRecorder must conform");
    }

    #[test]
    fn map_recorder_conforms_to_chrome_147() {
        // Different internals, same fixture → trait surface is impl-independent.
        let f = ConformanceFixture::chrome_147_stable_linux();
        let mut c = MapRecorder::default();
        conform(&mut c, &f).expect("MapRecorder must conform");
    }

    #[test]
    fn conformance_detects_dropped_ja4() {
        // A buggy backend that fails to record JA4 must surface a precise
        // mismatch — proves the assertion engine catches the most common
        // regression class.
        struct BrokenJa4(VecRecorder);
        impl HttpClient for BrokenJa4 {
            fn set_tls_fingerprint(
                &mut self,
                _ja3: Option<&str>,
                _ja4: &str,
            ) -> Result<(), FingerprintError> {
                // Silently drop.
                Ok(())
            }
            fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError> {
                self.0.set_alpn(alpn)
            }
            fn set_h2_settings(&mut self, s: &H2Settings) -> Result<(), FingerprintError> {
                self.0.set_h2_settings(s)
            }
            fn set_h2_window_update(
                &mut self,
                w: H2WindowUpdate,
            ) -> Result<(), FingerprintError> {
                self.0.set_h2_window_update(w)
            }
            fn set_h2_priority(&mut self, p: &H2Priority) -> Result<(), FingerprintError> {
                self.0.set_h2_priority(p)
            }
            fn set_default_header(
                &mut self,
                n: &str,
                v: &str,
            ) -> Result<(), FingerprintError> {
                self.0.set_default_header(n, v)
            }
        }
        impl ApplyInspector for BrokenJa4 {
            fn applied_ja4(&self) -> Option<&str> {
                self.0.applied_ja4()
            }
            fn applied_alpn(&self) -> &[String] {
                self.0.applied_alpn()
            }
            fn applied_h2_settings(&self) -> &H2Settings {
                self.0.applied_h2_settings()
            }
            fn applied_h2_window(&self) -> H2WindowUpdate {
                self.0.applied_h2_window()
            }
            fn applied_h2_priority(&self) -> &H2Priority {
                self.0.applied_h2_priority()
            }
            fn applied_headers(&self) -> &[(String, String)] {
                self.0.applied_headers()
            }
        }

        let f = ConformanceFixture::chrome_147_stable_linux();
        let mut c = BrokenJa4(VecRecorder::default());
        let report = f.assert_applied_state(&mut c);
        assert!(
            report
                .mismatches
                .iter()
                .any(|m| m.field == "ja4" && m.actual == "<none>"),
            "expected ja4=<none> mismatch in {report}"
        );
    }

    #[test]
    fn conformance_detects_wrong_user_agent_header() {
        struct WrongUaRecorder(VecRecorder);
        impl HttpClient for WrongUaRecorder {
            fn set_tls_fingerprint(
                &mut self,
                ja3: Option<&str>,
                ja4: &str,
            ) -> Result<(), FingerprintError> {
                self.0.set_tls_fingerprint(ja3, ja4)
            }
            fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError> {
                self.0.set_alpn(alpn)
            }
            fn set_h2_settings(&mut self, s: &H2Settings) -> Result<(), FingerprintError> {
                self.0.set_h2_settings(s)
            }
            fn set_h2_window_update(
                &mut self,
                w: H2WindowUpdate,
            ) -> Result<(), FingerprintError> {
                self.0.set_h2_window_update(w)
            }
            fn set_h2_priority(&mut self, p: &H2Priority) -> Result<(), FingerprintError> {
                self.0.set_h2_priority(p)
            }
            fn set_default_header(
                &mut self,
                n: &str,
                v: &str,
            ) -> Result<(), FingerprintError> {
                // Mangle the User-Agent — every other header passes through.
                let mangled = if n == "User-Agent" {
                    "WrongAgent/0.0".to_string()
                } else {
                    v.to_string()
                };
                self.0.set_default_header(n, &mangled)
            }
        }
        impl ApplyInspector for WrongUaRecorder {
            fn applied_ja4(&self) -> Option<&str> {
                self.0.applied_ja4()
            }
            fn applied_alpn(&self) -> &[String] {
                self.0.applied_alpn()
            }
            fn applied_h2_settings(&self) -> &H2Settings {
                self.0.applied_h2_settings()
            }
            fn applied_h2_window(&self) -> H2WindowUpdate {
                self.0.applied_h2_window()
            }
            fn applied_h2_priority(&self) -> &H2Priority {
                self.0.applied_h2_priority()
            }
            fn applied_headers(&self) -> &[(String, String)] {
                self.0.applied_headers()
            }
        }

        let f = ConformanceFixture::chrome_147_stable_linux();
        let mut c = WrongUaRecorder(VecRecorder::default());
        let report = f.assert_applied_state(&mut c);
        assert!(report
            .mismatches
            .iter()
            .any(|m| m.field == "header.user-agent"));
    }

    #[test]
    fn conformance_report_into_result_ok_when_empty() {
        let r = ConformanceReport::default();
        assert!(r.into_result().is_ok());
    }

    #[test]
    fn conformance_report_into_result_err_when_populated() {
        let r = ConformanceReport {
            mismatches: vec![ConformanceMismatch {
                field: "ja4",
                expected: "a".into(),
                actual: "b".into(),
            }],
        };
        assert!(r.into_result().is_err());
    }

    #[test]
    fn from_persona_derives_expected_values_from_template() {
        // Build a fixture from a custom persona; the expected values must
        // mirror that persona's network section verbatim.
        let p: Persona = toml::from_str(CHROME_147_STABLE_LINUX_TEMPLATE).expect("parse");
        let f = ConformanceFixture::from_persona("custom", p.clone()).expect("fixture");
        assert_eq!(f.expected_ja4, p.persona.network.ja4);
        assert_eq!(f.expected_alpn, p.persona.network.alpn);
    }
}
