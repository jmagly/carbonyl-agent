//! Firefox family presets.
//!
//! `FIREFOX_150_DESKTOP` was populated from the
//! `firefox-150-desktop.client_hello.bin` fixture captured 2026-05-17
//! (real Mozilla Firefox 150.0.3 native build on Linux x86_64; see
//! `crates/carbonyl-wreq/data/fixtures/firefox-150-desktop.metadata.toml`).
//!
//! Firefox differs from Chrome in three observable ways at this layer:
//! 1. No GREASE — Firefox does not emit GREASE cipher/extension entries.
//! 2. No extension permutation — Firefox emits extensions in a fixed
//!    order across connections (Chrome 110+ randomizes).
//! 3. Pseudo-header order is `:method, :path, :authority, :scheme`
//!    rather than Chrome's `:method, :authority, :scheme, :path`.

use carbonyl_fingerprint::schema::BrowserFamily;

use super::{BrowserVersion, H2Profile, HeaderProfile, Platform, PresetTable, TlsProfile};

/// Real Mozilla Firefox 150.0.3 desktop on Linux x86_64.
///
/// Provenance: `data/fixtures/firefox-150-desktop.client_hello.bin`
/// SHA-256 `3416dc48ef2cf51946508a8a4e2925a1c4d8b9404f0b159aab987df5e83f5fd5`
/// captured 2026-05-17 via `tests/capture_real_browser.rs::capture_firefox_desktop`.
///
/// h2 SETTINGS values come from the persona spec's `network.http2_akamai`
/// (`1:65536,4:131072,5:16384|12517377|0|m,p,a,s` — Firefox 150 ground
/// truth declared in the conformance fixture) because the capture's
/// TLS handshake didn't complete (responder cert untrusted by Firefox)
/// so h2 frames weren't reached. Persona-declared values are accepted
/// as the h2 backstop until a fixture with a Firefox-trusted cert lands.
pub static FIREFOX_150_DESKTOP: PresetTable = PresetTable {
    family: BrowserFamily::Firefox,
    version: BrowserVersion {
        major: 150,
        minor: 0,
    },
    platform: Platform::Desktop,
    tls: TlsProfile {
        // Firefox emits extensions in a fixed order (no permutation).
        // The captured order from firefox-150-desktop.client_hello.bin was:
        //   0x0017 (extended_master_secret), 0xff01 (renegotiation_info),
        //   0x000a (supported_groups), 0x000b (ec_point_formats),
        //   0x0023 (session_ticket), 0x0010 (alpn), 0x0005 (status_request),
        //   0x0022 (delegated_credentials), 0x0012 (signed_certificate_timestamp),
        //   0x0033 (key_share), 0x002b (supported_versions),
        //   0x000d (signature_algorithms), 0x002d (psk_key_exchange_modes),
        //   0x001c (record_size_limit), 0x001b (compress_certificate),
        //   0xfe0d (encrypted_client_hello)
        extension_permutation_indices: None,
        // Cipher list captured byte-for-byte from real Firefox 150.0.3.
        // No GREASE entries (Firefox doesn't emit GREASE). Order preserved.
        cipher_list: Some(concat!(
            "TLS_AES_128_GCM_SHA256:",
            "TLS_CHACHA20_POLY1305_SHA256:",
            "TLS_AES_256_GCM_SHA384:",
            "ECDHE-ECDSA-AES128-GCM-SHA256:",
            "ECDHE-RSA-AES128-GCM-SHA256:",
            "ECDHE-ECDSA-CHACHA20-POLY1305:",
            "ECDHE-RSA-CHACHA20-POLY1305:",
            "ECDHE-ECDSA-AES256-GCM-SHA384:",
            "ECDHE-RSA-AES256-GCM-SHA384:",
            "ECDHE-ECDSA-AES256-SHA:",
            "ECDHE-RSA-AES128-SHA:",
            "ECDHE-RSA-AES256-SHA:",
            "AES128-GCM-SHA256:",
            "AES256-GCM-SHA384:",
            "AES128-SHA:",
            "AES256-SHA",
        )),
        alpn_default: &["h2", "http/1.1"],
        // Firefox does NOT emit GREASE.
        grease_enabled: Some(false),
        // Firefox does NOT permute extensions (fixed order).
        permute_extensions: Some(false),
        // Captured supported groups (no GREASE): X25519MLKEM768, x25519,
        // secp256r1, secp384r1, secp521r1, ffdhe2048, ffdhe3072.
        supported_groups: &[0x11ec, 0x001d, 0x0017, 0x0018, 0x0019, 0x0100, 0x0101],
        // Signature algorithms captured byte-for-byte from real Firefox 150.0.3.
        sigalgs_list: Some(concat!(
            "ecdsa_secp256r1_sha256:",
            "ecdsa_secp384r1_sha384:",
            "ecdsa_secp521r1_sha512:",
            "rsa_pss_rsae_sha256:",
            "rsa_pss_rsae_sha384:",
            "rsa_pss_rsae_sha512:",
            "rsa_pkcs1_sha256:",
            "rsa_pkcs1_sha384:",
            "rsa_pkcs1_sha512:",
            "ecdsa_sha1:",
            "rsa_pkcs1_sha1",
        )),
    },
    h2: H2Profile {
        // Firefox 150 h2 SETTINGS from persona declaration
        // (1:65536, 4:131072, 5:16384). Persona's window_update is
        // 12517377 (vs Chrome's 15663105). Pseudo-header order
        // `m,p,a,s` differs from Chrome's `m,a,s,p`.
        settings_default: &[(0x01, 65536), (0x04, 131072), (0x05, 16384)],
        initial_connection_window: 12517377,
        pseudo_header_order: &[":method", ":path", ":authority", ":scheme"],
    },
    headers: HeaderProfile {
        // Firefox 150 default header emission order. Differs from
        // Chrome — Firefox does not emit sec-ch-ua-* headers.
        default_order: &[
            "host",
            "user-agent",
            "accept",
            "accept-language",
            "accept-encoding",
            "upgrade-insecure-requests",
        ],
        static_defaults: &[
            (
                "accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            ),
            ("accept-encoding", "gzip, deflate, br, zstd"),
            ("upgrade-insecure-requests", "1"),
        ],
    },
    provenance_id: "firefox-150-desktop",
};
