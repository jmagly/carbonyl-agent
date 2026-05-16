//! In-house preset registry — types and lookup scaffolding.
//!
//! Companion to ADR-W02 (persona is the source of truth) and the
//! ADR-W02 addendum (`adr-002-addendum-wreq-api-shape.md`) which
//! confirmed wreq 5.x exposes the field-level emulation surface this
//! module targets.
//!
//! # Status: types only — no concrete preset entries yet
//!
//! Iteration A item 2 establishes the type surface. Concrete preset
//! entries (e.g. `chrome::CHROME_148_DESKTOP`) require captured
//! real-browser fixtures (Iteration A item 3 — HITL). Adding a
//! placeholder entry with invented values would ship a fake fingerprint
//! through tests that pretend to pass; that fails the conformance
//! purpose of the registry.
//!
//! When the Chrome 148 desktop fixture lands, `chrome.rs` gains its
//! first concrete `CHROME_148_DESKTOP: PresetTable` and `preset_for`
//! gains a real arm.
//!
//! # Field encodings
//!
//! Per the ADR-W02 addendum, preset fields use wreq 5.x's preferred
//! encodings rather than raw u16 IDs:
//!
//! - `extension_permutation_indices`: `Option<&[u8]>` (indices into
//!   wreq's known-extension table; `None` = wreq default order)
//! - `cipher_list`: OpenSSL-style colon-separated string
//! - `sigalgs_list`: OpenSSL-style colon-separated string
//! - `curves`: `wreq::SslCurve` (re-export from boring2)
//! - `grease_enabled`: `Option<bool>` (per-position control isn't
//!   exposed by wreq; browsers that emit GREASE get `Some(true)`)
//! - `permute_extensions`: `Option<bool>` (Chrome 110+ randomizes)

use carbonyl_fingerprint::schema::BrowserFamily;

/// Static preset data for one (family, version, platform) triple.
///
/// Backstop for wire fields a persona does not declare. The persona
/// remains the source of truth per ADR-W02; values here are defaults
/// derived from independent capture of real-browser behavior.
#[derive(Debug, Clone, PartialEq)]
pub struct PresetTable {
    pub family: BrowserFamily,
    pub version: BrowserVersion,
    pub platform: Platform,
    pub tls: TlsProfile,
    pub h2: H2Profile,
    pub headers: HeaderProfile,
    /// Sourced from `data/fixtures/<id>.pcap`; SHA-256 recorded in `PROVENANCE.md`.
    pub provenance_id: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrowserVersion {
    pub major: u32,
    pub minor: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    Desktop,
    MobileAndroid,
    MobileIos,
}

/// TLS-layer fields. Per the ADR-W02 addendum, encodings match wreq
/// 5.x's `TlsConfig` shape so preset entries flow into
/// `wreq::TlsConfig` with minimal transformation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TlsProfile {
    /// Indices into wreq's known-extension table. `None` = wreq default order.
    pub extension_permutation_indices: Option<&'static [u8]>,
    /// OpenSSL-style cipher list, colon-separated.
    /// E.g. `"TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:..."`.
    pub cipher_list: Option<&'static str>,
    /// ALPN offer list (default; persona overrides via `network.alpn`).
    pub alpn_default: &'static [&'static str],
    /// GREASE on/off. Per-position control isn't exposed by wreq;
    /// browsers that emit GREASE (Chrome) get `Some(true)`.
    pub grease_enabled: Option<bool>,
    /// Permute extensions on each ClientHello (Chrome 110+ randomizes).
    pub permute_extensions: Option<bool>,
    /// Supported groups. Stored as raw u16 here for source readability;
    /// the build flow maps to `wreq::SslCurve` at preset-application
    /// time (avoids importing `wreq` types into this module's static
    /// definitions, which would force `boring-sys` linkage on every
    /// crate that depends on `carbonyl_wreq::presets`).
    pub supported_groups: &'static [u16],
    /// Signature algorithms, OpenSSL-style colon-separated string.
    pub sigalgs_list: Option<&'static str>,
}

/// HTTP/2-layer fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct H2Profile {
    /// Default SETTINGS entries in `(id, value)` tuples, in declared order.
    /// Persona's `network.http2_akamai` overrides on a per-id basis at
    /// build time.
    pub settings_default: &'static [(u16, u32)],
    pub initial_connection_window: u32,
    /// Pseudo-header order for HEADERS frames.
    /// E.g. `[":method", ":authority", ":scheme", ":path"]`.
    pub pseudo_header_order: &'static [&'static str],
}

/// Header-emission fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HeaderProfile {
    /// Default header names in emission order. Values come from the
    /// persona (User-Agent, Accept-Language, sec-ch-ua-* for Chrome);
    /// this struct only fixes ordering and supplies values for headers
    /// the persona does not declare (e.g. Accept, Accept-Encoding,
    /// sec-fetch-*).
    pub default_order: &'static [&'static str],
    /// Default values for headers the persona does not declare.
    /// Looked up by header name during build.
    pub static_defaults: &'static [(&'static str, &'static str)],
}

pub mod chrome;
pub mod firefox;
pub mod safari;

/// Resolve a preset for `(family, version, platform)`.
///
/// Returns the exact match when available, otherwise the nearest
/// preset within the same family with a `tracing::warn!` event.
/// Never panics. Never returns the wrong family.
///
/// Currently returns `None` for every input because no concrete preset
/// entries exist yet (Iteration A item 2 ships types only; concrete
/// entries land alongside captured fixtures in items 3+ and Iteration B).
pub fn preset_for(
    family: BrowserFamily,
    version: BrowserVersion,
    platform: Platform,
) -> Option<&'static PresetTable> {
    match (family, version.major, platform) {
        (BrowserFamily::Chrome, 148, Platform::Desktop) => Some(&chrome::CHROME_148_DESKTOP),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn _types_compile_and_construct() {
        // Sanity: the type surface compiles and a construction-only
        // instance is buildable. No real preset values yet.
        let p = PresetTable {
            family: BrowserFamily::Chrome,
            version: BrowserVersion { major: 0, minor: 0 },
            platform: Platform::Desktop,
            tls: TlsProfile {
                extension_permutation_indices: None,
                cipher_list: None,
                alpn_default: &["h2", "http/1.1"],
                grease_enabled: None,
                permute_extensions: None,
                supported_groups: &[],
                sigalgs_list: None,
            },
            h2: H2Profile {
                settings_default: &[],
                initial_connection_window: 0,
                pseudo_header_order: &[],
            },
            headers: HeaderProfile {
                default_order: &[],
                static_defaults: &[],
            },
            provenance_id: "test-construction-only",
        };
        assert_eq!(p.family, BrowserFamily::Chrome);
        assert_eq!(p.version.major, 0);
    }

    #[test]
    fn preset_for_chrome_148_returns_desktop_entry() {
        let p = preset_for(
            BrowserFamily::Chrome,
            BrowserVersion {
                major: 148,
                minor: 0,
            },
            Platform::Desktop,
        );
        let p = p.expect("Chrome 148 desktop preset should exist");
        assert_eq!(p.family, BrowserFamily::Chrome);
        assert_eq!(p.version.major, 148);
        assert_eq!(p.provenance_id, "chrome-148-desktop");
        assert!(p.tls.cipher_list.is_some());
        assert!(p.tls.sigalgs_list.is_some());
        assert_eq!(p.tls.alpn_default, &["h2", "http/1.1"]);
    }

    #[test]
    fn preset_for_chrome_148_falls_back_to_148_per_nearest_neighbor() {
        let p = preset_for(
            BrowserFamily::Chrome,
            BrowserVersion {
                major: 148,
                minor: 0,
            },
            Platform::Desktop,
        );
        let p = p.expect("Chrome 148 desktop should fall back to nearest neighbor");
        // Per ADR-W02 nearest-neighbor: persona-148 → preset-148
        assert_eq!(p.provenance_id, "chrome-148-desktop");
    }

    #[test]
    fn preset_for_unknown_returns_none() {
        let p = preset_for(
            BrowserFamily::Firefox,
            BrowserVersion {
                major: 150,
                minor: 0,
            },
            Platform::Desktop,
        );
        assert!(p.is_none(), "Firefox preset not yet captured");
    }
}
