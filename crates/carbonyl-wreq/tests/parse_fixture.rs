//! One-shot helper: parse a captured ClientHello fixture and dump the
//! structured fields needed to populate a `presets::PresetTable`
//! entry. Run on-demand (after `capture_real_browser` writes the
//! fixture):
//!
//!     cargo test -p carbonyl-wreq --test parse_fixture \
//!       -- --ignored --nocapture parse_chrome_148
//!
//! Output is a Rust source snippet that can be copy-pasted into
//! `crates/carbonyl-wreq/src/presets/chrome.rs`.

#![allow(dead_code)]

use std::fs;
use std::path::PathBuf;

fn fixture_path(id: &str, ext: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("data/fixtures")
        .join(format!("{id}.{ext}"))
}

#[derive(Debug, Default)]
struct Parsed {
    tls_version: u16,
    cipher_suites: Vec<u16>,
    extensions: Vec<u16>,
    alpn: Vec<String>,
    signature_algorithms: Vec<u16>,
    supported_groups: Vec<u16>,
    grease_in_extensions: bool,
}

fn is_grease(v: u16) -> bool {
    let lo = (v & 0xff) as u8;
    let hi = (v >> 8) as u8;
    lo == hi && (lo & 0x0f) == 0x0a
}

fn parse_client_hello(bytes: &[u8]) -> Parsed {
    use tls_parser::{parse_tls_plaintext, TlsMessage, TlsMessageHandshake};
    let mut out = Parsed::default();

    let (_rest, record) = parse_tls_plaintext(bytes).expect("parse TLS plaintext");
    let mut ch_opt = None;
    for msg in record.msg {
        if let TlsMessage::Handshake(TlsMessageHandshake::ClientHello(ch)) = msg {
            ch_opt = Some(ch);
            break;
        }
    }
    let ch = ch_opt.expect("ClientHello in handshake");
    out.cipher_suites = ch.ciphers.iter().map(|c| c.0).collect();
    out.tls_version = ch.version.0;

    if let Some(ext_bytes) = ch.ext {
        let mut cursor = ext_bytes;
        while cursor.len() >= 4 {
            let ext_type = u16::from_be_bytes([cursor[0], cursor[1]]);
            let ext_len = u16::from_be_bytes([cursor[2], cursor[3]]) as usize;
            if cursor.len() < 4 + ext_len {
                break;
            }
            let ext_data = &cursor[4..4 + ext_len];
            out.extensions.push(ext_type);
            if is_grease(ext_type) {
                out.grease_in_extensions = true;
            }
            match ext_type {
                0x0010 => {
                    if ext_data.len() >= 2 {
                        let list_len = u16::from_be_bytes([ext_data[0], ext_data[1]]) as usize;
                        let mut p = 2;
                        while p < 2 + list_len && p < ext_data.len() {
                            let proto_len = ext_data[p] as usize;
                            p += 1;
                            if p + proto_len > ext_data.len() {
                                break;
                            }
                            out.alpn.push(
                                String::from_utf8_lossy(&ext_data[p..p + proto_len]).into_owned(),
                            );
                            p += proto_len;
                        }
                    }
                }
                0x000d => {
                    if ext_data.len() >= 2 {
                        let list_len = u16::from_be_bytes([ext_data[0], ext_data[1]]) as usize;
                        let mut p = 2;
                        while p + 2 <= 2 + list_len && p + 2 <= ext_data.len() {
                            out.signature_algorithms
                                .push(u16::from_be_bytes([ext_data[p], ext_data[p + 1]]));
                            p += 2;
                        }
                    }
                }
                0x000a => {
                    // supported_groups: 2-byte list length, then u16 IDs
                    if ext_data.len() >= 2 {
                        let list_len = u16::from_be_bytes([ext_data[0], ext_data[1]]) as usize;
                        let mut p = 2;
                        while p + 2 <= 2 + list_len && p + 2 <= ext_data.len() {
                            out.supported_groups
                                .push(u16::from_be_bytes([ext_data[p], ext_data[p + 1]]));
                            p += 2;
                        }
                    }
                }
                _ => {}
            }
            cursor = &cursor[4 + ext_len..];
        }
    }
    out
}

fn cipher_to_openssl_name(id: u16) -> Option<&'static str> {
    Some(match id {
        0x1301 => "TLS_AES_128_GCM_SHA256",
        0x1302 => "TLS_AES_256_GCM_SHA384",
        0x1303 => "TLS_CHACHA20_POLY1305_SHA256",
        0xc02b => "ECDHE-ECDSA-AES128-GCM-SHA256",
        0xc02f => "ECDHE-RSA-AES128-GCM-SHA256",
        0xc02c => "ECDHE-ECDSA-AES256-GCM-SHA384",
        0xc030 => "ECDHE-RSA-AES256-GCM-SHA384",
        0xcca9 => "ECDHE-ECDSA-CHACHA20-POLY1305",
        0xcca8 => "ECDHE-RSA-CHACHA20-POLY1305",
        0xc009 => "ECDHE-ECDSA-AES128-SHA",
        0xc00a => "ECDHE-ECDSA-AES256-SHA",
        0xc013 => "ECDHE-RSA-AES128-SHA",
        0xc014 => "ECDHE-RSA-AES256-SHA",
        0x009c => "AES128-GCM-SHA256",
        0x009d => "AES256-GCM-SHA384",
        0x002f => "AES128-SHA",
        0x0035 => "AES256-SHA",
        _ => return None,
    })
}

fn sigalg_to_openssl_name(id: u16) -> Option<&'static str> {
    // Per IANA TLS SignatureScheme registry + OpenSSL names
    Some(match id {
        0x0403 => "ecdsa_secp256r1_sha256",
        0x0503 => "ecdsa_secp384r1_sha384",
        0x0603 => "ecdsa_secp521r1_sha512",
        0x0807 => "ed25519",
        0x0808 => "ed448",
        0x0804 => "rsa_pss_rsae_sha256",
        0x0805 => "rsa_pss_rsae_sha384",
        0x0806 => "rsa_pss_rsae_sha512",
        0x0809 => "rsa_pss_pss_sha256",
        0x080a => "rsa_pss_pss_sha384",
        0x080b => "rsa_pss_pss_sha512",
        0x0401 => "rsa_pkcs1_sha256",
        0x0501 => "rsa_pkcs1_sha384",
        0x0601 => "rsa_pkcs1_sha512",
        0x0203 => "ecdsa_sha1",
        0x0201 => "rsa_pkcs1_sha1",
        _ => return None,
    })
}

#[test]
#[ignore = "one-shot fixture parser; run on-demand to populate preset entries"]
fn parse_chrome_148() {
    let path = fixture_path("chrome-148-desktop", "client_hello.bin");
    let bytes = fs::read(&path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));

    let p = parse_client_hello(&bytes);

    eprintln!("\n========== chrome-148-desktop ClientHello ==========");
    eprintln!("TLS legacy version : 0x{:04x}", p.tls_version);
    eprintln!("Cipher suites ({}):", p.cipher_suites.len());
    let cipher_names: Vec<String> = p
        .cipher_suites
        .iter()
        .filter(|c| !is_grease(**c))
        .filter_map(|c| cipher_to_openssl_name(*c).map(|s| s.to_string()))
        .collect();
    for c in &p.cipher_suites {
        let mark = if is_grease(*c) { " [GREASE]" } else { "" };
        let name = cipher_to_openssl_name(*c).unwrap_or("?");
        eprintln!("  0x{:04x}{} {}", c, mark, name);
    }
    eprintln!("\nExtensions in order ({}):", p.extensions.len());
    for e in &p.extensions {
        let mark = if is_grease(*e) { " [GREASE]" } else { "" };
        eprintln!("  0x{:04x}{}", e, mark);
    }
    eprintln!("\nALPN: {:?}", p.alpn);
    eprintln!("\nSupported groups ({}):", p.supported_groups.len());
    for g in &p.supported_groups {
        let mark = if is_grease(*g) { " [GREASE]" } else { "" };
        eprintln!("  0x{:04x}{}", g, mark);
    }
    eprintln!("\nSignature algorithms ({}):", p.signature_algorithms.len());
    let sigalg_names: Vec<String> = p
        .signature_algorithms
        .iter()
        .filter_map(|s| sigalg_to_openssl_name(*s).map(|n| n.to_string()))
        .collect();
    for s in &p.signature_algorithms {
        let name = sigalg_to_openssl_name(*s).unwrap_or("?");
        eprintln!("  0x{:04x} {}", s, name);
    }

    eprintln!("\n========== Preset entry for chrome.rs ==========");
    eprintln!("pub static CHROME_148_DESKTOP: PresetTable = PresetTable {{");
    eprintln!("    family: BrowserFamily::Chrome,");
    eprintln!("    version: BrowserVersion {{ major: 148, minor: 0 }},");
    eprintln!("    platform: Platform::Desktop,");
    eprintln!("    tls: TlsProfile {{");
    eprintln!("        extension_permutation_indices: None, // Chrome 110+ permutes; let wreq permute too");
    eprintln!("        cipher_list: Some(\"{}\"),", cipher_names.join(":"));
    eprintln!("        alpn_default: &[\"h2\", \"http/1.1\"],");
    eprintln!("        grease_enabled: Some({}),", p.grease_in_extensions);
    eprintln!("        permute_extensions: Some(true),");
    eprint!("        supported_groups: &[");
    let groups_filtered: Vec<String> = p
        .supported_groups
        .iter()
        .filter(|g| !is_grease(**g))
        .map(|g| format!("0x{:04x}", g))
        .collect();
    eprintln!("{}],", groups_filtered.join(", "));
    eprintln!(
        "        sigalgs_list: Some(\"{}\"),",
        sigalg_names.join(":")
    );
    eprintln!("    }},");
    eprintln!("    h2: H2Profile {{");
    eprintln!("        // Real Chrome 148 wire capture (4 values; persona's 0x03=1000 dropped):");
    eprintln!(
        "        settings_default: &[(0x01, 65536), (0x02, 0), (0x04, 6291456), (0x06, 262144)],"
    );
    eprintln!("        initial_connection_window: 15663105,");
    eprintln!(
        "        pseudo_header_order: &[\":method\", \":authority\", \":scheme\", \":path\"],"
    );
    eprintln!("    }},");
    eprintln!("    headers: HeaderProfile {{");
    eprintln!("        default_order: &[");
    eprintln!("            \"host\", \"sec-ch-ua\", \"sec-ch-ua-mobile\", \"sec-ch-ua-platform\",");
    eprintln!("            \"upgrade-insecure-requests\", \"user-agent\", \"accept\",");
    eprintln!("            \"accept-encoding\", \"accept-language\",");
    eprintln!("        ],");
    eprintln!("        static_defaults: &[");
    eprintln!("            (\"accept\", \"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8\"),");
    eprintln!("            (\"accept-encoding\", \"gzip, deflate, br, zstd\"),");
    eprintln!("            (\"upgrade-insecure-requests\", \"1\"),");
    eprintln!("        ],");
    eprintln!("    }},");
    eprintln!("    provenance_id: \"chrome-148-desktop\",");
    eprintln!("}};");
    eprintln!("================================================================\n");
}

#[test]
#[ignore = "one-shot fixture parser; run on-demand to populate preset entries"]
fn parse_firefox_150() {
    let path = fixture_path("firefox-150-desktop", "client_hello.bin");
    let bytes = fs::read(&path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));

    let p = parse_client_hello(&bytes);

    eprintln!("\n========== firefox-150-desktop ClientHello ==========");
    eprintln!("TLS legacy version : 0x{:04x}", p.tls_version);
    eprintln!("Cipher suites ({}):", p.cipher_suites.len());
    let cipher_names: Vec<String> = p
        .cipher_suites
        .iter()
        .filter(|c| !is_grease(**c))
        .filter_map(|c| cipher_to_openssl_name(*c).map(|s| s.to_string()))
        .collect();
    for c in &p.cipher_suites {
        let mark = if is_grease(*c) { " [GREASE]" } else { "" };
        let name = cipher_to_openssl_name(*c).unwrap_or("?");
        eprintln!("  0x{:04x}{} {}", c, mark, name);
    }
    eprintln!("\nExtensions in order ({}):", p.extensions.len());
    for e in &p.extensions {
        let mark = if is_grease(*e) { " [GREASE]" } else { "" };
        eprintln!("  0x{:04x}{}", e, mark);
    }
    eprintln!("\nALPN: {:?}", p.alpn);
    eprintln!("\nSupported groups ({}):", p.supported_groups.len());
    for g in &p.supported_groups {
        let mark = if is_grease(*g) { " [GREASE]" } else { "" };
        eprintln!("  0x{:04x}{}", g, mark);
    }
    eprintln!("\nSignature algorithms ({}):", p.signature_algorithms.len());
    let sigalg_names: Vec<String> = p
        .signature_algorithms
        .iter()
        .filter_map(|s| sigalg_to_openssl_name(*s).map(|n| n.to_string()))
        .collect();
    for s in &p.signature_algorithms {
        let name = sigalg_to_openssl_name(*s).unwrap_or("?");
        eprintln!("  0x{:04x} {}", s, name);
    }
    eprintln!(
        "\ngrease_in_extensions: {} (Firefox does NOT emit GREASE)",
        p.grease_in_extensions
    );

    let groups_filtered: Vec<String> = p
        .supported_groups
        .iter()
        .filter(|g| !is_grease(**g))
        .map(|g| format!("0x{:04x}", g))
        .collect();
    eprintln!(
        "\nsupported_groups (no GREASE): &[{}]",
        groups_filtered.join(", ")
    );
    eprintln!("cipher_list: \"{}\"", cipher_names.join(":"));
    eprintln!("sigalgs_list: \"{}\"", sigalg_names.join(":"));
    eprintln!("================================================================\n");
}
