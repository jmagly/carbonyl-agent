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

use crate::presets::{self, BrowserVersion, Platform};

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

impl WreqClient {
    /// **Test-only** alternate build path: route through the in-house
    /// preset registry + a hand-built `wreq::EmulationProvider` instead
    /// of `wreq_util::Emulation::*`. Per Iteration A item 4 (#101) /
    /// ADR-W02 / `adr-002-addendum-wreq-api-shape.md`.
    ///
    /// Production [`Self::build`] is unchanged — it still uses
    /// `wreq_util::Emulation` until Iteration B (#102) flips the
    /// cutover. This helper exists so the L2 conformance suite can
    /// measure the new path's wire output against the same fixtures the
    /// legacy path is measured against.
    ///
    /// # Behavior with empty preset registry
    ///
    /// `presets::preset_for` returns `None` until concrete entries land
    /// (each requires a captured fixture per `fixtures-plan.md`). When
    /// it returns `None`, this helper falls back to: persona-declared
    /// fields + `TlsConfig::default()` + `Http2Config::default()`. The
    /// resulting wire output will diverge significantly from real
    /// browsers — that's the conformance suite's job to measure, and
    /// the divergence list documents what each preset entry needs to
    /// fix when populated.
    ///
    /// # Why a separate method
    ///
    /// Iteration A's quality gate requires production `WreqClient::build`
    /// to remain unchanged (no shipped-code behavior change yet). A
    /// parallel build path lets the new code compile and be exercised
    /// by tests without affecting any production caller.
    pub fn build_via_registry(self) -> Result<wreq::Client, WreqError> {
        let provider = persona_to_emulation_provider(&self.pending);
        wreq::Client::builder()
            .emulation(provider)
            .cert_verification(false)
            .build()
            .map_err(WreqError::Build)
    }
}

/// Construct a `wreq::EmulationProvider` from recorded persona state.
///
/// When `presets::preset_for` returns `Some`, preset values fill gaps
/// the persona doesn't declare (per ADR-W02's persona-first model).
/// When it returns `None`, only persona-declared fields populate the
/// provider; everything else is wreq's defaults.
///
/// Field encodings follow the addendum to ADR-W02 (`adr-002-addendum-wreq-api-shape.md`).
fn persona_to_emulation_provider(pending: &PendingConfig) -> wreq::EmulationProvider {
    let preset = preset_for_pending(pending);

    // ----- TLS -----
    let mut tls = wreq::TlsConfig::default();
    tls.alpn_protos = alpn_from_persona_or_preset(&pending.alpn, preset);
    if let Some(p) = preset {
        if let Some(cipher) = p.tls.cipher_list {
            tls.cipher_list = Some(std::borrow::Cow::Borrowed(cipher));
        }
        if let Some(sigs) = p.tls.sigalgs_list {
            tls.sigalgs_list = Some(std::borrow::Cow::Borrowed(sigs));
        }
        if let Some(ext) = p.tls.extension_permutation_indices {
            tls.extension_permutation_indices = Some(std::borrow::Cow::Borrowed(ext));
        }
        tls.grease_enabled = p.tls.grease_enabled;
        tls.permute_extensions = p.tls.permute_extensions;
        // `supported_groups` (raw u16 IANA codes in the preset table)
        // requires translation to `wreq::SslCurve` at this point — but
        // `SslCurve` is an opaque enum without a public IANA-code
        // constructor, so the mapping table belongs alongside the
        // first concrete preset entry. Until then, leave `tls.curves`
        // at the wreq default. Documented divergence point.
        let _ = p.tls.supported_groups;
    }

    // ----- HTTP/2 -----
    let mut h2 = wreq::Http2Config::builder().build();
    apply_h2_settings_to_config(&mut h2, &pending.h2_settings);
    if pending.h2_window.0 != 0 {
        h2.initial_connection_window_size = Some(pending.h2_window.0);
    } else if let Some(p) = preset {
        h2.initial_connection_window_size = Some(p.h2.initial_connection_window);
    }
    // Note: `h2.headers_pseudo_order` and `h2.priority` need the
    // same wreq-types translation as `tls.curves` and land alongside
    // the first concrete preset entry.

    // ----- Headers -----
    let mut headers = http::HeaderMap::new();
    let mut headers_order_vec: Vec<http::HeaderName> = Vec::new();
    for (name, value) in &pending.headers {
        if let (Ok(hn), Ok(hv)) = (
            http::HeaderName::from_bytes(name.as_bytes()),
            http::HeaderValue::from_str(value),
        ) {
            headers.insert(hn.clone(), hv);
            headers_order_vec.push(hn);
        }
    }
    if let Some(p) = preset {
        // Apply preset header order if persona didn't supply enough
        // ordering signal. Persona's recorded order is the
        // invocation order from `apply_persona`; preset's
        // `default_order` is the wire-realistic order.
        for header_name in p.headers.default_order {
            if let Ok(hn) = http::HeaderName::from_bytes(header_name.as_bytes()) {
                if !headers_order_vec.contains(&hn) {
                    headers_order_vec.push(hn);
                }
            }
        }
        // Apply preset static defaults for headers the persona didn't
        // emit (Accept, Accept-Encoding, sec-fetch-*, Priority, etc.).
        for (name, value) in p.headers.static_defaults {
            if let (Ok(hn), Ok(hv)) = (
                http::HeaderName::from_bytes(name.as_bytes()),
                http::HeaderValue::from_str(value),
            ) {
                headers.entry(hn).or_insert(hv);
            }
        }
    }

    // TypedBuilder fixes the chain at compile time. `tls_config`,
    // `http2_config`, `default_headers` use `setter(into)` and accept
    // `Option<T>` directly. `headers_order` uses `setter(strip_option,
    // into)` and takes the inner `Cow` directly — we always pass an
    // owned (possibly empty) Cow rather than skip the setter, since
    // skipping would leave positions inconsistent across branches.
    let headers_opt = if headers.is_empty() {
        None
    } else {
        Some(headers)
    };
    let order_cow: std::borrow::Cow<'static, [http::HeaderName]> =
        std::borrow::Cow::Owned(headers_order_vec);
    wreq::EmulationProvider::builder()
        .tls_config(tls)
        .http2_config(h2)
        .default_headers(headers_opt)
        .headers_order(order_cow)
        .build()
}

/// Map persona ALPN list → wreq's three-variant `AlpnProtos` enum.
///
/// wreq exposes only `HTTP1`, `HTTP2`, `ALL`. Arbitrary ALPN lists
/// (e.g. `["h3", "h2"]`) round to `ALL` since wreq has no per-protocol
/// public constructor. For the five canonical personas this is exact:
/// all five declare `["h2", "http/1.1"]` which maps cleanly to `ALL`.
fn alpn_from_persona_or_preset(
    persona_alpn: &[String],
    preset: Option<&'static presets::PresetTable>,
) -> wreq::AlpnProtos {
    let alpn_list: &[String] = if !persona_alpn.is_empty() {
        persona_alpn
    } else if let Some(p) = preset {
        // Coerce preset's &[&str] into a comparable shape via early
        // return — borrowing-lifetimes prevent a clean unified branch.
        return alpn_from_str_slice(p.tls.alpn_default);
    } else {
        return wreq::AlpnProtos::ALL;
    };
    let strs: Vec<&str> = alpn_list.iter().map(String::as_str).collect();
    alpn_from_str_slice(&strs)
}

fn alpn_from_str_slice(alpn: &[&str]) -> wreq::AlpnProtos {
    let has_h2 = alpn.iter().any(|p| *p == "h2");
    let has_h11 = alpn.iter().any(|p| *p == "http/1.1");
    match (has_h2, has_h11) {
        (true, true) => wreq::AlpnProtos::ALL,
        (true, false) => wreq::AlpnProtos::HTTP2,
        (false, true) => wreq::AlpnProtos::HTTP1,
        (false, false) => wreq::AlpnProtos::ALL,
    }
}

/// Look up the preset matching the persona-derived family/version/platform.
///
/// Recovers `BrowserVersion` and `Platform` from the recorded persona
/// state without re-parsing the persona itself; pendingConfig carries
/// the persona-derived h2/headers data but not the family triple, so
/// for now we infer Chrome desktop as a placeholder. Iteration B's
/// cutover replaces this inference with explicit family/version/platform
/// fields on `PendingConfig` (per `design-preset-registry.md` §3 sketch).
fn preset_for_pending(_pending: &PendingConfig) -> Option<&'static presets::PresetTable> {
    // The registry is empty until concrete entries land alongside
    // captured fixtures (Iteration A item 3 / Iteration B items 1a-1d).
    // The chrome-147-desktop preset would be looked up here once it
    // exists; until then `preset_for` always returns None.
    presets::preset_for(
        BrowserFamily::Chrome,
        BrowserVersion {
            major: 147,
            minor: 0,
        },
        Platform::Desktop,
    )
}

/// Translate persona-recorded H2 SETTINGS entries into the
/// per-field shape `wreq::Http2Config` exposes.
fn apply_h2_settings_to_config(h2: &mut wreq::Http2Config, settings: &H2Settings) {
    for (id, value) in &settings.entries {
        match *id {
            h2_setting::HEADER_TABLE_SIZE => h2.header_table_size = Some(*value),
            h2_setting::ENABLE_PUSH => h2.enable_push = Some(*value != 0),
            h2_setting::MAX_CONCURRENT_STREAMS => h2.max_concurrent_streams = Some(*value),
            h2_setting::INITIAL_WINDOW_SIZE => h2.initial_stream_window_size = Some(*value),
            h2_setting::MAX_FRAME_SIZE => h2.max_frame_size = Some(*value),
            h2_setting::MAX_HEADER_LIST_SIZE => h2.max_header_list_size = Some(*value),
            _ => {} // experimental setting IDs (8/9) — not exposed by wreq
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
