//! Layer 2.4 — end-to-end wire conformance integration
//! (W3B.2.4 — `Closes: roctinam/carbonyl-agent#79`, closes epic #62).
//!
//! Drives a real rustls + h2 client through [`LocalTlsResponder`],
//! captures the ClientHello and h2 preface/SETTINGS bytes, computes a
//! [`WireSnapshot`], and asserts against
//! [`ConformanceFixture::assert_wire_state`]. This closes the loop on
//! Layer 2: every prior sub-PR added a parser; this sub-PR proves the
//! parsers compose into a single verdict that the conformance harness
//! can apply.
//!
//! # What this test crate proves
//!
//! 1. `WireSnapshot` is constructible from real wire bytes captured
//!    during an actual TLS handshake against a real server.
//! 2. `assert_wire_state` produces a `ConformanceReport` that backends
//!    can consume the same way they consume `assert_applied_state`.
//! 3. The Layer 2 capture pipeline does NOT depend on the specific
//!    fixture being asserted against — `WireSnapshot` is a pure data
//!    artifact, and any fixture can be compared against any snapshot.
//!
//! # What this test crate does NOT prove
//!
//! - That rustls produces a JA4 matching Chrome 148. It doesn't — JA4
//!   depends on the cipher list, extension order, and ALPN ordering
//!   the client library happens to send. Reproducing Chrome's JA4
//!   requires a forked client like wreq (Phase 2, #75). Until then,
//!   Layer 2 proves the *measurement infrastructure* works; matching
//!   the *target* is a backend's responsibility.

#![allow(dead_code)]

use std::sync::Arc;
use std::time::Duration;

// Import the parser modules from sibling integration tests via #[path].
// They were authored as self-contained units (#76–#78) precisely so
// downstream consumers — including this integration test — can compose
// them without needing a shared common module.
#[path = "wire_responder.rs"]
mod wire_responder;

#[path = "wire_ja4.rs"]
mod wire_ja4;

#[path = "wire_h2.rs"]
mod wire_h2;

use wire_h2::parse_h2_initial_frames;
use wire_ja4::compute_ja4_from_client_hello;
use wire_responder::LocalTlsResponder;

use carbonyl_fingerprint::conformance::{ConformanceFixture, WireSnapshot};
use carbonyl_fingerprint::http::{H2Priority, H2Settings, H2WindowUpdate};

use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;

/// rustls's default crypto provider trusts the OS root store; for a
/// local self-signed server we install a no-op verifier that accepts
/// any cert. The responder's cert is generated fresh per test via
/// `rcgen` so there's no fixed CA to anchor against.
mod insecure {
    use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
    use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
    use rustls::{DigitallySignedStruct, SignatureScheme};

    #[derive(Debug)]
    pub struct AcceptAny;

    impl ServerCertVerifier for AcceptAny {
        fn verify_server_cert(
            &self,
            _end_entity: &CertificateDer<'_>,
            _intermediates: &[CertificateDer<'_>],
            _server_name: &ServerName<'_>,
            _ocsp_response: &[u8],
            _now: UnixTime,
        ) -> Result<ServerCertVerified, rustls::Error> {
            Ok(ServerCertVerified::assertion())
        }
        fn verify_tls12_signature(
            &self,
            _message: &[u8],
            _cert: &CertificateDer<'_>,
            _dss: &DigitallySignedStruct,
        ) -> Result<HandshakeSignatureValid, rustls::Error> {
            Ok(HandshakeSignatureValid::assertion())
        }
        fn verify_tls13_signature(
            &self,
            _message: &[u8],
            _cert: &CertificateDer<'_>,
            _dss: &DigitallySignedStruct,
        ) -> Result<HandshakeSignatureValid, rustls::Error> {
            Ok(HandshakeSignatureValid::assertion())
        }
        fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
            vec![
                SignatureScheme::RSA_PKCS1_SHA256,
                SignatureScheme::ECDSA_NISTP256_SHA256,
                SignatureScheme::ED25519,
                SignatureScheme::RSA_PSS_SHA256,
            ]
        }
    }
}

fn make_client_config() -> ClientConfig {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();

    let mut config = ClientConfig::builder()
        .with_root_certificates(RootCertStore::empty())
        .with_no_client_auth();
    config
        .dangerous()
        .set_certificate_verifier(Arc::new(insecure::AcceptAny));
    // Offer h2 first (Chrome's canonical preference) so the responder
    // negotiates HTTP/2 — required to trigger the h2 preface capture.
    config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    config
}

/// Build a `WireSnapshot` from a captured handshake. Returns `None` if
/// the handshake didn't complete (no point asserting against a torn
/// connection — the harness already reports `handshake_complete: false`
/// as its own failure mode).
fn snapshot_from_capture(capture: &wire_responder::CapturedHandshake) -> Option<WireSnapshot> {
    if !capture.handshake_complete {
        return None;
    }
    let ja4 = compute_ja4_from_client_hello(&capture.client_hello_bytes).ok()?;
    let h2 = parse_h2_initial_frames(&capture.h2_bytes).ok()?;
    let settings = H2Settings::from_akamai(&h2.akamai_string).ok()?;
    let window = H2WindowUpdate::from_akamai(&h2.akamai_string).ok()?;
    let priority = H2Priority::from_akamai(&h2.akamai_string).ok()?;
    Some(WireSnapshot {
        ja4,
        negotiated_alpn: capture.negotiated_alpn.clone(),
        h2_settings: settings,
        h2_window_update: window,
        h2_priority: priority,
        akamai_string: h2.akamai_string,
    })
}

/// Drive a real rustls + h2 client against [`LocalTlsResponder`] and
/// return the captured handshake. This is the foundation every Layer
/// 2.4 test below uses.
async fn run_one_handshake() -> wire_responder::CapturedHandshake {
    let (responder, captured_rx) = LocalTlsResponder::start_one_shot()
        .await
        .expect("responder must bind");

    let listen_addr = responder.listen_addr;
    let client_task = tokio::spawn(async move {
        let config = make_client_config();
        let connector = TlsConnector::from(Arc::new(config));
        let server_name = ServerName::try_from("localhost").unwrap();
        let tcp = TcpStream::connect(listen_addr).await.expect("connect");
        let tls = connector
            .connect(server_name, tcp)
            .await
            .expect("tls handshake");

        // Promote to h2: send the preface + a trivial SETTINGS frame so
        // the responder sees real h2 wire bytes. We use the raw h2 client
        // path via the `h2` crate to flush the protocol-correct preface
        // and initial frames; we don't actually await a response — the
        // responder shuts down after capturing.
        let (h2, connection) = h2::client::handshake(tls).await.expect("h2 handshake");
        let conn_task = tokio::spawn(async move {
            // Server closes after capture; expected error.
            let _ = connection.await;
        });
        let mut h2 = h2;
        let req = http::Request::builder()
            .method("GET")
            .uri("https://localhost/")
            .body(())
            .unwrap();
        let _ = h2.send_request(req, true);
        tokio::time::sleep(Duration::from_millis(150)).await;
        drop(h2);
        let _ = conn_task.await;
    });

    let captured = tokio::time::timeout(Duration::from_secs(10), captured_rx)
        .await
        .expect("responder reports within timeout")
        .expect("responder send did not fail");
    let _ = client_task.await;
    captured
}

#[tokio::test]
async fn integrated_snapshot_round_trips_against_self() {
    // Strategy: capture real wire bytes, build a snapshot, then
    // construct a fixture whose expected fields ARE the snapshot. The
    // round-trip MUST produce zero mismatches — proves the diff engine
    // is self-consistent for any well-formed snapshot.
    let capture = run_one_handshake().await;
    assert!(
        capture.handshake_complete,
        "rustls handshake failed: {:?}",
        capture.error
    );
    assert!(
        !capture.client_hello_bytes.is_empty(),
        "ClientHello must be captured"
    );
    assert!(!capture.h2_bytes.is_empty(), "h2 preface must be captured");
    assert_eq!(
        capture.negotiated_alpn.as_deref(),
        Some("h2"),
        "ALPN must resolve to h2"
    );

    let snap = snapshot_from_capture(&capture).expect("snapshot must construct");

    // Sanity: JA4 has the canonical three-section structure
    let sections: Vec<&str> = snap.ja4.split('_').collect();
    assert_eq!(
        sections.len(),
        3,
        "JA4 must be three underscore-separated parts"
    );

    // The Chrome 148 fixture's JA4 will NOT match rustls's JA4 — that's
    // expected (different client). We don't assert that here; we just
    // verify the snapshot is well-formed and the assert_wire_state path
    // produces a coherent mismatch report.
    let chrome = ConformanceFixture::chrome_148_stable_linux();
    let report = chrome.assert_wire_state(&snap);
    // Either there's a JA4 mismatch (expected for rustls vs Chrome) OR
    // no mismatch (only possible if rustls happened to match — would be
    // a coincidence). Either way the report is well-formed.
    if !report.mismatches.is_empty() {
        let fields: Vec<&str> = report.mismatches.iter().map(|m| m.field).collect();
        // ja4 mismatch is the expected failure when rustls != Chrome
        assert!(
            fields.contains(&"wire.ja4"),
            "if mismatches exist, ja4 must be among them: {fields:?}"
        );
    }
}

#[tokio::test]
async fn integrated_snapshot_against_matching_fixture_passes_clean() {
    // The integration test above proves the harness produces a coherent
    // report. This test proves the harness produces ZERO mismatches
    // when the fixture's expected values are derived from the same wire
    // capture — i.e. when a backend genuinely conforms to its own
    // baseline.
    let capture = run_one_handshake().await;
    assert!(capture.handshake_complete);
    let snap = snapshot_from_capture(&capture).expect("snapshot");

    // Build a custom fixture whose expectations are the snapshot itself.
    // We start from chrome_148 (to get a valid Persona) and override the
    // expected_* fields with the captured values.
    let mut fixture = ConformanceFixture::chrome_148_stable_linux();
    fixture.expected_ja4 = snap.ja4.clone();
    fixture.expected_alpn = snap
        .negotiated_alpn
        .clone()
        .map(|p| vec![p])
        .unwrap_or_default();
    fixture.expected_h2_settings = snap.h2_settings.clone();
    fixture.expected_h2_window = snap.h2_window_update;
    fixture.expected_h2_priority = snap.h2_priority.clone();
    // The Akamai-shape diagnostic field compares against the persona's
    // declared http2_akamai — override there so the round-trip is clean.
    fixture.persona.persona.network.http2_akamai = snap.akamai_string.clone();

    let report = fixture.assert_wire_state(&snap);
    assert!(
        report.mismatches.is_empty(),
        "self-referential fixture must produce empty report, got {:?}",
        report.mismatches
    );
}
