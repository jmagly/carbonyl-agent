# Fixture Capture Plan — Reference Browser Wire Captures

**Status**: Draft
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Refs**: UC-W03, ADR-W03, NFR-W-05

This plan describes how reference browser wire captures (ClientHello + h2 SETTINGS frames) are produced, validated, and committed. Fixtures are the ground truth for Layer 2 conformance. Their integrity is the integrity of the test suite.

## 1. Why fixtures are HITL

A fixture is a record of "what a real browser emits". Producing one requires actually running the browser. We do not automate this for three reasons:

1. **Authenticity** — the value of the fixture is that it came from the actual browser, not from anything we built. Automating capture inside our own code defeats the purpose.
2. **Browser availability** — Safari does not containerize. Mobile profiles require emulators that are not reliably reproducible. macOS-only and iOS-only captures need physical or virtualized Apple hardware.
3. **Verification** — every fixture's JA4 hash is cross-checked against an external corpus before commit. That cross-check is what catches "I captured the wrong thing".

Capture is therefore a documented manual procedure, run once per browser version per platform, with the result committed as a binary artifact alongside provenance.

## 2. What gets captured

For each of the five canonical families:

| Family | Platform | Capture target |
|--------|----------|----------------|
| Chrome 147 stable | Linux desktop | TLS ClientHello + h2 SETTINGS frame to a localhost responder |
| Chrome 147 stable | Android device or emulator | Same |
| Firefox 150 stable | Linux desktop | Same |
| Safari 26 | macOS desktop | Same |
| Safari 26 | iOS device or simulator | Same |

The captured artifact per fixture:

1. **`<fixture-id>.pcap`** — raw packet capture of the connection (`tcpdump -w` or equivalent). Contains the TLS handshake + the first h2 frame exchange.
2. **`<fixture-id>.client_hello.bin`** — extracted ClientHello bytes (TLS record-layer payload of the first record).
3. **`<fixture-id>.h2_settings.bin`** — h2 SETTINGS frame bytes from the first SETTINGS the client sends (after the handshake completes).
4. **`<fixture-id>.metadata.toml`** — provenance entry; one row in the consolidated `PROVENANCE.md` and the raw values inline for diff readability.

All four artifacts live in `crates/carbonyl-wreq/data/fixtures/`.

## 3. Capture procedure (Chrome on Linux — reference)

Other platforms vary in tooling but follow the same shape: launch a real browser pointed at a localhost TLS responder; capture its first request; extract ClientHello + h2 SETTINGS; record provenance.

### 3.1 Environment

- Fresh Chrome 147 stable, installed from the vendor's official channel.
- Default profile only — no extensions, no flags overriding network behavior.
- Loopback TLS responder running on `127.0.0.1:8443` with a self-signed certificate the OS trust store does not trust (so the browser shows a warning we click through; this does not change wire shape).
- `tcpdump -i lo -w fixture-raw.pcap` capturing on loopback.

### 3.2 Steps

1. Verify browser version: `google-chrome --version` exactly matches the persona's declared version.
2. Start `tcpdump -i lo -nn -w chrome-147-desktop.pcap port 8443`.
3. Start a minimal TLS+h2 responder bound to `127.0.0.1:8443` that accepts the connection, completes the handshake, accepts the SETTINGS frame, then closes the stream. The responder logs the raw bytes of the ClientHello and the first incoming SETTINGS frame.
4. In a fresh Chrome profile, navigate to `https://127.0.0.1:8443/`. Click through the certificate warning (`Advanced → Proceed`).
5. Wait for the request to complete (response will be a 200 with an empty body).
6. Stop `tcpdump`. Stop the responder.
7. Extract `client_hello.bin` from the pcap via `tshark -r chrome-147-desktop.pcap -Y 'tls.handshake.type==1' -T fields -e tls.handshake -w client_hello.bin` (or equivalent). Validate the byte length is plausible (typically 300–600 bytes).
8. Extract `h2_settings.bin` from the responder's log.
9. Compute SHA-256 of all three artifacts.
10. Compute the JA4 hash of `client_hello.bin` using the `ja4` CLI from FoxIO.
11. Cross-check the JA4 hash against the FoxIO JA4 database for "Chrome 147 stable Linux". If the hash matches, the capture is validated. If not, restart from step 1 — something in the environment was wrong (extension installed, language pack, etc.).
12. Write `chrome-147-desktop.metadata.toml`:

```toml
fixture_id = "chrome-147-desktop"
browser_product = "Google Chrome"
browser_version = "147.0.6789.42"   # exact build string
platform = "Linux x86_64 / Ubuntu 24.04"
captured_at = "2026-05-XXTHH:MM:SSZ"
captured_by = "<operator handle>"
capture_method = "tcpdump on lo, localhost TLS+h2 responder"

[artifacts]
pcap_path = "chrome-147-desktop.pcap"
pcap_sha256 = "..."
client_hello_path = "chrome-147-desktop.client_hello.bin"
client_hello_sha256 = "..."
h2_settings_path = "chrome-147-desktop.h2_settings.bin"
h2_settings_sha256 = "..."

[validation]
ja4 = "t13d1516h2_8daaf6152771_b0da82dd1658"   # example shape
ja4_source = "FoxIO JA4 database snapshot 2026-05-XX"
ja4_external_match = true
```

13. Append the same row to `data/fixtures/PROVENANCE.md` (the consolidated table).
14. Commit all four files in a single PR titled `fixtures: capture chrome-147-desktop reference`.
15. PR review confirms the JA4 cross-check entry; merge.

### 3.3 Per-platform variations

**Chrome on Android**: capture via `adb` + `tcpdump` on an Android emulator, or `mitmproxy` in transparent-proxy mode on a physical device. The validation step (JA4 cross-check against FoxIO) is identical.

**Firefox on Linux**: same shape as Chrome; Firefox profile is a fresh `firefox -P fixture-capture --no-remote` profile.

**Safari on macOS**: capture via `tcpdump` on `lo0`. Safari trusts only the system keychain — install the responder's self-signed cert into the user keychain first.

**Safari on iOS**: capture via the Network Link Conditioner or mitmproxy in transparent-proxy mode pointing iOS Safari at a localhost responder reachable via the host machine. Provenance must include the iOS version.

## 4. Legal posture

The captures are **observations of public network behavior**. Browsers emit these bytes to every server they talk to; nothing about the capture is non-public information, derivative of copyrighted code, or a trade secret.

We do NOT:

- Copy any `wreq-util` source or data.
- Reverse-engineer browser binaries.
- Decompile or disassemble browser internals.
- Reference any other project's preset tables when authoring our registry.

We DO:

- Capture packets from real browsers running in standard configurations.
- Cross-validate against publicly published JA4 databases.
- Document the provenance of every fixture so an auditor can reproduce it.

The independent capture is what makes our registry MIT-distributable. The provenance documentation is what makes that claim auditable. NFR-W-05 codifies this.

## 5. Fixture refresh policy

A fixture is considered fresh when:

- The browser version it represents is still the persona's declared version.
- The capture date is within 12 months OR the browser has not had a major version bump since capture.

Triggers for re-capture:

| Trigger | Action |
|---------|--------|
| Persona schema bumps browser to a new major version | Re-capture per the procedure |
| External JA4 database publishes a different hash for the same version | Re-capture and investigate the divergence |
| Conformance test fails on a clean local checkout and divergence does not match an expected code change | Investigate first (likely a registry bug); re-capture only if the browser actually changed |
| 12 months elapsed | Re-capture as routine maintenance |

Re-capture follows the same procedure and the metadata block records the supersession (`supersedes_fixture_id = "..."`).

## 6. Iteration scoping

| Iteration | Fixtures required |
|-----------|-------------------|
| A — Chrome desktop proof | `chrome-147-desktop` only |
| B — Five-family rollout | All five canonical families |
| C — Conformance closeout | Same five; re-capture any with provenance older than 6 months at that point |

Fixtures captured for Iteration A are not re-done for Iteration B — they're already there.

## 7. Open questions

- Safari 26 iOS: do we have access to an iOS device with iOS 26? If not, the simulator on Apple silicon is acceptable but provenance must reflect that.
- Mobile Chrome 147 on Android: which Android version do we capture against? Persona schema currently does not distinguish Android versions; default to Android 14 unless the persona schema is extended.

## References

- @.aiwg/tracks/wreq-util-replacement/testing/test-plan.md
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-003-wire-conformance-coverage-strategy.md
- @.aiwg/tracks/wreq-util-replacement/requirements/nfr.md §NFR-W-05
- @.aiwg/architecture/adr-005-tls-fingerprint-http-client.md §"Persona binding contract"
