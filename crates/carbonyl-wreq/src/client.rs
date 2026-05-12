//! Pending-state recorder + scaffold for the eventual wreq-backed
//! [`HttpClient`] implementation.
//!
//! The persona-to-wreq mapping (Phase 2.2 — `Refs: roctinam/carbonyl-agent#81`)
//! will replace the `todo!()` in [`WreqClient::build`] with the real
//! conversion. Until then, the setters record values into
//! [`PendingConfig`] so the conformance harness can verify the trait
//! surface compiles end-to-end and the values reach the backend
//! unchanged.

use carbonyl_fingerprint::conformance::ApplyInspector;
use carbonyl_fingerprint::http::{
    FingerprintError, H2Priority, H2Settings, H2WindowUpdate, HttpClient,
};

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
    /// see [`carbonyl_fingerprint::conformance::ApplyInspector::applied_headers`]).
    pub headers: Vec<(String, String)>,
}

/// wreq-backed HTTP client builder. Phase 2.1 (this crate) stops at
/// recording; Phase 2.2 (#81) wires the recorded values into
/// `wreq::Client`.
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
    /// before [`Self::build`] has wired anything to wreq.
    pub fn pending(&self) -> &PendingConfig {
        &self.pending
    }

    /// Consume the recorder and produce a configured `wreq::Client`.
    ///
    /// **Phase 2.1**: this returns [`WreqError::BuildIncomplete`]
    /// because the wreq dep itself isn't pulled in yet (boring-sys2
    /// adds 5-10 min to a cold build; we land that in Phase 2.2
    /// alongside the actual mapping work). Phase 2.2 (#81) replaces
    /// the body with the real persona→wreq config conversion.
    pub fn build(self) -> Result<(), WreqError> {
        Err(WreqError::BuildIncomplete)
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

/// Errors emitted by the wreq backend. Phase 2.2 fills the variants
/// with mapping failures (unsupported family, malformed Akamai
/// string, etc.); Phase 2.1 ships only `BuildIncomplete` so the
/// `build()` signature is stable.
#[derive(Debug, thiserror::Error)]
pub enum WreqError {
    /// Phase 2.1 placeholder — `build()` was called before the
    /// persona mapping landed. Will be removed in Phase 2.2.
    #[error("WreqClient::build not implemented until Phase 2.2 (#81)")]
    BuildIncomplete,
}

#[cfg(test)]
mod tests {
    use super::*;
    use carbonyl_fingerprint::conformance::ConformanceFixture;
    use carbonyl_fingerprint::http::HttpClient;

    /// Layer 1 smoke: the trait surface accepts a persona and
    /// `ApplyInspector` reads back exactly what was set. This proves
    /// the wreq backend's scaffold is wired into the harness
    /// correctly — the Phase 2.2 mapping work then targets
    /// `WreqClient::build` without breaking the trait shape.
    #[test]
    fn smoke_apply_persona_records_into_pending() {
        let fixture = ConformanceFixture::chrome_147_stable_linux();
        let mut client = WreqClient::new();

        client
            .apply_persona(&fixture.persona)
            .expect("apply_persona must succeed against a valid persona");

        // The trait recorded the persona into PendingConfig.
        assert_eq!(client.applied_ja4(), Some(fixture.expected_ja4.as_str()));
        assert_eq!(client.applied_alpn(), fixture.expected_alpn.as_slice());
        assert_eq!(client.applied_h2_settings(), &fixture.expected_h2_settings);
        assert_eq!(client.applied_h2_window(), fixture.expected_h2_window);
        assert_eq!(client.applied_h2_priority(), &fixture.expected_h2_priority);
        assert!(
            !client.applied_headers().is_empty(),
            "Chrome persona must emit at least User-Agent + Accept-Language"
        );
    }

    /// Phase 2.1 contract: `build()` returns `BuildIncomplete` until
    /// Phase 2.2 lands. This test pins that — once Phase 2.2 wires
    /// the wreq client construction, this test gets rewritten to
    /// assert on a successful build.
    #[test]
    fn build_returns_build_incomplete_until_phase_2_2() {
        let err = WreqClient::new().build().unwrap_err();
        assert!(matches!(err, WreqError::BuildIncomplete));
    }
}
