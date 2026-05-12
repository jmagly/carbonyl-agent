//! wreq-backed implementation of the
//! [`carbonyl_fingerprint::http::HttpClient`] trait.
//!
//! # Phase 2.2 status: persona → wreq client mapping
//!
//! Phase 2.1 (#80) shipped the trait scaffold and a [`PendingConfig`]
//! recorder. Phase 2.2 (#81) wires that recorder into wreq's
//! `ClientBuilder` so [`WreqClient::build`] returns a real
//! [`wreq::Client`]:
//!
//! 1. Persona's `browser_family` + `browser_version` → closest
//!    [`wreq_util::Emulation`] preset. The preset drives TLS
//!    ClientHello generation and HTTP/2 frame ordering — fields the
//!    `ClientBuilder` API does not expose individually.
//! 2. Persona's H2 SETTINGS (parsed from `network.http2_akamai`)
//!    are applied as overrides via [`wreq::Http2Builder`] setters on
//!    top of the preset. This is how the persona's exact Akamai
//!    shape reaches the wire even when no exact preset matches the
//!    family + version.
//! 3. Persona's `User-Agent`, `Accept-Language`, and (for Chrome
//!    family only — `Refs: roctinam/carbonyl-agent#71`) `sec-ch-ua*`
//!    headers become default request headers.
//!
//! See [`persona_to_emulation`] for the family + version → preset
//! mapping table. The mapping is "closest available preset" — wreq
//! ships up to Chrome 137 / Firefox 139 / Safari 18.3.1 today
//! whereas the W3A.6 personas target Chrome 147 / Firefox 150 /
//! Safari 26. That gap is unavoidable until wreq-util ships newer
//! presets; Phase 2.3 (#82) measures the resulting wire-level
//! divergence against the persona spec.
//!
//! ALPN is NOT exposed on `ClientBuilder` — it comes through the
//! emulation preset alongside the TLS ClientHello.

use carbonyl_fingerprint::conformance::ApplyInspector;
use carbonyl_fingerprint::http::{
    FingerprintError, H2Priority, H2Settings, H2WindowUpdate, HttpClient,
};
use carbonyl_fingerprint::schema::BrowserFamily;
use carbonyl_fingerprint::Persona;

/// HTTP/2 SETTINGS frame identifiers (RFC 9113 §6.5.2). Surfaced as
/// constants so the Phase 2.2 mapping doesn't bury magic numbers in
/// the `apply_h2_settings` body.
mod h2_setting {
    pub const HEADER_TABLE_SIZE: u16 = 0x01;
    pub const ENABLE_PUSH: u16 = 0x02;
    pub const MAX_CONCURRENT_STREAMS: u16 = 0x03;
    pub const INITIAL_WINDOW_SIZE: u16 = 0x04;
    pub const MAX_FRAME_SIZE: u16 = 0x05;
    pub const MAX_HEADER_LIST_SIZE: u16 = 0x06;
}

/// Pending state the trait setters accumulate into. Public so test
/// crates can read it back via [`ApplyInspector`] before
/// [`WreqClient::build`] is ever called.
#[derive(Debug, Default, Clone)]
pub struct PendingConfig {
    pub ja3: Option<String>,
    pub ja4: Option<String>,
    pub alpn: Vec<String>,
    pub h2_settings: H2Settings,
    pub h2_window: H2WindowUpdate,
    pub h2_priority: H2Priority,
    /// Default headers in invocation order (per the trait contract —
    /// see [`ApplyInspector::applied_headers`]).
    pub headers: Vec<(String, String)>,
    /// Closest wreq emulation preset selected by [`HttpClient::apply_persona`].
    /// `None` until `apply_persona` runs — Phase 2.2's mapping requires
    /// browser family/version visibility that the trait setters alone
    /// don't carry.
    pub emulation: Option<wreq_util::Emulation>,
}

/// wreq-backed HTTP client builder. The trait setters record into
/// [`PendingConfig`]; [`Self::build`] consumes the recorded state
/// into a configured [`wreq::Client`].
#[derive(Debug, Default)]
pub struct WreqClient {
    pending: PendingConfig,
}

impl WreqClient {
    /// Fresh client with empty pending config.
    pub fn new() -> Self {
        Self::default()
    }

    /// Borrow the pending configuration. Useful for test inspection
    /// without consuming the builder.
    pub fn pending(&self) -> &PendingConfig {
        &self.pending
    }

    /// Apply a persona — same behavior as the trait's default
    /// `apply_persona`, but also records the emulation preset so
    /// [`Self::build`] can wire it in. We override the trait
    /// implementation here rather than relying on the default because
    /// `HttpClient::apply_persona` doesn't receive the
    /// `BrowserFamily`/version fields needed for preset selection.
    pub fn apply_persona_typed(&mut self, persona: &Persona) -> Result<(), FingerprintError> {
        self.pending.emulation = Some(persona_to_emulation(persona));
        // Delegate the rest to the trait default — that path is
        // already responsible for sec-ch-ua gating on Chrome-only
        // (#71) and the H2 Akamai parsing.
        self.apply_persona(persona)
    }

    /// Consume the recorder and produce a configured [`wreq::Client`].
    ///
    /// Mapping summary (see module docs for rationale):
    /// - Emulation preset from [`PendingConfig::emulation`] (set by
    ///   [`Self::apply_persona_typed`])
    /// - H2 SETTINGS overrides via [`wreq::Http2Builder`]
    /// - Default headers via `ClientBuilder::default_headers`
    /// - User-Agent via `ClientBuilder::user_agent` (extracted from
    ///   the recorded headers list — the persona emits `User-Agent` as
    ///   one of the default headers, but wreq prefers it through the
    ///   dedicated setter so it lands in the right position)
    pub fn build(self) -> Result<wreq::Client, WreqError> {
        let mut builder = wreq::Client::builder();

        // 1. Emulation preset
        if let Some(emul) = self.pending.emulation {
            builder = builder.emulation(emul);
        }

        // 2. H2 SETTINGS overrides
        let h2_settings = self.pending.h2_settings.clone();
        let h2_window = self.pending.h2_window;
        builder = builder.http2(move |h2| {
            apply_h2_settings(h2, &h2_settings, h2_window);
        });

        // 3. Default headers — extract User-Agent for the dedicated
        //    setter so wreq's emulation-aware header ordering puts
        //    it where the persona expects.
        let mut headers = http::HeaderMap::new();
        let mut user_agent: Option<String> = None;
        for (name, value) in &self.pending.headers {
            if name.eq_ignore_ascii_case("user-agent") {
                user_agent = Some(value.clone());
                continue;
            }
            if let (Ok(hn), Ok(hv)) = (
                http::HeaderName::from_bytes(name.as_bytes()),
                http::HeaderValue::from_str(value),
            ) {
                headers.insert(hn, hv);
            }
        }
        if !headers.is_empty() {
            builder = builder.default_headers(headers);
        }
        if let Some(ua) = user_agent {
            builder = builder.user_agent(ua);
        }

        builder.build().map_err(WreqError::Build)
    }
}

/// Apply persona-derived HTTP/2 SETTINGS entries + initial window
/// to the wreq Http2Builder. SETTINGS entries that wreq does NOT
/// expose individually (`MAX_HEADER_LIST_SIZE` is in flux upstream;
/// unknown_setting8/9 cover the experimental shape) are skipped with
/// the rationale that the emulation preset already carries them in
/// the right ballpark for the chosen browser.
fn apply_h2_settings(
    mut h2: wreq::Http2Builder<'_>,
    settings: &H2Settings,
    window: H2WindowUpdate,
) {
    for (id, value) in &settings.entries {
        match *id {
            h2_setting::HEADER_TABLE_SIZE => {
                h2.header_table_size(*value);
            }
            h2_setting::ENABLE_PUSH => {
                h2.enable_push(*value != 0);
            }
            h2_setting::MAX_CONCURRENT_STREAMS => {
                h2.max_concurrent_streams(*value);
            }
            h2_setting::INITIAL_WINDOW_SIZE => {
                h2.initial_stream_window_size(*value);
            }
            h2_setting::MAX_FRAME_SIZE => {
                h2.max_frame_size(*value);
            }
            h2_setting::MAX_HEADER_LIST_SIZE => {
                // wreq 5.x's Http2Builder doesn't expose a public
                // `max_header_list_size` setter at the time of
                // writing. The emulation preset's default is
                // browser-realistic; skipping here means the persona
                // can't override it until wreq adds the setter.
                // Phase 2.3 (#82) measures whether this matters at
                // the wire layer.
            }
            _ => {
                // Unknown / experimental setting (e.g. id 8/9 used by
                // some Chromium builds). wreq's unknown_setting8/9
                // helpers exist but the mapping is fragile — defer
                // until a persona actually needs them.
            }
        }
    }
    // Akamai's window-update value applies at the connection level,
    // not stream level — that's what `initial_connection_window_size`
    // sets.
    h2.initial_connection_window_size(window.0);
}

/// Pick the closest [`wreq_util::Emulation`] preset for a persona.
///
/// Selection rules:
/// - Chrome family → newest `Chrome*` preset (wreq-util tops out at
///   Chrome137 as of v2; the W3A.6 personas target Chrome147 →
///   closest is Chrome137)
/// - Firefox family → newest `Firefox*` preset (Firefox139)
/// - Safari family → newest desktop `Safari18_3_1` for macOS, newest
///   `SafariIos17_4_1` for iOS (distinguished by the persona's
///   `platform.os_family` — `iOS` → iOS preset)
/// - Mobile Chrome on Android → `Chrome137` (no Android-Chrome
///   preset exists; the family contract on #71 keeps sec-ch-ua-mobile
///   coherent regardless)
///
/// This is the "closest available" mapping. When wreq-util ships
/// newer presets the table here updates in lockstep; the persona
/// spec evolves on its own cadence.
pub fn persona_to_emulation(persona: &Persona) -> wreq_util::Emulation {
    use wreq_util::Emulation::*;
    let p = &persona.persona;
    let os = p.platform.os_family.to_ascii_lowercase();
    match p.browser_family {
        BrowserFamily::Chrome => {
            // Chrome 147 → Chrome137 (newest preset). Android variant
            // has no dedicated preset — same TLS/H2 shape applies and
            // sec-ch-ua-mobile carries the platform signal.
            Chrome137
        }
        BrowserFamily::Firefox => Firefox139,
        BrowserFamily::Safari => {
            if os == "ios" || os == "ipados" {
                SafariIos17_4_1
            } else {
                Safari18_3_1
            }
        }
    }
}

impl HttpClient for WreqClient {
    fn set_tls_fingerprint(
        &mut self,
        ja3: Option<&str>,
        ja4: &str,
    ) -> Result<(), FingerprintError> {
        self.pending.ja3 = ja3.map(str::to_string);
        self.pending.ja4 = Some(ja4.to_string());
        Ok(())
    }

    fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError> {
        self.pending.alpn = alpn.to_vec();
        Ok(())
    }

    fn set_h2_settings(&mut self, settings: &H2Settings) -> Result<(), FingerprintError> {
        self.pending.h2_settings = settings.clone();
        Ok(())
    }

    fn set_h2_window_update(&mut self, update: H2WindowUpdate) -> Result<(), FingerprintError> {
        self.pending.h2_window = update;
        Ok(())
    }

    fn set_h2_priority(&mut self, priority: &H2Priority) -> Result<(), FingerprintError> {
        self.pending.h2_priority = priority.clone();
        Ok(())
    }

    fn set_default_header(&mut self, name: &str, value: &str) -> Result<(), FingerprintError> {
        self.pending
            .headers
            .push((name.to_string(), value.to_string()));
        Ok(())
    }
}

impl ApplyInspector for WreqClient {
    fn applied_ja4(&self) -> Option<&str> {
        self.pending.ja4.as_deref()
    }
    fn applied_alpn(&self) -> &[String] {
        &self.pending.alpn
    }
    fn applied_h2_settings(&self) -> &H2Settings {
        &self.pending.h2_settings
    }
    fn applied_h2_window(&self) -> H2WindowUpdate {
        self.pending.h2_window
    }
    fn applied_h2_priority(&self) -> &H2Priority {
        &self.pending.h2_priority
    }
    fn applied_headers(&self) -> &[(String, String)] {
        &self.pending.headers
    }
}

/// Errors emitted by the wreq backend.
#[derive(Debug, thiserror::Error)]
pub enum WreqError {
    /// `wreq::Client::builder().build()` failed — typically a TLS
    /// backend initialization issue or a system-resolver problem.
    #[error("wreq client build failed: {0}")]
    Build(wreq::Error),
}

#[cfg(test)]
mod tests {
    use super::*;
    use carbonyl_fingerprint::conformance::{conform, ConformanceFixture};

    /// Layer 1 smoke against Chrome 147 — confirms the trait setters
    /// + recorder still work end-to-end after Phase 2.2 added wreq.
    #[test]
    fn layer1_chrome_147_round_trips() {
        let fixture = ConformanceFixture::chrome_147_stable_linux();
        let mut client = WreqClient::new();
        client.apply_persona_typed(&fixture.persona).unwrap();
        conform(&mut client, &fixture).expect("Chrome 147 must conform at Layer 1");
        assert_eq!(
            client.pending().emulation,
            Some(wreq_util::Emulation::Chrome137)
        );
    }

    #[test]
    fn layer1_firefox_150_round_trips() {
        let fixture = ConformanceFixture::firefox_150_stable_linux();
        let mut client = WreqClient::new();
        client.apply_persona_typed(&fixture.persona).unwrap();
        conform(&mut client, &fixture).expect("Firefox 150 must conform at Layer 1");
        assert_eq!(
            client.pending().emulation,
            Some(wreq_util::Emulation::Firefox139)
        );
    }

    #[test]
    fn layer1_safari_26_round_trips() {
        let fixture = ConformanceFixture::safari_26_macos();
        let mut client = WreqClient::new();
        client.apply_persona_typed(&fixture.persona).unwrap();
        conform(&mut client, &fixture).expect("Safari 26 must conform at Layer 1");
        assert_eq!(
            client.pending().emulation,
            Some(wreq_util::Emulation::Safari18_3_1)
        );
    }

    #[test]
    fn layer1_mobile_chrome_android_round_trips() {
        let fixture = ConformanceFixture::mobile_chrome_android();
        let mut client = WreqClient::new();
        client.apply_persona_typed(&fixture.persona).unwrap();
        conform(&mut client, &fixture).expect("Mobile Chrome must conform at Layer 1");
        assert_eq!(
            client.pending().emulation,
            Some(wreq_util::Emulation::Chrome137)
        );
    }

    #[test]
    fn layer1_mobile_safari_ios_round_trips() {
        let fixture = ConformanceFixture::mobile_safari_ios();
        let mut client = WreqClient::new();
        client.apply_persona_typed(&fixture.persona).unwrap();
        conform(&mut client, &fixture).expect("Mobile Safari iOS must conform at Layer 1");
        assert_eq!(
            client.pending().emulation,
            Some(wreq_util::Emulation::SafariIos17_4_1)
        );
    }

    /// All five fixtures must produce a working wreq::Client without
    /// panicking. We don't issue a request — Phase 2.3 (#82) covers
    /// the wire-level verification.
    #[test]
    fn build_succeeds_for_all_fixtures() {
        for (label, fixture) in [
            ("chrome-147", ConformanceFixture::chrome_147_stable_linux()),
            (
                "firefox-150",
                ConformanceFixture::firefox_150_stable_linux(),
            ),
            ("safari-26", ConformanceFixture::safari_26_macos()),
            ("mobile-chrome", ConformanceFixture::mobile_chrome_android()),
            ("mobile-safari", ConformanceFixture::mobile_safari_ios()),
        ] {
            let mut client = WreqClient::new();
            client.apply_persona_typed(&fixture.persona).unwrap();
            let result = client.build();
            assert!(
                result.is_ok(),
                "wreq build must succeed for fixture {label}: {:?}",
                result.err()
            );
        }
    }
}
