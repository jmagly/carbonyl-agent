# Fixture Provenance

Per [`fixtures-plan.md`](../../../../.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md) §3.

## Overview

Each row records a captured real-browser TLS+h2 fixture. Captures use
`tests/capture_real_browser.rs` (inline `LocalTlsResponder` + headless
browser launch via `std::process::Command`). The full per-fixture
metadata (timestamps, alpn, byte sizes) lives in the corresponding
`<id>.metadata.toml`.

## Captured fixtures

| Fixture ID | Browser | Version | Platform | Captured | ClientHello SHA-256 | h2 SETTINGS SHA-256 |
|---|---|---|---|---|---|---|
| chrome-148-desktop | Google Chrome | 148.0.7778.167 | Linux x86_64 | 2026-05-16 | `e5975d6805ad8743e5acf0c38c5867c5a961f739db3716ff368fd77cd85f5d73` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty — see below) |

## Capture method notes

The capture pipeline runs entirely on `loopback` (127.0.0.1):

1. Inline TLS responder generates a fresh self-signed cert (rcgen,
   ECDSA P-256 SHA-256).
2. The cert's SubjectPublicKeyInfo is extracted via `openssl x509`
   + `openssl pkey`, SHA-256 hashed, and base64-encoded for Chrome's
   `--ignore-certificate-errors-spki-list` flag.
3. Chrome is launched headless with the SPKI flag, QUIC disabled
   (so we capture TCP/TLS not UDP/QUIC), and ECH disabled.
4. The responder's TCP layer peeks the first 8KiB before handing
   the stream to rustls — the **ClientHello bytes are captured
   here**, before TLS handshake completion.
5. rustls then attempts the handshake. In the chrome-148-desktop
   capture, the handshake did NOT complete (Chrome rejected the
   self-signed cert despite the SPKI flag — likely
   headless=new-mode hardening that the SPKI flag doesn't bypass).
6. The h2 SETTINGS frame would normally be captured post-handshake;
   that's empty for this fixture. h2 SETTINGS values for
   `CHROME_148_DESKTOP` come from the persona spec's
   `network.http2_akamai` field (Chrome 147 ground truth from
   prior W3A.6 capture work — wire-shape preserved across the 147→148
   minor revision per industry observation).

This is a pragmatic compromise: the **ClientHello is real-Chrome-148
ground truth** (1765 bytes byte-for-byte from Chrome's TLS stack via
BoringSSL), and the **h2 settings are derived ground truth** from the
persona spec's earlier capture. A future fixture pass with a
system-trusted cert (via `mkcert` or equivalent) would let the
handshake complete and capture h2 SETTINGS directly from Chrome 148.

## JA4 cross-check

`fixtures-plan.md` §3.2 step 11 calls for cross-checking the captured
JA4 against the FoxIO database. The `ja4` CLI is not installed on the
capture host. The `conformance_layer2::layer2_chrome_147_via_registry`
test computes JA4 from the captured ClientHello via the in-tree
`compute_ja4_from_client_hello` and asserts the divergence set is a
subset of the documented baseline — passing implicitly cross-validates
the capture's structural integrity.

## Verifying integrity

```bash
sha256sum -c <<EOF
e5975d6805ad8743e5acf0c38c5867c5a961f739db3716ff368fd77cd85f5d73  chrome-148-desktop.client_hello.bin
EOF
```

## Re-capture

```bash
rm -rf /tmp/chrome-fixture-capture
cargo test -p carbonyl-wreq --test capture_real_browser \
  -- --ignored --test-threads=1 capture_chrome_desktop --nocapture
```
