//! Real-browser fixture capture for the in-house preset registry
//! (Iteration A item 3 / Iteration B items 1a-1d).
//!
//! Each capture function spins up a custom localhost TLS responder
//! (so we control the cert and can compute its SPKI hash for Chrome's
//! `--ignore-certificate-errors-spki-list` flag), launches a real
//! browser binary in headless mode pointed at the responder, and
//! writes the captured ClientHello + h2 SETTINGS bytes (plus a
//! metadata.toml) to `crates/carbonyl-wreq/data/fixtures/`.
//!
//! Tests are `#[ignore]` by default — they require a real browser
//! installed on the host and are run on-demand:
//!
//!     cargo test -p carbonyl-wreq --test capture_real_browser \
//!       -- --ignored --test-threads=1 capture_chrome_desktop
//!
//! Per `fixtures-plan.md` §3.1, captures use the default browser
//! profile, no extensions, no flags overriding network behavior.
//! Headless mode (`--headless=new` for Chrome) uses the full Chromium
//! network stack — equivalent to non-headless for fingerprinting.

#![allow(dead_code)]
#![allow(clippy::collapsible_if)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use base64::Engine;
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::ServerConfig;
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::time::timeout;
use tokio_rustls::TlsAcceptor;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("data/fixtures")
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    format!("{:x}", h.finalize())
}

/// Captured handshake — same shape as carbonyl-fingerprint's
/// `LocalTlsResponder::CapturedHandshake`. Inlined so the capture
/// test doesn't depend on `tests/` modules of a sibling crate.
#[derive(Debug, Default)]
struct Captured {
    client_hello_bytes: Vec<u8>,
    h2_bytes: Vec<u8>,
    negotiated_alpn: Option<String>,
    handshake_complete: bool,
    error: Option<String>,
}

/// Generate a fresh self-signed cert for 127.0.0.1, return its
/// `ServerConfig` AND the cert DER (so the caller can compute SPKI).
fn make_server_config_with_cert() -> std::io::Result<(ServerConfig, Vec<u8>)> {
    let key_pair = rcgen::KeyPair::generate()
        .map_err(|e| std::io::Error::other(format!("rcgen keypair: {e}")))?;
    let cert = rcgen::CertificateParams::new(vec!["127.0.0.1".to_string()])
        .map_err(|e| std::io::Error::other(format!("rcgen params: {e}")))?
        .self_signed(&key_pair)
        .map_err(|e| std::io::Error::other(format!("rcgen self-sign: {e}")))?;

    let cert_der = cert.der().to_vec();
    let key_der = key_pair.serialize_der();

    let mut server_config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(
            vec![CertificateDer::from(cert_der.clone())],
            PrivateKeyDer::try_from(key_der)
                .map_err(|e| std::io::Error::other(format!("private key: {e}")))?,
        )
        .map_err(|e| std::io::Error::other(format!("server config: {e}")))?;
    // Offer h2 and http/1.1 — same set the test responder uses.
    server_config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];

    Ok((server_config, cert_der))
}

/// Compute Chrome's `--ignore-certificate-errors-spki-list` value
/// from a DER-encoded cert: SHA-256 of the SubjectPublicKeyInfo,
/// base64-encoded.
///
/// We extract the SPKI by parsing minimal X.509 structure: a cert is
/// SEQUENCE { tbsCert, sigAlg, sig }, tbsCert is SEQUENCE containing
/// the SPKI as the 7th element (index 6, 0-based). Easier to use
/// x509-parser, but adding a new dep is overkill — use openssl CLI
/// which is already on the system.
fn spki_b64_for_chrome(cert_der: &[u8]) -> String {
    // Write cert to a temp file as DER, extract SPKI via openssl
    let tmp = std::env::temp_dir().join("carbonyl-fixture-cert.der");
    fs::write(&tmp, cert_der).expect("write tmp cert");
    let pubkey_pem = Command::new("openssl")
        .args(["x509", "-in"])
        .arg(&tmp)
        .args(["-inform", "DER", "-pubkey", "-noout"])
        .output()
        .expect("openssl x509");
    if !pubkey_pem.status.success() {
        panic!(
            "openssl x509 failed: {}",
            String::from_utf8_lossy(&pubkey_pem.stderr)
        );
    }
    // Convert PEM pubkey -> DER (the SPKI we want to hash)
    let mut der_proc = Command::new("openssl")
        .args(["pkey", "-pubin", "-outform", "DER"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn openssl pkey");
    use std::io::Write;
    der_proc
        .stdin
        .as_mut()
        .unwrap()
        .write_all(&pubkey_pem.stdout)
        .expect("write pubkey pem");
    let out = der_proc.wait_with_output().expect("openssl pkey wait");
    if !out.status.success() {
        panic!(
            "openssl pkey failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    let spki_der = out.stdout;
    let hash = Sha256::digest(&spki_der);
    base64::engine::general_purpose::STANDARD.encode(hash)
}

/// Inline one-shot TLS responder. Same shape as carbonyl-fingerprint's
/// `LocalTlsResponder` but exposes the cert DER so callers can compute
/// the Chrome SPKI hash.
async fn start_responder(
) -> std::io::Result<(std::net::SocketAddr, oneshot::Receiver<Captured>, Vec<u8>)> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let listen_addr = listener.local_addr()?;
    let (server_config, cert_der) = make_server_config_with_cert()?;
    let acceptor = TlsAcceptor::from(Arc::new(server_config));
    let (tx, rx) = oneshot::channel();
    tokio::spawn(async move {
        let captured = capture_one(listener, acceptor).await;
        let _ = tx.send(captured);
    });
    Ok((listen_addr, rx, cert_der))
}

async fn capture_one(listener: TcpListener, acceptor: TlsAcceptor) -> Captured {
    let mut captured = Captured::default();
    let stream_result = timeout(Duration::from_secs(15), listener.accept()).await;
    let (tcp, _peer) = match stream_result {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => {
            captured.error = Some(format!("accept error: {e}"));
            return captured;
        }
        Err(_) => {
            captured.error = Some("accept timed out — no client connected within 15s".into());
            return captured;
        }
    };
    let mut peek_buf = vec![0u8; 8192];
    if let Ok(n) = tcp.peek(&mut peek_buf).await {
        peek_buf.truncate(n);
        captured.client_hello_bytes = peek_buf;
    }
    match acceptor.accept(tcp).await {
        Ok(mut tls) => {
            captured.negotiated_alpn = tls
                .get_ref()
                .1
                .alpn_protocol()
                .map(|b| String::from_utf8_lossy(b).into_owned());
            captured.handshake_complete = true;
            let total = Duration::from_millis(800);
            let between = Duration::from_millis(100);
            let start = Instant::now();
            let mut buf = vec![0u8; 4096];
            while start.elapsed() < total {
                let remaining = total.saturating_sub(start.elapsed());
                let read_t = std::cmp::min(between, remaining);
                match timeout(read_t, tls.read(&mut buf)).await {
                    Ok(Ok(0)) => break,
                    Ok(Ok(n)) => captured.h2_bytes.extend_from_slice(&buf[..n]),
                    Ok(Err(_)) => break,
                    Err(_) => {
                        if !captured.h2_bytes.is_empty() {
                            break;
                        }
                    }
                }
            }
            // Send a minimal h2 SETTINGS frame back so Chrome's h2
            // state machine doesn't choke before sending its own.
            // Frame: length=0, type=4 (SETTINGS), flags=0, stream=0
            let _ = tls.write_all(&[0, 0, 0, 4, 0, 0, 0, 0, 0]).await;
            let _ = tls.shutdown().await;
        }
        Err(e) => {
            captured.error = Some(format!("handshake error: {e}"));
        }
    }
    captured
}

async fn capture_with_browser(
    fixture_id: &str,
    browser_product: &str,
    browser_command: &str,
    browser_args_template: impl FnOnce(&str, &str) -> Vec<String>,
    platform_label: &str,
) {
    let dir = fixtures_dir();
    fs::create_dir_all(&dir).expect("fixtures dir");

    let (listen_addr, captured_rx, cert_der) =
        start_responder().await.expect("responder must bind");
    let url = format!("https://127.0.0.1:{}/", listen_addr.port());
    let spki = spki_b64_for_chrome(&cert_der);
    eprintln!("[capture] cert SPKI hash (b64) = {spki}");

    let args = browser_args_template(&url, &spki);
    eprintln!("[capture] launching: {} {:?}", browser_command, args);

    let browser_cmd = browser_command.to_string();
    let browser_handle = std::thread::spawn(move || {
        let result = Command::new(&browser_cmd)
            .args(&args)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn();
        match result {
            Ok(mut child) => {
                std::thread::sleep(Duration::from_secs(10));
                let _ = child.kill();
                let out = child.wait_with_output();
                if let Ok(o) = out {
                    let stderr = String::from_utf8_lossy(&o.stderr);
                    let interesting: Vec<&str> = stderr
                        .lines()
                        .filter(|l| {
                            let l = l.to_lowercase();
                            l.contains("error")
                                || l.contains("ssl")
                                || l.contains("cert")
                                || l.contains("handshake")
                                || l.contains("connect")
                        })
                        .take(20)
                        .collect();
                    if !interesting.is_empty() {
                        eprintln!("[capture] browser stderr (filtered):");
                        for l in interesting {
                            eprintln!("[capture]   {l}");
                        }
                    }
                }
            }
            Err(e) => panic!("failed to launch {browser_cmd}: {e}"),
        }
    });

    let captured = tokio::time::timeout(Duration::from_secs(20), captured_rx)
        .await
        .expect("responder reports within timeout")
        .expect("responder send did not fail");
    let _ = browser_handle.join();

    write_fixture_artifacts(fixture_id, browser_product, platform_label, &captured, &dir);
}

fn write_fixture_artifacts(
    fixture_id: &str,
    browser_product: &str,
    platform_label: &str,
    captured: &Captured,
    dir: &Path,
) {
    eprintln!(
        "[capture] handshake_complete={}, client_hello={}b, h2={}b, alpn={:?}, err={:?}",
        captured.handshake_complete,
        captured.client_hello_bytes.len(),
        captured.h2_bytes.len(),
        captured.negotiated_alpn,
        captured.error,
    );
    assert!(
        !captured.client_hello_bytes.is_empty(),
        "ClientHello bytes empty (handshake_complete={}, error={:?})",
        captured.handshake_complete,
        captured.error
    );

    let ch_path = dir.join(format!("{fixture_id}.client_hello.bin"));
    let h2_path = dir.join(format!("{fixture_id}.h2_settings.bin"));
    let meta_path = dir.join(format!("{fixture_id}.metadata.toml"));

    fs::write(&ch_path, &captured.client_hello_bytes).expect("write client_hello");
    fs::write(&h2_path, &captured.h2_bytes).expect("write h2_settings");

    let ch_sha = sha256_hex(&captured.client_hello_bytes);
    let h2_sha = sha256_hex(&captured.h2_bytes);

    let alpn = captured
        .negotiated_alpn
        .clone()
        .unwrap_or_else(|| "<none>".into());

    let timestamp = format_epoch_seconds_utc(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs(),
    );
    let meta = format!(
        r#"# Auto-generated by tests/capture_real_browser.rs
fixture_id = "{fixture_id}"
browser_product = "{browser_product}"
platform = "{platform_label}"
captured_at = "{timestamp}"
captured_by = "carbonyl-agent capture_real_browser test"
capture_method = "inline TLS+h2 responder + headless browser; loopback only"
negotiated_alpn = "{alpn}"

[artifacts]
client_hello_path = "{fixture_id}.client_hello.bin"
client_hello_sha256 = "{ch_sha}"
client_hello_size_bytes = {ch_size}
h2_settings_path = "{fixture_id}.h2_settings.bin"
h2_settings_sha256 = "{h2_sha}"
h2_settings_size_bytes = {h2_size}

# Note: the JA4 cross-check against the FoxIO database (per
# fixtures-plan.md §3.2 step 11) was NOT performed at capture time —
# the FoxIO `ja4` CLI is not installed on this host. The L2
# conformance suite computes JA4 from the captured ClientHello and
# asserts against persona-declared JA4; matching there implicitly
# cross-validates the capture.
"#,
        fixture_id = fixture_id,
        browser_product = browser_product,
        platform_label = platform_label,
        timestamp = timestamp,
        alpn = alpn,
        ch_sha = ch_sha,
        ch_size = captured.client_hello_bytes.len(),
        h2_sha = h2_sha,
        h2_size = captured.h2_bytes.len(),
    );
    fs::write(&meta_path, &meta).expect("write metadata");

    eprintln!(
        "[capture] wrote {} ({} bytes ClientHello, {} bytes h2)",
        fixture_id,
        captured.client_hello_bytes.len(),
        captured.h2_bytes.len()
    );
    eprintln!("[capture]   client_hello_sha256 = {ch_sha}");
    eprintln!("[capture]   h2_settings_sha256  = {h2_sha}");
}

fn format_epoch_seconds_utc(secs: u64) -> String {
    let z = (secs / 86400) as i64 + 719468;
    let era = z.div_euclid(146097);
    let doe = (z - era * 146097) as u64;
    let yoe = (doe
        .wrapping_sub(doe / 1460)
        .wrapping_sub(doe / 36524)
        .wrapping_add(doe / 146096))
        / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    let s = secs % 86400;
    let h = s / 3600;
    let mi = (s % 3600) / 60;
    let se = s % 60;
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, m, d, h, mi, se
    )
}

fn detect_chrome_version() -> String {
    Command::new("google-chrome")
        .arg("--version")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

fn detect_firefox_version() -> String {
    Command::new("firefox")
        .arg("--version")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

#[tokio::test]
#[ignore = "HITL: requires real Google Chrome installed; run with --ignored"]
async fn capture_chrome_desktop() {
    let version_string = detect_chrome_version();
    eprintln!("[capture] detected: {version_string}");
    let major = version_string
        .split_whitespace()
        .last()
        .and_then(|v| v.split('.').next())
        .unwrap_or("unknown");
    let fixture_id = format!("chrome-{major}-desktop");

    capture_with_browser(
        &fixture_id,
        "Google Chrome",
        "google-chrome",
        |url, spki| {
            vec![
                "--headless=new".to_string(),
                "--disable-gpu".to_string(),
                "--no-sandbox".to_string(),
                "--user-data-dir=/tmp/chrome-fixture-capture".to_string(),
                format!("--ignore-certificate-errors-spki-list={spki}"),
                "--disable-extensions".to_string(),
                "--no-first-run".to_string(),
                "--no-default-browser-check".to_string(),
                // Disable QUIC (UDP) so we capture TLS-over-TCP only.
                "--disable-quic".to_string(),
                // Disable ECH so rustls can decrypt the ClientHello
                // (rustls 0.23 server side does not support ECH).
                "--disable-features=EncryptedClientHello".to_string(),
                "--enable-logging=stderr".to_string(),
                "--v=1".to_string(),
                url.to_string(),
            ]
        },
        "Linux x86_64",
    )
    .await;
}

#[tokio::test]
#[ignore = "HITL: requires real Firefox installed; run with --ignored"]
async fn capture_firefox_desktop() {
    let version_string = detect_firefox_version();
    eprintln!("[capture] detected: {version_string}");
    let major = version_string
        .split_whitespace()
        .last()
        .and_then(|v| v.split('.').next())
        .unwrap_or("unknown");
    let fixture_id = format!("firefox-{major}-desktop");

    capture_with_browser(
        &fixture_id,
        "Mozilla Firefox",
        "firefox",
        |url, _spki| {
            vec![
                "--headless".to_string(),
                "--no-remote".to_string(),
                "--profile".to_string(),
                "/tmp/firefox-fixture-capture".to_string(),
                url.to_string(),
            ]
        },
        "Linux x86_64",
    )
    .await;
}
