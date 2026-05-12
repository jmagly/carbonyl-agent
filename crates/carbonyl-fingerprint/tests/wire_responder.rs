//! Layer 2 wire-level conformance — `LocalTlsResponder` scaffold (W3B.2.1
//! — `Refs: roctinam/carbonyl-agent#76`).
//!
//! Provides a one-shot localhost TLS server that captures the client's
//! `ClientHello` bytes plus the negotiated ALPN. Subsequent sub-PRs
//! (#77 JA4 algorithm, #78 h2 SETTINGS capture, #79 fixture assertion)
//! consume the captured handshake and turn it into a wire-level
//! conformance verdict against `ConformanceFixture`.
//!
//! # Status
//!
//! Layer 2.1: scaffold only. The responder accepts a connection, performs
//! the TLS handshake, and returns the raw `ClientHello` bytes. JA4
//! computation and HTTP/2 capture are NOT implemented here — they land
//! in the follow-up sub-PRs.
//!
//! # Why it's a separate test crate
//!
//! The responder pulls in `rustls`, `tokio`, `tokio-rustls`, `rcgen`. The
//! production crate's surface (schema, validator, sampler, refresher,
//! http trait, conformance Layer 1) doesn't depend on any of those — they
//! stay test-only. This keeps the rlib + cdylib targets small and
//! avoids leaking async runtime dependencies into the trait surface.
//!
//! # Usage from a backend's test suite (post-Layer 2.4)
//!
//! ```ignore
//! use carbonyl_fingerprint::conformance::ConformanceFixture;
//! // Layer 2 types from this test crate would be re-exposed via a
//! // proper public test-utilities crate in Layer 2.4.
//!
//! #[tokio::test]
//! async fn wreq_conforms_chrome_147_wire_level() {
//!     let fixture = ConformanceFixture::chrome_147_stable_linux();
//!     let (addr, capture) = LocalTlsResponder::start_one_shot().await.unwrap();
//!
//!     let client = wreq::Client::builder()
//!         .apply_persona(&fixture.persona).unwrap()
//!         .build().unwrap();
//!     let _ = client.get(format!("https://{addr}/")).send().await;
//!
//!     let captured = capture.await.unwrap();
//!     // Layer 2.4: assert_wire_state matches against fixture.expected_*
//! }
//! ```

use std::io;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::ServerConfig;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;
use tokio::time::timeout;
use tokio_rustls::TlsAcceptor;

/// Captured handshake produced by [`LocalTlsResponder`] after a single
/// TLS connection has been accepted. Subsequent Layer 2 sub-PRs (#77,
/// #78) extend this with derived JA4 and H2 SETTINGS data.
#[derive(Debug, Clone, Default)]
pub struct CapturedHandshake {
    /// The 5-byte TLS record header followed by the full ClientHello
    /// handshake message bytes the client sent on its first record.
    /// Empty when the client disconnected before sending anything.
    pub client_hello_bytes: Vec<u8>,

    /// The ALPN protocol the server selected during negotiation, if any.
    /// `None` when ALPN wasn't offered by the client or no match was
    /// found in the server's offered set.
    pub negotiated_alpn: Option<String>,

    /// `true` when the handshake reached the post-handshake application
    /// data stage. `false` indicates an aborted handshake (record-level
    /// error, alert, timeout, or client disconnect).
    pub handshake_complete: bool,

    /// Non-`None` when the responder encountered an error during accept
    /// or handshake. Preserved for test diagnostics; assertion-style
    /// callers should still inspect `handshake_complete`.
    pub error: Option<String>,
}

/// One-shot localhost TLS responder for Layer 2 wire-level conformance
/// testing. Generates a fresh self-signed cert per call, binds to
/// `127.0.0.1:0` (kernel-assigned port), accepts exactly one connection,
/// and reports what the client sent.
///
/// The server's TLS config offers ALPN `["h2", "http/1.1"]` so clients
/// negotiating HTTP/2 (the W3A.6 personas' canonical ALPN) succeed.
///
/// # Lifecycle
///
/// - [`start_one_shot`](Self::start_one_shot) — spawns a tokio task that
///   binds, accepts one connection, captures, and exits. Returns the
///   bound address and a oneshot receiver. The task lives only for that
///   single connection; no manual shutdown is needed.
/// - The internal `JoinHandle` is held by the returned `LocalTlsResponder`
///   so dropping it cancels the task (defense-in-depth — the task exits
///   on its own once the client connects or the accept timeout fires).
pub struct LocalTlsResponder {
    pub listen_addr: SocketAddr,
    _join_handle: JoinHandle<()>,
}

impl LocalTlsResponder {
    /// Bind a localhost TLS responder, accept exactly one connection,
    /// and capture the client's handshake. Returns the bound `SocketAddr`
    /// and a oneshot receiver that delivers the captured handshake.
    ///
    /// The internal task aborts itself if no client connects within
    /// [`ACCEPT_TIMEOUT`]; the receiver completes with a
    /// `CapturedHandshake` whose `error` field is populated in that case.
    pub async fn start_one_shot() -> io::Result<(Self, oneshot::Receiver<CapturedHandshake>)> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let listen_addr = listener.local_addr()?;
        let server_config = make_server_config()?;
        let acceptor = TlsAcceptor::from(Arc::new(server_config));

        let (tx, rx) = oneshot::channel();
        let join_handle = tokio::spawn(async move {
            let captured = capture_one(listener, acceptor).await;
            // Receiver may have been dropped if the caller didn't care
            // — that's fine.
            let _ = tx.send(captured);
        });

        Ok((
            Self {
                listen_addr,
                _join_handle: join_handle,
            },
            rx,
        ))
    }
}

/// How long to wait for a client to connect before timing out the
/// accept. Generous enough for slow CI runners; short enough that a
/// stuck test doesn't hang the suite.
const ACCEPT_TIMEOUT: Duration = Duration::from_secs(5);

/// Inner workhorse: accept one TCP connection (with timeout), capture
/// the first record bytes, run the rustls handshake, and report.
async fn capture_one(listener: TcpListener, acceptor: TlsAcceptor) -> CapturedHandshake {
    let mut captured = CapturedHandshake::default();

    let stream_result = timeout(ACCEPT_TIMEOUT, listener.accept()).await;
    let (tcp, _peer) = match stream_result {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => {
            captured.error = Some(format!("accept error: {e}"));
            return captured;
        }
        Err(_) => {
            captured.error = Some(format!(
                "accept timed out after {}s — no client connected",
                ACCEPT_TIMEOUT.as_secs()
            ));
            return captured;
        }
    };

    // Peek the first record (TLS record header is 5 bytes: type, version
    // major, version minor, length high, length low). We read enough to
    // cover the ClientHello handshake message without consuming the
    // bytes — rustls's acceptor will re-read them. tokio's `peek` on
    // TcpStream gives us exactly this without disturbing the read
    // position.
    let mut peek_buf = vec![0u8; 8192];
    match tcp.peek(&mut peek_buf).await {
        Ok(n) => {
            peek_buf.truncate(n);
            captured.client_hello_bytes = peek_buf;
        }
        Err(e) => {
            captured.error = Some(format!("peek error: {e}"));
            // Continue to the handshake anyway — peek failure shouldn't
            // block the handshake completing if the data is still on
            // the wire.
        }
    }

    // Run the TLS handshake.
    match acceptor.accept(tcp).await {
        Ok(mut tls) => {
            // ALPN negotiation result is in the connection's negotiated
            // protocol after the handshake.
            let alpn = tls.get_ref().1.alpn_protocol();
            captured.negotiated_alpn = alpn.map(|b| String::from_utf8_lossy(b).into_owned());
            captured.handshake_complete = true;

            // Drain anything the client wants to send (so the test
            // client doesn't get reset-by-peer when it pushes its first
            // request). Then close cleanly.
            let mut drain = vec![0u8; 4096];
            // Best-effort read with a short timeout — we don't actually
            // need the data, just to absorb it so the client's write
            // succeeds.
            let _ = timeout(Duration::from_millis(200), tls.read(&mut drain)).await;
            let _ = tls.shutdown().await;
        }
        Err(e) => {
            captured.error = Some(format!("handshake error: {e}"));
        }
    }

    captured
}

/// Build a rustls `ServerConfig` with a fresh self-signed cert for
/// `127.0.0.1`. ALPN offered: `["h2", "http/1.1"]` (matches the W3A.6
/// personas).
fn make_server_config() -> io::Result<ServerConfig> {
    let key_pair =
        rcgen::KeyPair::generate().map_err(|e| io::Error::other(format!("rcgen keypair: {e}")))?;
    let cert = rcgen::CertificateParams::new(vec!["127.0.0.1".to_string()])
        .map_err(|e| io::Error::other(format!("rcgen params: {e}")))?
        .self_signed(&key_pair)
        .map_err(|e| io::Error::other(format!("rcgen self-sign: {e}")))?;

    let cert_der = CertificateDer::from(cert.der().to_vec());
    let key_der: PrivateKeyDer = PrivateKeyDer::Pkcs8(rustls::pki_types::PrivatePkcs8KeyDer::from(
        key_pair.serialize_der(),
    ));

    let mut config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(vec![cert_der], key_der)
        .map_err(|e| io::Error::other(format!("rustls config: {e}")))?;

    config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];

    Ok(config)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use rustls::pki_types::ServerName;
    use rustls::ClientConfig;
    use std::sync::OnceLock;
    use tokio::net::TcpStream;
    use tokio_rustls::TlsConnector;

    fn install_default_provider() {
        // rustls 0.23 requires installing a default crypto provider once
        // per process. Idempotent via OnceLock.
        static INIT: OnceLock<()> = OnceLock::new();
        INIT.get_or_init(|| {
            let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
        });
    }

    /// Build a rustls client that trusts ANY server cert (test-only —
    /// the responder uses a fresh self-signed cert each call so trust
    /// would otherwise have to be re-issued every time).
    fn permissive_client_config() -> ClientConfig {
        #[derive(Debug)]
        struct AcceptAny;
        impl rustls::client::danger::ServerCertVerifier for AcceptAny {
            fn verify_server_cert(
                &self,
                _end: &CertificateDer<'_>,
                _ints: &[CertificateDer<'_>],
                _name: &ServerName<'_>,
                _ocsp: &[u8],
                _now: rustls::pki_types::UnixTime,
            ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
                Ok(rustls::client::danger::ServerCertVerified::assertion())
            }
            fn verify_tls12_signature(
                &self,
                _msg: &[u8],
                _cert: &CertificateDer<'_>,
                _dss: &rustls::DigitallySignedStruct,
            ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error>
            {
                Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
            }
            fn verify_tls13_signature(
                &self,
                _msg: &[u8],
                _cert: &CertificateDer<'_>,
                _dss: &rustls::DigitallySignedStruct,
            ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error>
            {
                Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
            }
            fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
                vec![
                    rustls::SignatureScheme::RSA_PKCS1_SHA256,
                    rustls::SignatureScheme::RSA_PSS_SHA256,
                    rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
                    rustls::SignatureScheme::ED25519,
                ]
            }
        }

        ClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(AcceptAny))
            .with_no_client_auth()
    }

    #[tokio::test]
    async fn responder_accepts_a_basic_rustls_client_connection() {
        install_default_provider();
        let (responder, capture_rx) = LocalTlsResponder::start_one_shot().await.unwrap();

        // Connect a basic rustls client.
        let mut config = permissive_client_config();
        config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        let connector = TlsConnector::from(Arc::new(config));

        let tcp = TcpStream::connect(responder.listen_addr).await.unwrap();
        let name = ServerName::try_from("127.0.0.1").unwrap();
        let mut tls = connector.connect(name, tcp).await.unwrap();
        let _ = tls.shutdown().await;

        let captured = capture_rx.await.unwrap();
        assert!(
            captured.error.is_none(),
            "no responder error expected; got: {:?}",
            captured.error
        );
        assert!(
            captured.handshake_complete,
            "handshake should have completed: {captured:?}"
        );
        assert!(
            !captured.client_hello_bytes.is_empty(),
            "ClientHello bytes should be captured"
        );
        // TLS record type for handshake is 0x16.
        assert_eq!(
            captured.client_hello_bytes[0], 0x16,
            "first byte should be the TLS handshake record type"
        );
    }

    #[tokio::test]
    async fn responder_captures_alpn_from_client_offer() {
        install_default_provider();
        let (responder, capture_rx) = LocalTlsResponder::start_one_shot().await.unwrap();

        let mut config = permissive_client_config();
        // Offer h2 first; server's config offers both, so h2 wins.
        config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        let connector = TlsConnector::from(Arc::new(config));

        let tcp = TcpStream::connect(responder.listen_addr).await.unwrap();
        let name = ServerName::try_from("127.0.0.1").unwrap();
        let mut tls = connector.connect(name, tcp).await.unwrap();
        let _ = tls.shutdown().await;

        let captured = capture_rx.await.unwrap();
        assert!(captured.handshake_complete);
        assert_eq!(
            captured.negotiated_alpn.as_deref(),
            Some("h2"),
            "ALPN should have negotiated to h2"
        );
    }

    #[tokio::test]
    async fn responder_returns_within_timeout_on_no_client() {
        install_default_provider();
        let (_responder, capture_rx) = LocalTlsResponder::start_one_shot().await.unwrap();

        // Don't connect. The responder must time out within its own
        // budget and return an error-bearing CapturedHandshake.
        let captured = timeout(Duration::from_secs(10), capture_rx)
            .await
            .expect("outer wait should not deadlock")
            .expect("oneshot receiver should not be dropped");
        assert!(!captured.handshake_complete);
        assert!(
            captured
                .error
                .as_deref()
                .is_some_and(|e| e.contains("timed out")),
            "expected accept-timeout error; got: {:?}",
            captured.error
        );
    }
}
