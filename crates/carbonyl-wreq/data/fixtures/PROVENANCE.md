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
| chrome-148-desktop | Google Chrome | 148.0.7778.167 | Linux x86_64 | 2026-05-16 | `6aabf5ef3f19afda8b34ddeb40ef149b79237ea7a532e52f59c315a3b6415a1c` | `7b3adff36a4d97f1b0193a52b2dc2a9a662031d472dd037c29de20d849b477ec` |
| firefox-150-desktop | Mozilla Firefox | 150.0.3 | Linux x86_64 | 2026-05-17 | `3416dc48ef2cf51946508a8a4e2925a1c4d8b9404f0b159aab987df5e83f5fd5` | _(empty — handshake aborted; see note below)_ |

### Firefox 150 capture notes

The Firefox capture's ClientHello was recorded successfully (1874 bytes,
SHA-256 above), but the TLS handshake aborted with `BadCertificate` —
Firefox does not accept the responder's self-signed cert via an
SPKI-bypass flag the way Chrome does. As a result, no h2 SETTINGS
frames were observed and `firefox-150-desktop.h2_settings.bin` is empty.

The `FIREFOX_150_DESKTOP` preset in
`crates/carbonyl-wreq/src/presets/firefox.rs` uses the captured TLS
fields (cipher list, extension order, supported_groups, sigalgs) for
the TLS layer and the persona-declared `network.http2_akamai` values
(`1:65536,4:131072,5:16384|12517377|0|m,p,a,s`, from
`crates/carbonyl-fingerprint/src/conformance.rs:771`) for the h2 layer
until a fixture with a Firefox-trusted cert (or a different bypass
mechanism, such as a `cert9.db` profile with the responder cert
imported) lands.

Firefox 150 ClientHello highlights (parsed from the capture):

- 16 cipher suites in fixed order, **no GREASE**
- Extensions in fixed order, **no permutation** (Chrome 110+ randomizes)
- Supported groups: `X25519MLKEM768, x25519, secp256r1, secp384r1, secp521r1, ffdhe2048, ffdhe3072`
- ALPN: `h2, http/1.1`
- 11 signature algorithms starting `ecdsa_secp256r1_sha256`

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
5. rustls accepts the TLS handshake and immediately sends the
   server-side h2 connection preface (an empty `SETTINGS` frame,
   per RFC 7540 §3.5). This is critical for Chrome 148+: unlike
   earlier versions, Chrome 148 strictly waits for the server
   preface before sending its own preface + SETTINGS. Sending
   `SETTINGS` after the read loop (the pre-`#107` behavior)
   deadlocked the h2 channel and produced an empty h2 capture.
6. The responder accepts **multiple concurrent connections** and
   returns the one that yielded h2 bytes. Chrome 148's network
   service opens parallel preconnect sockets that complete the
   TLS handshake without sending any HTTP/2 data; the real
   navigation socket is opened in parallel. Single-accept
   responders catch the preconnect and miss the real socket.
7. The captured `chrome-148-desktop.h2_settings.bin` contains the
   complete client preface: 24-byte magic (`PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`),
   a `SETTINGS` frame, a `WINDOW_UPDATE` frame, the navigation
   `HEADERS` frame, and a `SETTINGS` ACK. Parsing yields the
   `network.http2_akamai` values directly (see below).

### Real Chrome 148 h2 fingerprint (parsed from the capture)

| Setting | ID | Value |
|---|---|---|
| `HEADER_TABLE_SIZE` | 0x01 | 65536 |
| `ENABLE_PUSH` | 0x02 | 0 |
| `INITIAL_WINDOW_SIZE` | 0x04 | 6291456 |
| `MAX_HEADER_LIST_SIZE` | 0x06 | 262144 |
| `WINDOW_UPDATE` (stream 0) | — | +15663105 |

Reconstructed Akamai-string form:

```
1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p
```

**Note: structural divergence from the persona spec.** The persona
spec's `network.http2_akamai` for Chrome 147 declared
`1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p` — including
`MAX_CONCURRENT_STREAMS=1000` (setting 3). The real Chrome 148
client no longer advertises `MAX_CONCURRENT_STREAMS` in its preface
SETTINGS frame; the wire-shape was NOT preserved across the 147→148
boundary as previously assumed in this document. Downstream personas
should update their `network.http2_akamai` template accordingly. This
divergence is in scope for the follow-up persona refresh, not for
the recapture work in `#107`.

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
6aabf5ef3f19afda8b34ddeb40ef149b79237ea7a532e52f59c315a3b6415a1c  chrome-148-desktop.client_hello.bin
7b3adff36a4d97f1b0193a52b2dc2a9a662031d472dd037c29de20d849b477ec  chrome-148-desktop.h2_settings.bin
EOF
```

## Re-capture

```bash
rm -rf /tmp/chrome-fixture-capture
cargo test -p carbonyl-wreq --test capture_real_browser \
  -- --ignored --test-threads=1 capture_chrome_desktop --nocapture
```

The capture is non-deterministic in the SHA-256 sense — each run
generates a fresh self-signed cert and Chrome's ClientHello varies
slightly (GREASE values, session-ticket extension content) — so the
hashes in this document update on each recapture. The **structural
shape** (TLS record layout, h2 preface contents, the SETTINGS values
above) is stable across runs.
