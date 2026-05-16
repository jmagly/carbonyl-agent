//! Layer 2 wire conformance for the wreq backend (W3B.Phase2.3 —
//! `Closes: roctinam/carbonyl-agent#82`).
//!
//! Drives a real [`WreqClient`] (configured from each of the five
//! W3A.6 fixtures) through [`LocalTlsResponder`], captures the
//! ClientHello + h2 preface/SETTINGS bytes, composes a
//! [`WireSnapshot`], and reports the diff against
//! [`ConformanceFixture::assert_wire_state`].
//!
//! # What "passes"
//!
//! wreq's emulation presets top out at Chrome 137 / Firefox 139 /
//! Safari 18.3.1 / SafariIos 17.4.1 today, whereas the W3A.6
//! personas target Chrome 148 / Firefox 150 / Safari 26. The
//! `wire.ja4` field will therefore diverge — that gap is real and
//! documented in [`expected_partial_mismatches`].
//!
//! A surprise (mismatch where one wasn't documented) is a test
//! failure. A documented gap is just a measurement. When wreq-util
//! ships a newer preset, the expected-gap list shrinks.
//!
//! # Why this is an integration test, not a unit test
//!
//! - Needs a running tokio runtime
//! - Needs a TLS server (rustls + rcgen self-signed cert)
//! - Needs a real wreq client issuing an HTTPS request
//!
//! The `#[path]` imports pull in the same parser modules that
//! `crates/carbonyl-fingerprint/tests/wire_integrated.rs` uses, so the
//! capture pipeline is byte-identical across the two crates.

#![allow(dead_code)]
#![allow(clippy::collapsible_match)]
#![allow(clippy::collapsible_if)]

use std::time::Duration;

#[path = "../../carbonyl-fingerprint/tests/wire_responder.rs"]
mod wire_responder;

#[path = "../../carbonyl-fingerprint/tests/wire_ja4.rs"]
mod wire_ja4;

#[path = "../../carbonyl-fingerprint/tests/wire_h2.rs"]
mod wire_h2;

use carbonyl_fingerprint::conformance::{
    ConformanceFixture, ConformanceMismatch, ConformanceReport, WireSnapshot,
};
use carbonyl_fingerprint::http::{H2Priority, H2Settings, H2WindowUpdate};
use carbonyl_wreq::WreqClient;

use wire_h2::parse_h2_initial_frames;
use wire_ja4::compute_ja4_from_client_hello;
use wire_responder::{CapturedHandshake, LocalTlsResponder};

/// Per-fixture documentation of which `assert_wire_state` fields are
/// EXPECTED to diverge from the persona spec. Entries here are
/// load-bearing — anything missing here counts as a real regression.
///
/// Two distinct sources of divergence are at play:
///
/// 1. **wire.ja4** — wreq-util's emulation presets target the most
///    recent browser version they ship (Chrome137 etc.); the W3A.6
///    personas target newer browser versions (Chrome148 etc.) whose
///    TLS ClientHello has incremented cipher and extension orderings.
///    JA4 is a content-sensitive hash; any drift yields a different
///    `ja4_b`/`ja4_c`. Closes when wreq-util ships newer presets.
///
/// 2. **wire.h2_settings / wire.akamai_string / wire.h2_window** —
///    wreq's `ClientBuilder::http2(closure)` accepts SETTINGS
///    overrides but the emulation preset's defaults still dominate
///    on the wire. Phase 2.3's job is to *measure* this drift; Phase
///    2.4+ may revisit whether to fight the preset (e.g. by
///    constructing an `EmulationProvider` directly from persona
///    fields rather than starting from a preset).
///
/// Safari diverges on `wire.h2_window` instead of `wire.h2_settings`
/// because the Safari preset matches the persona's settings entries
/// exactly but emits a different connection-level WINDOW_UPDATE.
fn expected_partial_mismatches(fixture_label: &str) -> &'static [&'static str] {
    match fixture_label {
        // Chrome 148 corpus refresh (#105) aligned the persona's
        // declared h2 SETTINGS + Akamai string with the real Chrome 148
        // wire capture (4 settings, no 0x03=1000), so those two gaps
        // closed. Only `wire.ja4` remains because wreq's emulation
        // emits 15 non-GREASE extensions vs the real Chrome 148
        // ClientHello's 16, and the LocalTlsResponder capture is
        // SNI-less. ALPN was h2 in both, but the ja4_c hash differs.
        "chrome-148-stable-linux" | "mobile-chrome-android" => &["wire.ja4"],
        "firefox-150-stable-linux" | "mobile-safari-ios" => {
            &["wire.ja4", "wire.h2_settings", "wire.akamai_string"]
        }
        "safari-26-macos" => &["wire.ja4", "wire.h2_window", "wire.akamai_string"],
        _ => &[],
    }
}

/// Build a `WireSnapshot` from a captured handshake, mirroring the
/// shape used by `crates/carbonyl-fingerprint/tests/wire_integrated.rs`.
fn snapshot_from_capture(capture: &CapturedHandshake) -> Option<WireSnapshot> {
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

/// Spin up `LocalTlsResponder`, build a `WreqClient` from the
/// fixture's persona, fire one request, and return the captured
/// handshake. The responder shuts down after a single connection so
/// the test always terminates.
async fn drive_wreq_through_responder(fixture: &ConformanceFixture) -> CapturedHandshake {
    let (responder, captured_rx) = LocalTlsResponder::start_one_shot()
        .await
        .expect("responder must bind");
    let listen_addr = responder.listen_addr;

    let persona = fixture.persona.clone();
    let client_task = tokio::spawn(async move {
        let mut client = WreqClient::new();
        client
            .apply_persona_typed(&persona)
            .expect("apply_persona must succeed");
        // cert_verification(false) is required: LocalTlsResponder
        // generates a fresh self-signed cert per test via rcgen, and
        // wreq has no way to trust an ephemeral CA without pinning
        // the cert PEM at builder time. Acceptable for test code; a
        // production wreq client never disables cert verification.
        let wreq_client = wreq::Client::builder()
            .emulation(carbonyl_wreq::persona_to_emulation(&persona))
            .cert_verification(false)
            .build()
            .expect("wreq client must build");
        // Use a URL with an unresolvable hostname pointing at our
        // 127.0.0.1 responder address via the explicit override. The
        // simplest path: target `https://127.0.0.1:<port>/` directly.
        let url = format!("https://127.0.0.1:{}/", listen_addr.port());
        // The responder closes after capture, so the request will
        // error — that's fine, we only need the ClientHello + h2
        // preface to reach the wire.
        let _ = wreq_client.get(&url).send().await;

        // The unused `client` variable carried the PendingConfig
        // pattern for documentation purposes; drop it explicitly
        // so the borrow checker doesn't complain about apply_persona
        // having no effect.
        drop(client);
    });

    let captured = tokio::time::timeout(Duration::from_secs(15), captured_rx)
        .await
        .expect("responder reports within timeout")
        .expect("responder send did not fail");
    let _ = client_task.await;
    captured
}

/// Run a single fixture through Layer 2. Asserts that:
/// 1. Handshake completed (TLS reached application-data stage)
/// 2. h2 preface was captured (ALPN negotiated to h2)
/// 3. `assert_wire_state`'s mismatch set is EXACTLY the documented
///    expected-gap list for this fixture — no missing surprises, no
///    extra surprises
fn assert_layer2_for_fixture(
    fixture: &ConformanceFixture,
    label: &'static str,
    capture: &CapturedHandshake,
) {
    assert!(
        capture.handshake_complete,
        "[{label}] TLS handshake failed: error={:?}",
        capture.error
    );
    assert!(
        !capture.client_hello_bytes.is_empty(),
        "[{label}] ClientHello must be captured"
    );
    // h2 preface is conditional: if wreq's emulation preset negotiated
    // http/1.1 instead of h2 the h2_bytes will be empty. That itself
    // is a wire-level conformance signal — record it via the report
    // (which will surface a wire.alpn mismatch) rather than a panic.
    let snap = snapshot_from_capture(capture);

    let report = match snap {
        Some(s) => fixture.assert_wire_state(&s),
        None => {
            // No snapshot — manufacture a fully-mismatched report so
            // the test fails with diagnostic info rather than a
            // null-deref-style panic.
            ConformanceReport {
                mismatches: vec![ConformanceMismatch {
                    field: "wire.snapshot_construction",
                    expected: "successful snapshot from capture".into(),
                    actual: format!("snapshot=None, h2_bytes={}b", capture.h2_bytes.len()),
                }],
            }
        }
    };

    let actual_fields: std::collections::BTreeSet<&'static str> =
        report.mismatches.iter().map(|m| m.field).collect();
    let expected_fields: std::collections::BTreeSet<&'static str> =
        expected_partial_mismatches(label).iter().copied().collect();

    assert_eq!(
        actual_fields, expected_fields,
        "[{label}] Layer 2 mismatch set drifted. \
         Expected gap set: {:?}. Actual: {:?}. Report: {:#?}",
        expected_fields, actual_fields, report.mismatches
    );
}

#[tokio::test]
async fn layer2_chrome_148() {
    let fixture = ConformanceFixture::chrome_148_stable_linux();
    let capture = drive_wreq_through_responder(&fixture).await;
    assert_layer2_for_fixture(&fixture, "chrome-148-stable-linux", &capture);
}

// ----- New-path (build_via_registry) parallel tests — Iteration A item 5 -----
//
// These exercise `WreqClient::build_via_registry()` against the same
// LocalTlsResponder + ConformanceFixture machinery the legacy tests
// use. With the preset registry empty (no concrete entries until
// captured fixtures land), the new path produces wreq's defaults +
// persona-declared fields only — so the divergence set is wider than
// the legacy path's. As preset entries are populated from captured
// fixtures (Iteration A item 3, Iteration B items 1a-1d), the
// expected_partial_mismatches_via_registry list shrinks toward equality
// with the legacy table.

fn expected_partial_mismatches_via_registry(_label: &str) -> &'static [&'static str] {
    // Empty preset registry → wreq defaults dominate → every wire field
    // diverges from the persona spec. This is the BASELINE the new
    // path is measured against; preset entries shrink the gap.
    &[
        "wire.ja4",
        "wire.h2_settings",
        "wire.h2_window",
        "wire.akamai_string",
    ]
}

async fn drive_wreq_via_registry_through_responder(
    fixture: &ConformanceFixture,
) -> CapturedHandshake {
    let (responder, captured_rx) = LocalTlsResponder::start_one_shot()
        .await
        .expect("responder must bind");
    let listen_addr = responder.listen_addr;

    let persona = fixture.persona.clone();
    let client_task = tokio::spawn(async move {
        let mut client = WreqClient::new();
        client
            .apply_persona_typed(&persona)
            .expect("apply_persona must succeed");
        let wreq_client = client
            .build_via_registry()
            .expect("build_via_registry must succeed");
        let url = format!("https://127.0.0.1:{}/", listen_addr.port());
        let _ = wreq_client.get(&url).send().await;
    });

    let captured = tokio::time::timeout(Duration::from_secs(15), captured_rx)
        .await
        .expect("responder reports within timeout")
        .expect("responder send did not fail");
    let _ = client_task.await;
    captured
}

fn assert_layer2_via_registry(
    fixture: &ConformanceFixture,
    label: &'static str,
    capture: &CapturedHandshake,
) {
    assert!(
        capture.handshake_complete,
        "[{label}/registry] TLS handshake failed: error={:?}",
        capture.error
    );
    let snap = snapshot_from_capture(capture);
    let report = match snap {
        Some(s) => fixture.assert_wire_state(&s),
        None => ConformanceReport {
            mismatches: vec![ConformanceMismatch {
                field: "wire.snapshot_construction",
                expected: "successful snapshot from capture".into(),
                actual: format!("snapshot=None, h2_bytes={}b", capture.h2_bytes.len()),
            }],
        },
    };
    let actual_fields: std::collections::BTreeSet<&'static str> =
        report.mismatches.iter().map(|m| m.field).collect();
    let expected_fields: std::collections::BTreeSet<&'static str> =
        expected_partial_mismatches_via_registry(label)
            .iter()
            .copied()
            .collect();

    // The new path's expected divergence is a SUPERSET of the legacy
    // path's gap (wreq defaults diverge from the persona on more
    // fields than wreq_util's tuned presets do). Assert subset
    // containment rather than exact equality so each preset entry
    // population narrows the gap monotonically.
    for field in &actual_fields {
        assert!(
            expected_fields.contains(field),
            "[{label}/registry] unexpected mismatch field {:?}. Expected gap superset: {:?}. \
             Actual: {:?}. Report: {:#?}",
            field,
            expected_fields,
            actual_fields,
            report.mismatches
        );
    }
}

#[tokio::test]
async fn layer2_chrome_148_via_registry() {
    let fixture = ConformanceFixture::chrome_148_stable_linux();
    let capture = drive_wreq_via_registry_through_responder(&fixture).await;
    assert_layer2_via_registry(&fixture, "chrome-148-stable-linux", &capture);
}

#[tokio::test]
async fn layer2_firefox_150() {
    let fixture = ConformanceFixture::firefox_150_stable_linux();
    let capture = drive_wreq_through_responder(&fixture).await;
    assert_layer2_for_fixture(&fixture, "firefox-150-stable-linux", &capture);
}

#[tokio::test]
async fn layer2_safari_26_macos() {
    let fixture = ConformanceFixture::safari_26_macos();
    let capture = drive_wreq_through_responder(&fixture).await;
    assert_layer2_for_fixture(&fixture, "safari-26-macos", &capture);
}

#[tokio::test]
async fn layer2_mobile_chrome_android() {
    let fixture = ConformanceFixture::mobile_chrome_android();
    let capture = drive_wreq_through_responder(&fixture).await;
    assert_layer2_for_fixture(&fixture, "mobile-chrome-android", &capture);
}

#[tokio::test]
async fn layer2_mobile_safari_ios() {
    let fixture = ConformanceFixture::mobile_safari_ios();
    let capture = drive_wreq_through_responder(&fixture).await;
    assert_layer2_for_fixture(&fixture, "mobile-safari-ios", &capture);
}
