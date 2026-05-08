//! HTTP client persona-binding trait — Phase 3A scaffold for ADR-005.
//!
//! The trait defined here is the normative contract between a [`Persona`] and
//! a TLS-fingerprint-aware HTTP client. The concrete `wreq` integration is
//! intentionally out of scope for this crate (it lives in W3B / #44); this
//! module only fixes the surface so consumers in W3B never name a backend
//! crate directly. A second backend can be plugged in later for the
//! conformance suite without churning call sites.
//!
//! See `.aiwg/architecture/adr-005-tls-fingerprint-http-client.md` §
//! "Persona binding contract" for the field-by-field mapping this trait
//! implements.
//!
//! # Status
//!
//! Trait surface only. No production impl. The `tests` module exercises a
//! recorder impl so the surface is verifiably callable.
//!
//! [`Persona`]: crate::Persona

use crate::Persona;

/// Errors produced when binding a [`Persona`] to an HTTP client.
#[derive(Debug, thiserror::Error)]
pub enum FingerprintError {
    /// A required field was missing or malformed (e.g. unparseable
    /// `http2_akamai` template, empty `ja4`).
    #[error("invalid persona field `{field}`: {reason}")]
    InvalidField { field: &'static str, reason: String },

    /// The backend rejected an otherwise well-formed value (e.g. an ALPN
    /// list the underlying TLS stack will not negotiate).
    #[error("backend rejected `{field}`: {reason}")]
    BackendRejected { field: &'static str, reason: String },
}

/// Initial HTTP/2 SETTINGS frame, expressed as ordered `(id, value)` pairs.
///
/// The order is significant — it is part of the Akamai H2 fingerprint. Use
/// [`H2Settings::from_akamai`] to parse from the `network.http2_akamai`
/// template string (e.g. `"1:65536,2:0,4:6291456,6:262144"`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct H2Settings {
    pub entries: Vec<(u16, u32)>,
}

impl H2Settings {
    /// Parse the `entries` portion (before the first `|`) of an Akamai H2
    /// fingerprint string.
    ///
    /// Format: `"id:value,id:value,..."`. Other sections (window update,
    /// priority, pseudo-header order) are returned by sibling parsers.
    pub fn from_akamai(s: &str) -> Result<Self, FingerprintError> {
        let entries_part = s.split('|').next().unwrap_or(s);
        let mut entries = Vec::new();
        for piece in entries_part.split(',').filter(|p| !p.is_empty()) {
            let (id, val) = piece.split_once(':').ok_or_else(|| {
                FingerprintError::InvalidField {
                    field: "http2_akamai",
                    reason: format!("settings piece `{piece}` missing `:`"),
                }
            })?;
            let id: u16 = id.parse().map_err(|e| FingerprintError::InvalidField {
                field: "http2_akamai",
                reason: format!("settings id `{id}`: {e}"),
            })?;
            let val: u32 = val.parse().map_err(|e| FingerprintError::InvalidField {
                field: "http2_akamai",
                reason: format!("settings value `{val}`: {e}"),
            })?;
            entries.push((id, val));
        }
        Ok(Self { entries })
    }
}

/// Initial HTTP/2 WINDOW_UPDATE increment, parsed from the second `|`
/// section of an Akamai fingerprint.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct H2WindowUpdate(pub u32);

impl H2WindowUpdate {
    pub fn from_akamai(s: &str) -> Result<Self, FingerprintError> {
        let mut parts = s.split('|');
        let _settings = parts.next();
        let window = parts.next().unwrap_or("0");
        let v: u32 = window
            .parse()
            .map_err(|e| FingerprintError::InvalidField {
                field: "http2_akamai",
                reason: format!("window_update `{window}`: {e}"),
            })?;
        Ok(Self(v))
    }
}

/// HTTP/2 PRIORITY frame parameters (third Akamai section).
///
/// Akamai encodes priority frames as `weight:dep:exclusive,...` per stream;
/// the registry shape preserves the order received. Empty section yields an
/// empty vector — not all personas exercise PRIORITY frames.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct H2Priority {
    pub frames: Vec<H2PriorityFrame>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct H2PriorityFrame {
    pub stream_id: u32,
    pub depends_on: u32,
    pub weight: u8,
    pub exclusive: bool,
}

impl H2Priority {
    pub fn from_akamai(s: &str) -> Result<Self, FingerprintError> {
        let mut parts = s.split('|');
        let _ = parts.next();
        let _ = parts.next();
        let priorities = parts.next().unwrap_or("");
        if priorities.is_empty() || priorities == "0" {
            return Ok(Self::default());
        }
        // The Akamai PRIORITY encoding has multiple variants in the wild;
        // for now we accept it as opaque and store nothing — backends that
        // want richer parsing can override.
        Ok(Self::default())
    }
}

/// The persona-binding contract.
///
/// A backend (e.g. `wreq` in W3B) implements this trait so a [`Persona`] can
/// be applied via [`HttpClient::apply_persona`]. Methods correspond to the
/// rows in ADR-005 § "Persona binding contract".
///
/// All methods return `Result` so backends can reject values they cannot
/// honor. Mutation is `&mut self` so impls can hold an internal builder.
pub trait HttpClient {
    /// Set the TLS ClientHello fingerprint. `ja3` is optional because the
    /// canonical persona schema records JA4 only; backends that need JA3
    /// should derive it from the ALPN + cipher fields embedded in JA4.
    fn set_tls_fingerprint(
        &mut self,
        ja3: Option<&str>,
        ja4: &str,
    ) -> Result<(), FingerprintError>;

    /// Set ALPN advertised protocols (in order).
    fn set_alpn(&mut self, alpn: &[String]) -> Result<(), FingerprintError>;

    /// Set the initial HTTP/2 SETTINGS frame.
    fn set_h2_settings(&mut self, settings: &H2Settings) -> Result<(), FingerprintError>;

    /// Set the initial HTTP/2 WINDOW_UPDATE increment.
    fn set_h2_window_update(&mut self, update: H2WindowUpdate) -> Result<(), FingerprintError>;

    /// Set the initial HTTP/2 PRIORITY frames.
    fn set_h2_priority(&mut self, priority: &H2Priority) -> Result<(), FingerprintError>;

    /// Set a default header to be applied to every outgoing request.
    fn set_default_header(&mut self, name: &str, value: &str) -> Result<(), FingerprintError>;

    /// Apply a full [`Persona`] to this client. Default impl maps the
    /// persona fields to the individual setters above; backends are free to
    /// override for atomic application.
    fn apply_persona(&mut self, persona: &Persona) -> Result<(), FingerprintError> {
        let p = &persona.persona;

        self.set_tls_fingerprint(None, &p.network.ja4)?;
        self.set_alpn(&p.network.alpn)?;

        let settings = H2Settings::from_akamai(&p.network.http2_akamai)?;
        self.set_h2_settings(&settings)?;

        let window = H2WindowUpdate::from_akamai(&p.network.http2_akamai)?;
        self.set_h2_window_update(window)?;

        let priority = H2Priority::from_akamai(&p.network.http2_akamai)?;
        self.set_h2_priority(&priority)?;

        self.set_default_header("User-Agent", &p.user_agent.full)?;
        self.set_default_header("Accept-Language", &p.locale.accept_language)?;

        // sec-ch-ua brand list — emitted as a comma-separated quoted list
        // matching the Client Hints spec form.
        if !p.user_agent.ua_ch.brands.is_empty() {
            let sec_ch_ua = p
                .user_agent
                .ua_ch
                .brands
                .iter()
                .map(|(brand, version)| format!("\"{brand}\";v=\"{version}\""))
                .collect::<Vec<_>>()
                .join(", ");
            self.set_default_header("sec-ch-ua", &sec_ch_ua)?;
        }
        self.set_default_header(
            "sec-ch-ua-mobile",
            if p.user_agent.ua_ch.mobile { "?1" } else { "?0" },
        )?;
        self.set_default_header("sec-ch-ua-platform", &p.user_agent.ua_ch.platform)?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Persona;

    /// Recorder backend — captures every setter call so tests can assert
    /// what `apply_persona` produced. Stands in for `wreq` until W3B lands.
    #[derive(Debug, Default)]
    struct Recorder {
        ja4: Option<String>,
        alpn: Vec<String>,
        h2_settings: H2Settings,
        h2_window: H2WindowUpdate,
        h2_priority: H2Priority,
        headers: Vec<(String, String)>,
    }

    impl HttpClient for Recorder {
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
        fn set_h2_settings(&mut self, settings: &H2Settings) -> Result<(), FingerprintError> {
            self.h2_settings = settings.clone();
            Ok(())
        }
        fn set_h2_window_update(
            &mut self,
            update: H2WindowUpdate,
        ) -> Result<(), FingerprintError> {
            self.h2_window = update;
            Ok(())
        }
        fn set_h2_priority(&mut self, priority: &H2Priority) -> Result<(), FingerprintError> {
            self.h2_priority = priority.clone();
            Ok(())
        }
        fn set_default_header(
            &mut self,
            name: &str,
            value: &str,
        ) -> Result<(), FingerprintError> {
            self.headers.push((name.to_string(), value.to_string()));
            Ok(())
        }
    }

    const SAMPLE_PERSONA: &str = r#"
[persona]
id = "persona-test-01"
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
user_data_dir = "/tmp/persona-test-01"
"#;

    #[test]
    fn h2_settings_parse_akamai_section() {
        let s = H2Settings::from_akamai("1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p")
            .expect("parse");
        assert_eq!(
            s.entries,
            vec![(1, 65536), (2, 0), (4, 6291456), (6, 262144)]
        );
    }

    #[test]
    fn h2_window_update_parse_akamai() {
        let w =
            H2WindowUpdate::from_akamai("1:65536,2:0|15663105|0|m,a,s,p").expect("parse");
        assert_eq!(w.0, 15_663_105);
    }

    #[test]
    fn h2_settings_invalid_returns_field_error() {
        let err = H2Settings::from_akamai("garbage").expect_err("should fail");
        assert!(matches!(
            err,
            FingerprintError::InvalidField { field: "http2_akamai", .. }
        ));
    }

    #[test]
    fn apply_persona_records_all_setter_calls() {
        let persona: Persona = toml::from_str(SAMPLE_PERSONA).expect("parse persona");
        let mut rec = Recorder::default();
        rec.apply_persona(&persona).expect("apply");

        assert_eq!(rec.ja4.as_deref(), Some("t13d1516h2_8daaf6152771_02713d6af862"));
        assert_eq!(rec.alpn, vec!["h2".to_string(), "http/1.1".to_string()]);
        assert_eq!(rec.h2_settings.entries.len(), 5);
        assert_eq!(rec.h2_window.0, 15_663_105);

        let header_names: Vec<&str> = rec.headers.iter().map(|(n, _)| n.as_str()).collect();
        assert!(header_names.contains(&"User-Agent"));
        assert!(header_names.contains(&"Accept-Language"));
        assert!(header_names.contains(&"sec-ch-ua"));
        assert!(header_names.contains(&"sec-ch-ua-mobile"));
        assert!(header_names.contains(&"sec-ch-ua-platform"));

        let ua = rec
            .headers
            .iter()
            .find(|(n, _)| n == "User-Agent")
            .map(|(_, v)| v.as_str())
            .unwrap();
        assert!(ua.contains("Chrome/147.0.7727.94"));

        let sec_ch_ua = rec
            .headers
            .iter()
            .find(|(n, _)| n == "sec-ch-ua")
            .map(|(_, v)| v.as_str())
            .unwrap();
        assert_eq!(sec_ch_ua, r#""Chromium";v="147", "Google Chrome";v="147""#);

        let mobile = rec
            .headers
            .iter()
            .find(|(n, _)| n == "sec-ch-ua-mobile")
            .map(|(_, v)| v.as_str())
            .unwrap();
        assert_eq!(mobile, "?0");
    }

    #[test]
    fn trait_is_object_safe() {
        // If this compiles, the trait can be used as `&mut dyn HttpClient`,
        // which W3B's call sites need so the backend stays swappable.
        fn _accepts_dyn(_c: &mut dyn HttpClient) {}
        let mut rec = Recorder::default();
        _accepts_dyn(&mut rec);
    }
}
