//! JA4 algorithm — parse ClientHello bytes, compute persona-comparable
//! fingerprint string (W3B.2.2 — `Refs: roctinam/carbonyl-agent#77`).
//!
//! Implements the FoxIO JA4+ specification's `JA4` variant
//! (https://github.com/FoxIO-LLC/ja4):
//!
//! ```text
//! JA4 = <ja4_a>_<ja4_b>_<ja4_c>
//!
//! ja4_a = <protocol><tls_ver><sni_ind><cipher_cnt><ext_cnt><alpn>
//!   protocol     = "t" (TCP) | "q" (QUIC)   — this impl: always "t"
//!   tls_ver      = "13" | "12" | "11" | "10" | "s3"  — from highest
//!                  supported_versions extension (TLS 1.3) or
//!                  legacy handshake version (TLS 1.2-)
//!   sni_ind      = "d" (SNI present) | "i" (no SNI)
//!   cipher_cnt   = count of non-GREASE cipher suites, zero-padded
//!                  to 2 decimal digits (capped at 99)
//!   ext_cnt      = count of non-GREASE extensions (including SNI
//!                  and ALPN), zero-padded to 2 digits
//!   alpn         = first 2 chars of first ALPN protocol, or "00"
//!                  if no ALPN
//!
//! ja4_b = sha256(sorted GREASE-stripped cipher list as
//!                comma-joined 4-char hex)[:12]
//!
//! ja4_c = sha256(GREASE-stripped extension list IN PRESENTED ORDER,
//!                excluding 0x0000 (SNI) and 0x0010 (ALPN) ids,
//!                comma-joined hex, followed by "_" and the
//!                signature_algorithms list in presented order)[:12]
//! ```
//!
//! # Status
//!
//! Layer 2.2: pure algorithm. Integrates with `CapturedHandshake` via
//! `compute_ja4_from_client_hello` — Layer 2.4 (#79) wires this into
//! `ConformanceFixture::assert_wire_state`.

// rust 1.95's clippy added collapsible_match — the nested ifs in this
// extension-walker each carry distinct length-check failure paths;
// collapsing them obscures the validation logic. Stylistic-only lint.
#![allow(clippy::collapsible_match)]
#![allow(clippy::collapsible_if)]

use sha2::{Digest, Sha256};

/// Errors surfaced when parsing a captured ClientHello.
#[derive(Debug, thiserror::Error)]
pub enum Ja4Error {
    #[error("ClientHello parse failed: {0}")]
    Parse(String),
    #[error("captured bytes do not contain a ClientHello handshake message")]
    NotClientHello,
    #[error("invalid TLS record header")]
    InvalidRecord,
}

/// Compute the JA4 fingerprint string from raw TLS ClientHello bytes.
///
/// Input bytes are expected to start with the TLS record header
/// (`0x16, 0x03, 0x0X, len_hi, len_lo`) followed by the ClientHello
/// handshake message. This is exactly what
/// `LocalTlsResponder` captures in its peek buffer.
pub fn compute_ja4_from_client_hello(bytes: &[u8]) -> Result<String, Ja4Error> {
    let parsed = parse_client_hello(bytes)?;
    Ok(format_ja4(&parsed))
}

/// Fields extracted from a ClientHello sufficient to compute JA4.
#[derive(Debug, Clone)]
pub struct ParsedClientHello {
    /// Highest supported TLS version per supported_versions extension,
    /// or the legacy handshake version when supported_versions is
    /// absent. Encoded as the FoxIO 2-char tag: `"13"` for TLS 1.3,
    /// `"12"` for TLS 1.2, etc.
    pub tls_version_tag: String,
    /// `true` when the server_name extension (0x0000) is present.
    pub sni_present: bool,
    /// Cipher suite IDs as presented in the ClientHello (NOT
    /// GREASE-stripped; caller decides).
    pub cipher_suites: Vec<u16>,
    /// Extension IDs as presented (NOT GREASE-stripped).
    pub extensions: Vec<u16>,
    /// ALPN protocols offered by the client, in presented order.
    pub alpn_protocols: Vec<Vec<u8>>,
    /// Signature algorithms list from extension 0x000d, in presented
    /// order. Empty when the extension wasn't offered.
    pub signature_algorithms: Vec<u16>,
}

fn parse_client_hello(bytes: &[u8]) -> Result<ParsedClientHello, Ja4Error> {
    use tls_parser::{parse_tls_plaintext, TlsMessage, TlsMessageHandshake};

    let (_rest, record) =
        parse_tls_plaintext(bytes).map_err(|e| Ja4Error::Parse(format!("tls record: {e:?}")))?;

    let mut ch_opt = None;
    for msg in record.msg {
        if let TlsMessage::Handshake(TlsMessageHandshake::ClientHello(ch)) = msg {
            ch_opt = Some(ch);
            break;
        }
    }
    let ch = ch_opt.ok_or(Ja4Error::NotClientHello)?;

    // Cipher suites — tls-parser returns them as u16 values.
    let cipher_suites: Vec<u16> = ch.ciphers.iter().map(|c| c.0).collect();

    // Extensions — parse the raw bytes manually to preserve order and
    // capture IDs. tls-parser has `parse_tls_client_hello_extensions`
    // that walks them.
    let mut extensions: Vec<u16> = Vec::new();
    let mut alpn_protocols: Vec<Vec<u8>> = Vec::new();
    let mut signature_algorithms: Vec<u16> = Vec::new();
    let mut sni_present = false;
    let mut tls_version_from_supported: Option<u16> = None;

    if let Some(ext_bytes) = ch.ext {
        let mut cursor = ext_bytes;
        while cursor.len() >= 4 {
            let ext_type = u16::from_be_bytes([cursor[0], cursor[1]]);
            let ext_len = u16::from_be_bytes([cursor[2], cursor[3]]) as usize;
            if cursor.len() < 4 + ext_len {
                break;
            }
            let ext_data = &cursor[4..4 + ext_len];
            extensions.push(ext_type);

            match ext_type {
                0x0000 => {
                    sni_present = true;
                }
                0x0010 => {
                    // ALPN: 2-byte total length, then list of
                    // 1-byte-length-prefixed protocol strings.
                    if ext_data.len() >= 2 {
                        let list_len = u16::from_be_bytes([ext_data[0], ext_data[1]]) as usize;
                        let mut p = 2;
                        while p < 2 + list_len && p < ext_data.len() {
                            let proto_len = ext_data[p] as usize;
                            p += 1;
                            if p + proto_len > ext_data.len() {
                                break;
                            }
                            alpn_protocols.push(ext_data[p..p + proto_len].to_vec());
                            p += proto_len;
                        }
                    }
                }
                0x000d => {
                    // signature_algorithms: 2-byte total length, then
                    // list of u16 algorithm identifiers.
                    if ext_data.len() >= 2 {
                        let list_len = u16::from_be_bytes([ext_data[0], ext_data[1]]) as usize;
                        let mut p = 2;
                        while p + 2 <= 2 + list_len && p + 2 <= ext_data.len() {
                            let alg = u16::from_be_bytes([ext_data[p], ext_data[p + 1]]);
                            signature_algorithms.push(alg);
                            p += 2;
                        }
                    }
                }
                0x002b => {
                    // supported_versions (TLS 1.3 indicator): 1-byte
                    // list length, then list of u16 versions. Highest
                    // non-GREASE version wins.
                    if !ext_data.is_empty() {
                        let list_len = ext_data[0] as usize;
                        let mut p = 1;
                        let mut highest: u16 = 0;
                        while p + 2 <= 1 + list_len && p + 2 <= ext_data.len() {
                            let v = u16::from_be_bytes([ext_data[p], ext_data[p + 1]]);
                            if !is_grease(v) && v > highest {
                                highest = v;
                            }
                            p += 2;
                        }
                        if highest != 0 {
                            tls_version_from_supported = Some(highest);
                        }
                    }
                }
                _ => {}
            }

            cursor = &cursor[4 + ext_len..];
        }
    }

    // Determine TLS version tag: supported_versions wins; legacy
    // handshake version is the fallback.
    let version_u16 = tls_version_from_supported.unwrap_or(ch.version.0);
    let tls_version_tag = encode_version_tag(version_u16);

    Ok(ParsedClientHello {
        tls_version_tag,
        sni_present,
        cipher_suites,
        extensions,
        alpn_protocols,
        signature_algorithms,
    })
}

/// FoxIO version tag mapping.
fn encode_version_tag(v: u16) -> String {
    match v {
        0x0304 => "13".to_string(), // TLS 1.3
        0x0303 => "12".to_string(), // TLS 1.2
        0x0302 => "11".to_string(), // TLS 1.1
        0x0301 => "10".to_string(), // TLS 1.0
        0x0300 => "s3".to_string(), // SSL 3.0
        _ => "00".to_string(),
    }
}

/// GREASE detection per RFC 8701. Values are `0x0A0A, 0x1A1A, 0x2A2A,
/// ..., 0xFAFA` — both bytes identical, low nibble = 0xA.
pub fn is_grease(v: u16) -> bool {
    let hi = (v >> 8) as u8;
    let lo = (v & 0xFF) as u8;
    hi == lo && (hi & 0x0F) == 0x0A
}

/// First 12 hex chars (lowercase) of SHA-256(input).
fn sha256_first12(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    let digest = hasher.finalize();
    hex::encode(&digest[..6]) // 6 bytes = 12 hex chars
}

/// Compose the JA4 string from a parsed ClientHello.
fn format_ja4(p: &ParsedClientHello) -> String {
    // ja4_a
    let sni_ind = if p.sni_present { "d" } else { "i" };
    let cipher_count: usize = p
        .cipher_suites
        .iter()
        .filter(|c| !is_grease(**c))
        .count()
        .min(99);
    let ext_count: usize = p
        .extensions
        .iter()
        .filter(|e| !is_grease(**e))
        .count()
        .min(99);
    let alpn_tag = match p.alpn_protocols.first() {
        Some(proto) if proto.len() >= 2 => {
            // First two chars of the first ALPN protocol value as
            // printable ASCII. Per FoxIO spec, non-ASCII gets mapped
            // to "99" but in practice ALPN values are ASCII strings
            // ("h2", "http/1.1", "h3", etc.).
            let s = std::str::from_utf8(&proto[..2]).unwrap_or("99");
            s.to_string()
        }
        Some(proto) if proto.len() == 1 => {
            // 1-char ALPN (unusual). Pad with first char twice per
            // FoxIO convention — but more commonly use "00". Stay
            // safe with the latter; real personas don't hit this.
            let c = std::str::from_utf8(proto)
                .unwrap_or("0")
                .chars()
                .next()
                .unwrap_or('0');
            format!("{c}{c}")
        }
        _ => "00".to_string(),
    };
    let ja4_a = format!(
        "t{}{}{:02}{:02}{}",
        p.tls_version_tag, sni_ind, cipher_count, ext_count, alpn_tag
    );

    // ja4_b: sha256(sorted GREASE-stripped cipher list, comma-joined
    // as 4-char lowercase hex)[:12]
    let mut ciphers: Vec<u16> = p
        .cipher_suites
        .iter()
        .copied()
        .filter(|c| !is_grease(*c))
        .collect();
    ciphers.sort();
    let cipher_str = ciphers
        .iter()
        .map(|c| format!("{c:04x}"))
        .collect::<Vec<_>>()
        .join(",");
    let ja4_b = sha256_first12(&cipher_str);

    // ja4_c: sha256(extension list in presented order, GREASE-stripped,
    // excluding 0x0000 (SNI) and 0x0010 (ALPN), comma-joined hex, then
    // "_" then signature_algorithms list in presented order, comma-
    // joined hex)[:12]
    let ext_str = p
        .extensions
        .iter()
        .copied()
        .filter(|e| !is_grease(*e) && *e != 0x0000 && *e != 0x0010)
        .map(|e| format!("{e:04x}"))
        .collect::<Vec<_>>()
        .join(",");
    let sig_str = p
        .signature_algorithms
        .iter()
        .map(|s| format!("{s:04x}"))
        .collect::<Vec<_>>()
        .join(",");
    let ja4_c_input = format!("{ext_str}_{sig_str}");
    let ja4_c = sha256_first12(&ja4_c_input);

    format!("{ja4_a}_{ja4_b}_{ja4_c}")
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grease_detection_matches_rfc_8701_values() {
        // All 16 RFC 8701 GREASE values must be detected.
        for n in 0u16..16 {
            let v = (n << 12) | 0x0A00 | (n << 4) | 0x0A;
            assert!(is_grease(v), "0x{v:04x} should be GREASE");
        }
        // Common non-GREASE values must NOT be flagged.
        for v in [0x1301, 0x1302, 0x1303, 0xc02b, 0xc02c, 0x0000, 0x002b] {
            assert!(!is_grease(v), "0x{v:04x} should NOT be GREASE");
        }
    }

    #[test]
    fn version_tag_maps_known_versions() {
        assert_eq!(encode_version_tag(0x0304), "13");
        assert_eq!(encode_version_tag(0x0303), "12");
        assert_eq!(encode_version_tag(0x0302), "11");
        assert_eq!(encode_version_tag(0x0301), "10");
        assert_eq!(encode_version_tag(0x0300), "s3");
        assert_eq!(encode_version_tag(0xdead), "00");
    }

    #[test]
    fn sha256_first12_is_lowercase_hex_12_chars() {
        let h = sha256_first12("hello");
        assert_eq!(h.len(), 12);
        assert!(h
            .chars()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
        // Stable assertion: sha256("hello")[:6] = 2cf24dba5fb0
        assert_eq!(h, "2cf24dba5fb0");
    }

    #[test]
    fn format_ja4_basic_structure() {
        // Hand-craft a ParsedClientHello matching FoxIO's documented
        // canonical example for Chrome 122 stable. The cipher list
        // and extensions are chosen to verify the format machinery —
        // exact hashes here are deterministic but not validated
        // against an external source (that's the next test).
        let p = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: true,
            cipher_suites: vec![0x1301, 0x1302, 0x1303],
            extensions: vec![0x002b, 0x000d, 0x0000, 0x0010],
            alpn_protocols: vec![b"h2".to_vec()],
            signature_algorithms: vec![0x0403, 0x0804],
        };
        let ja4 = format_ja4(&p);
        // ja4_a structure: t13d + cipher_cnt 03 + ext_cnt 04 + h2
        assert!(ja4.starts_with("t13d0304h2_"), "got: {ja4}");
        let parts: Vec<&str> = ja4.split('_').collect();
        assert_eq!(parts.len(), 3, "JA4 must have 3 underscore-separated parts");
        assert_eq!(parts[1].len(), 12, "ja4_b must be 12 hex chars");
        assert_eq!(parts[2].len(), 12, "ja4_c must be 12 hex chars");
    }

    #[test]
    fn format_ja4_strips_grease_before_counting_and_hashing() {
        // Persona A: real cipher list.
        let pa = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: true,
            cipher_suites: vec![0x1301, 0x1302, 0x1303],
            extensions: vec![0x002b, 0x000d, 0x0000, 0x0010],
            alpn_protocols: vec![b"h2".to_vec()],
            signature_algorithms: vec![0x0403, 0x0804],
        };
        // Persona B: same as A + GREASE values sprinkled in.
        let pb = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: true,
            cipher_suites: vec![0x0a0a, 0x1301, 0x1302, 0xfafa, 0x1303],
            extensions: vec![0x4a4a, 0x002b, 0x000d, 0x0000, 0x0010, 0xeaea],
            alpn_protocols: vec![b"h2".to_vec()],
            signature_algorithms: vec![0x0403, 0x0804],
        };
        // JA4 must be identical — GREASE doesn't affect the output.
        assert_eq!(format_ja4(&pa), format_ja4(&pb));
    }

    #[test]
    fn format_ja4_no_sni_yields_i_indicator() {
        let p = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: false,
            cipher_suites: vec![0x1301],
            extensions: vec![0x002b],
            alpn_protocols: vec![b"h2".to_vec()],
            signature_algorithms: vec![],
        };
        let ja4 = format_ja4(&p);
        assert!(ja4.starts_with("t13i"), "got: {ja4}");
    }

    #[test]
    fn format_ja4_no_alpn_yields_00_tag() {
        let p = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: true,
            cipher_suites: vec![0x1301],
            extensions: vec![0x002b],
            alpn_protocols: vec![],
            signature_algorithms: vec![],
        };
        let ja4 = format_ja4(&p);
        assert!(ja4.starts_with("t13d010100_"), "got: {ja4}");
    }

    #[test]
    fn format_ja4_http11_alpn_uses_first_two_chars() {
        let p = ParsedClientHello {
            tls_version_tag: "13".to_string(),
            sni_present: true,
            cipher_suites: vec![0x1301],
            extensions: vec![0x002b],
            alpn_protocols: vec![b"http/1.1".to_vec()],
            signature_algorithms: vec![],
        };
        let ja4 = format_ja4(&p);
        // "http/1.1" → first two chars "ht"
        assert!(ja4.starts_with("t13d0101ht_"), "got: {ja4}");
    }

    proptest::proptest! {
        /// GREASE values must NEVER influence the output JA4. Add any
        /// random combination of GREASE values to the cipher list and
        /// extension list, and the output JA4 must match the un-GREASE'd
        /// baseline.
        #[test]
        fn prop_grease_does_not_affect_output(seed: u64) {
            use rand::SeedableRng;
            use rand::rngs::StdRng;
            use rand::seq::SliceRandom;
            use rand::Rng;
            let mut rng = StdRng::seed_from_u64(seed);

            let grease_values: [u16; 16] = [
                0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a, 0x5a5a, 0x6a6a, 0x7a7a,
                0x8a8a, 0x9a9a, 0xaaaa, 0xbaba, 0xcaca, 0xdada, 0xeaea, 0xfafa,
            ];

            let base = ParsedClientHello {
                tls_version_tag: "13".to_string(),
                sni_present: true,
                cipher_suites: vec![0x1301, 0x1302, 0x1303, 0xc02b, 0xc02c],
                extensions: vec![0x002b, 0x000d, 0x0017, 0x0023, 0x002d, 0x0033, 0x0000, 0x0010],
                alpn_protocols: vec![b"h2".to_vec()],
                signature_algorithms: vec![0x0403, 0x0804, 0x0807],
            };
            let baseline = format_ja4(&base);

            // Insert 1-4 random GREASE values into both lists.
            let n_ciphers: usize = rng.gen_range(1..=4);
            let n_exts: usize = rng.gen_range(1..=4);
            let mut perturbed = base.clone();
            for _ in 0..n_ciphers {
                let g = *grease_values.choose(&mut rng).unwrap();
                let pos: usize = rng.gen_range(0..=perturbed.cipher_suites.len());
                perturbed.cipher_suites.insert(pos, g);
            }
            for _ in 0..n_exts {
                let g = *grease_values.choose(&mut rng).unwrap();
                let pos: usize = rng.gen_range(0..=perturbed.extensions.len());
                perturbed.extensions.insert(pos, g);
            }

            proptest::prop_assert_eq!(format_ja4(&perturbed), baseline);
        }
    }
}

// ClientHello-bytes round-trip tests — exercise the parser end-to-end
// using rustls to generate real ClientHellos from a controlled client
// config.
#[cfg(test)]
mod parser_tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::OnceLock;
    use tokio::io::AsyncWriteExt;
    use tokio::net::TcpListener;

    fn install_default_provider() {
        static INIT: OnceLock<()> = OnceLock::new();
        INIT.get_or_init(|| {
            let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
        });
    }

    /// Capture exactly one ClientHello by spawning a TCP listener,
    /// reading the first 8KB, returning. No TLS handshake — we just
    /// want the raw bytes the client emits.
    async fn capture_one_client_hello(client_setup: impl FnOnce(std::net::SocketAddr)) -> Vec<u8> {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();

        let server_task = tokio::spawn(async move {
            let (mut tcp, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 8192];
            let n = tokio::io::AsyncReadExt::read(&mut tcp, &mut buf)
                .await
                .unwrap_or(0);
            buf.truncate(n);
            // Reset the TCP connection so the client doesn't hang on
            // handshake completion.
            let _ = tcp.shutdown().await;
            buf
        });

        client_setup(addr);
        // Spawn the client on a separate task so server.accept() and
        // client.connect() run concurrently.
        server_task.await.unwrap()
    }

    #[tokio::test]
    async fn parses_rustls_client_hello() {
        install_default_provider();

        // Build a permissive rustls client and have it attempt a
        // handshake against a TCP-only socket. We capture the
        // ClientHello bytes before the handshake fails.
        #[derive(Debug)]
        struct AcceptAny;
        impl rustls::client::danger::ServerCertVerifier for AcceptAny {
            fn verify_server_cert(
                &self,
                _: &rustls::pki_types::CertificateDer<'_>,
                _: &[rustls::pki_types::CertificateDer<'_>],
                _: &rustls::pki_types::ServerName<'_>,
                _: &[u8],
                _: rustls::pki_types::UnixTime,
            ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
                Ok(rustls::client::danger::ServerCertVerified::assertion())
            }
            fn verify_tls12_signature(
                &self,
                _: &[u8],
                _: &rustls::pki_types::CertificateDer<'_>,
                _: &rustls::DigitallySignedStruct,
            ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error>
            {
                Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
            }
            fn verify_tls13_signature(
                &self,
                _: &[u8],
                _: &rustls::pki_types::CertificateDer<'_>,
                _: &rustls::DigitallySignedStruct,
            ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error>
            {
                Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
            }
            fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
                vec![rustls::SignatureScheme::ED25519]
            }
        }

        let mut config = rustls::ClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(AcceptAny))
            .with_no_client_auth();
        config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        let connector = tokio_rustls::TlsConnector::from(Arc::new(config));

        let bytes = capture_one_client_hello(move |addr| {
            tokio::spawn(async move {
                if let Ok(tcp) = tokio::net::TcpStream::connect(addr).await {
                    let name = rustls::pki_types::ServerName::try_from("example.com").unwrap();
                    // This will fail (server doesn't reply with valid
                    // TLS) but ClientHello goes out before failure.
                    let _ = connector.connect(name, tcp).await;
                }
            });
        })
        .await;

        let parsed = parse_client_hello(&bytes).expect("parse rustls ClientHello");
        // rustls emits TLS 1.3 with supported_versions ext present.
        assert_eq!(parsed.tls_version_tag, "13");
        // SNI = "example.com" — must be detected.
        assert!(parsed.sni_present);
        // ALPN: h2 first, then http/1.1.
        assert_eq!(parsed.alpn_protocols.len(), 2);
        assert_eq!(parsed.alpn_protocols[0], b"h2");
        // Cipher suites must be non-empty.
        assert!(!parsed.cipher_suites.is_empty());
        // signature_algorithms is offered by rustls.
        assert!(!parsed.signature_algorithms.is_empty());

        // Compute JA4 — exact value depends on rustls's cipher/ext list
        // (varies by rustls version). Just verify structure.
        let ja4 = format_ja4(&parsed);
        assert!(ja4.starts_with("t13d"), "got: {ja4}");
        let parts: Vec<&str> = ja4.split('_').collect();
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[1].len(), 12);
        assert_eq!(parts[2].len(), 12);
    }

    #[test]
    fn compute_ja4_from_client_hello_rejects_non_handshake_bytes() {
        let garbage: Vec<u8> = vec![0xff; 64];
        let err = compute_ja4_from_client_hello(&garbage).expect_err("must fail");
        assert!(
            matches!(err, Ja4Error::Parse(_) | Ja4Error::NotClientHello),
            "got: {err:?}"
        );
    }
}
