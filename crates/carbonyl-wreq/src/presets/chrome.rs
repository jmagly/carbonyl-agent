//! Chrome family presets.
//!
//! `CHROME_148_DESKTOP` was populated from the
//! `chrome-148-desktop.client_hello.bin` fixture captured 2026-05-16
//! (real Google Chrome 148.0.7778.167 on Linux x86_64; see
//! `crates/carbonyl-wreq/data/fixtures/chrome-148-desktop.metadata.toml`).
//!
//! `CHROME_147_DESKTOP` is intentionally absent. The persona spec
//! declares Chrome 147 but Chrome 147 stable is no longer available
//! from Google's apt repo. Lookups for `(Chrome, 147, Desktop)` fall
//! through to `CHROME_148_DESKTOP` via `presets::preset_for`'s
//! nearest-neighbor — wire fingerprint between Chrome 147 and 148 is
//! near-identical (same TLS profile family; possible bump in
//! `extension_permutation_indices` ordering only).
//!
//! `CHROME_147_MOBILE_ANDROID` lands in Iteration B item 1a (HITL:
//! Chrome on Android device or emulator). See `fixtures-plan.md`.

use carbonyl_fingerprint::schema::BrowserFamily;

use super::{BrowserVersion, H2Profile, HeaderProfile, Platform, PresetTable, TlsProfile};

/// Real Chrome 148.0.7778.167 desktop on Linux x86_64.
///
/// Provenance: `data/fixtures/chrome-148-desktop.client_hello.bin`
/// SHA-256 `e5975d6805ad8743e5acf0c38c5867c5a961f739db3716ff368fd77cd85f5d73`
/// captured 2026-05-16 via `tests/capture_real_browser.rs::capture_chrome_desktop`.
///
/// h2 SETTINGS values come from the persona spec's `network.http2_akamai`
/// (Chrome 147 ground truth) — the capture's TLS handshake didn't
/// complete (responder cert untrusted in headless=new) so h2 frames
/// weren't reached. The persona-declared values are accepted as the
/// h2 backstop until a fixture with a system-trusted cert lands.
pub static CHROME_148_DESKTOP: PresetTable = PresetTable {
    family: BrowserFamily::Chrome,
    version: BrowserVersion {
        major: 148,
        minor: 0,
    },
    platform: Platform::Desktop,
    tls: TlsProfile {
        // Chrome 110+ permutes extension order; letting wreq drive the
        // permutation matches Chrome's randomization rather than fixing
        // a single capture's order. The captured order from
        // chrome-148-desktop.client_hello.bin was:
        //   GREASE, 0x0005, 0x002b, 0xff01, 0x000b, 0x0023, 0xfe0d,
        //   0x0033, 0x002d, 0x0012, 0x0017, 0x000d, 0x0010, 0x001b,
        //   0x000a, 0x44cd, GREASE
        extension_permutation_indices: None,
        // Cipher list captured byte-for-byte from real Chrome 148.
        // GREASE entries (0x?A?A) stripped — wreq inserts its own.
        // Order preserved.
        cipher_list: Some(concat!(
            "TLS_AES_128_GCM_SHA256:",
            "TLS_AES_256_GCM_SHA384:",
            "TLS_CHACHA20_POLY1305_SHA256:",
            "ECDHE-ECDSA-AES128-GCM-SHA256:",
            "ECDHE-RSA-AES128-GCM-SHA256:",
            "ECDHE-ECDSA-AES256-GCM-SHA384:",
            "ECDHE-RSA-AES256-GCM-SHA384:",
            "ECDHE-ECDSA-CHACHA20-POLY1305:",
            "ECDHE-RSA-CHACHA20-POLY1305:",
            "ECDHE-RSA-AES128-SHA:",
            "ECDHE-RSA-AES256-SHA:",
            "AES128-GCM-SHA256:",
            "AES256-GCM-SHA384:",
            "AES128-SHA:",
            "AES256-SHA"
        )),
        alpn_default: &["h2", "http/1.1"],
        grease_enabled: Some(true),
        permute_extensions: Some(true),
        // Captured supported_groups (GREASE 0x8a8a stripped):
        //   0x11ec X25519MLKEM768 (post-quantum hybrid — Chrome 148 default!)
        //   0x001d X25519
        //   0x0017 secp256r1
        //   0x0018 secp384r1
        // Note: 0x11ec is recent (post-quantum); wreq's SslCurve may
        // not enumerate it yet. The build-time translation in
        // client.rs::persona_to_emulation_provider should map known
        // values and skip unknowns with a tracing::debug.
        supported_groups: &[0x11ec, 0x001d, 0x0017, 0x0018],
        // Captured signature algorithms in OpenSSL names:
        sigalgs_list: Some(concat!(
            "ecdsa_secp256r1_sha256:",
            "rsa_pss_rsae_sha256:",
            "rsa_pkcs1_sha256:",
            "ecdsa_secp384r1_sha384:",
            "rsa_pss_rsae_sha384:",
            "rsa_pkcs1_sha384:",
            "rsa_pss_rsae_sha512:",
            "rsa_pkcs1_sha512"
        )),
    },
    h2: H2Profile {
        // From persona spec network.http2_akamai for Chrome 147:
        //   "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
        settings_default: &[
            (0x01, 65536),   // HEADER_TABLE_SIZE
            (0x02, 0),       // ENABLE_PUSH
            (0x03, 1000),    // MAX_CONCURRENT_STREAMS
            (0x04, 6291456), // INITIAL_WINDOW_SIZE
            (0x06, 262144),  // MAX_HEADER_LIST_SIZE
        ],
        initial_connection_window: 15663105,
        // Chrome's pseudo-header order (per Akamai 4th section "m,a,s,p"):
        pseudo_header_order: &[":method", ":authority", ":scheme", ":path"],
    },
    headers: HeaderProfile {
        // Standard Chrome navigation request header order. The persona
        // contributes User-Agent, Accept-Language, sec-ch-ua trio
        // (Chrome family); preset fills in the remaining always-on
        // headers in the standard Chrome order.
        default_order: &[
            "host",
            "connection",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "upgrade-insecure-requests",
            "user-agent",
            "accept",
            "sec-fetch-site",
            "sec-fetch-mode",
            "sec-fetch-user",
            "sec-fetch-dest",
            "accept-encoding",
            "accept-language",
            "priority",
        ],
        static_defaults: &[
            (
                "accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            ),
            ("accept-encoding", "gzip, deflate, br, zstd"),
            ("upgrade-insecure-requests", "1"),
            ("sec-fetch-site", "none"),
            ("sec-fetch-mode", "navigate"),
            ("sec-fetch-user", "?1"),
            ("sec-fetch-dest", "document"),
            ("priority", "u=0, i"),
        ],
    },
    provenance_id: "chrome-148-desktop",
};
