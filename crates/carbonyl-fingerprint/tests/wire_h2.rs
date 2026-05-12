//! HTTP/2 SETTINGS capture + Akamai string composition (W3B.2.3 —
//! `Refs: roctinam/carbonyl-agent#78`).
//!
//! After the TLS handshake completes (Layer 2.1 #76) and the client has
//! negotiated ALPN to `h2`, the HTTP/2 connection begins with:
//!
//! 1. Connection preface (24 bytes): `PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`
//! 2. First frame: SETTINGS (type 0x04) listing the client's
//!    initial settings (HEADER_TABLE_SIZE, ENABLE_PUSH, etc.)
//! 3. Subsequent frames: typically WINDOW_UPDATE (connection-level),
//!    then HEADERS for the first request.
//!
//! This module captures the SETTINGS entries and the first
//! connection-level WINDOW_UPDATE value, then composes an Akamai-shaped
//! string that round-trips through [`H2Settings::from_akamai`]. Layer
//! 2.4 (#79) wires the captured values into
//! [`ConformanceFixture::assert_wire_state`].

#![allow(dead_code)] // Test utilities — Layer 2.4 (#79) consumes them.

use std::fmt;

/// One HTTP/2 frame's parsed state. Layer 2 only needs SETTINGS and
/// WINDOW_UPDATE; richer frames are tagged `Other` for completeness.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum H2Frame {
    Settings { entries: Vec<(u16, u32)>, ack: bool },
    WindowUpdate { stream_id: u32, increment: u32 },
    Other { frame_type: u8, length: u32 },
}

/// What [`parse_h2_initial_frames`] returns: the client's first
/// SETTINGS entries plus the first connection-level WINDOW_UPDATE
/// increment, plus the composed Akamai string.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct H2Capture {
    pub settings_entries: Vec<(u16, u32)>,
    pub initial_window_increment: u32,
    /// Composed Akamai string. Round-trippable via
    /// `carbonyl_fingerprint::http::H2Settings::from_akamai`.
    pub akamai_string: String,
}

#[derive(Debug, thiserror::Error)]
pub enum H2CaptureError {
    #[error("missing or invalid HTTP/2 connection preface")]
    BadPreface,
    #[error("HTTP/2 frame truncated at offset {0}")]
    Truncated(usize),
    #[error("first HTTP/2 frame must be SETTINGS, got type 0x{0:02x}")]
    FirstFrameNotSettings(u8),
}

/// The HTTP/2 connection preface — 24 ASCII bytes that every h2 client
/// MUST send before any frames.
pub const H2_CONNECTION_PREFACE: &[u8] = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";

/// Parse a captured byte buffer starting at the HTTP/2 connection
/// preface. Walks frames until either:
/// - A SETTINGS frame and a connection-level WINDOW_UPDATE have both
///   been seen (returns with both populated), or
/// - The buffer is exhausted (returns what was seen; window increment
///   defaults to 0 if no WINDOW_UPDATE arrived in the captured bytes).
///
/// Akamai string composition follows the canonical shape used in the
/// persona schema and the `H2Settings::from_akamai` parser:
/// `<id:val,id:val,...>|<window>|0|m,a,s,p`. PRIORITY frames (third
/// section) and pseudo-header order (fourth) are placeholders here —
/// the current `H2Priority::from_akamai` parser returns
/// `Self::default()` regardless. Layer 2.4 (#79) decides whether to
/// extend those sections.
pub fn parse_h2_initial_frames(buf: &[u8]) -> Result<H2Capture, H2CaptureError> {
    // 1. Connection preface (24 bytes).
    if buf.len() < H2_CONNECTION_PREFACE.len() || !buf.starts_with(H2_CONNECTION_PREFACE) {
        return Err(H2CaptureError::BadPreface);
    }
    let mut cursor = H2_CONNECTION_PREFACE.len();

    let mut settings_entries: Vec<(u16, u32)> = Vec::new();
    let mut initial_window_increment: u32 = 0;
    let mut saw_settings = false;
    let mut saw_window_update = false;

    while cursor + 9 <= buf.len() {
        // Frame header: 3-byte length, 1-byte type, 1-byte flags,
        // 1-byte reserved+stream_id high bit, 4-byte stream_id total.
        // RFC 9113 §4.1.
        let length = (u32::from(buf[cursor]) << 16)
            | (u32::from(buf[cursor + 1]) << 8)
            | u32::from(buf[cursor + 2]);
        let frame_type = buf[cursor + 3];
        let flags = buf[cursor + 4];
        let stream_id = u32::from_be_bytes([
            buf[cursor + 5] & 0x7f,
            buf[cursor + 6],
            buf[cursor + 7],
            buf[cursor + 8],
        ]);
        let payload_start = cursor + 9;
        let payload_end = payload_start + length as usize;
        if payload_end > buf.len() {
            return Err(H2CaptureError::Truncated(cursor));
        }
        let payload = &buf[payload_start..payload_end];

        match frame_type {
            0x04 => {
                // SETTINGS. Payload is a list of (u16 id, u32 value)
                // pairs — 6 bytes each. ACK flag set means no payload.
                if !saw_settings {
                    let ack = (flags & 0x01) != 0;
                    if !ack {
                        let mut p = 0;
                        while p + 6 <= payload.len() {
                            let id = u16::from_be_bytes([payload[p], payload[p + 1]]);
                            let value = u32::from_be_bytes([
                                payload[p + 2],
                                payload[p + 3],
                                payload[p + 4],
                                payload[p + 5],
                            ]);
                            settings_entries.push((id, value));
                            p += 6;
                        }
                    }
                    saw_settings = true;
                }
            }
            0x08 => {
                // WINDOW_UPDATE. 4-byte payload: increment. Connection-
                // level WINDOW_UPDATE has stream_id == 0.
                if !saw_window_update && stream_id == 0 && payload.len() == 4 {
                    let inc = u32::from_be_bytes([payload[0], payload[1], payload[2], payload[3]])
                        & 0x7fff_ffff;
                    initial_window_increment = inc;
                    saw_window_update = true;
                }
            }
            _ => {
                // Other frame types (HEADERS, PRIORITY, etc.) — out of
                // scope for this PR; skip past payload and continue.
            }
        }

        cursor = payload_end;

        // Bail early once we have both pieces of state we need.
        if saw_settings && saw_window_update {
            break;
        }
    }

    if !saw_settings {
        return Err(H2CaptureError::FirstFrameNotSettings(
            buf.get(H2_CONNECTION_PREFACE.len() + 3)
                .copied()
                .unwrap_or(0xff),
        ));
    }

    let akamai_string = compose_akamai(&settings_entries, initial_window_increment);

    Ok(H2Capture {
        settings_entries,
        initial_window_increment,
        akamai_string,
    })
}

/// Compose the Akamai-shape string: `id:val,id:val|window|0|m,a,s,p`.
///
/// Sections:
/// 1. Settings entries — comma-joined `id:value` pairs in observed order
/// 2. Connection-level WINDOW_UPDATE increment
/// 3. PRIORITY summary — `"0"` placeholder (current `H2Priority::from_akamai`
///    parser returns empty regardless; Layer 2.4 decides whether to extend)
/// 4. Pseudo-header order — `"m,a,s,p"` placeholder (HPACK decode needed
///    for the real value; Layer 2.4 decides whether to extend)
pub fn compose_akamai(entries: &[(u16, u32)], window: u32) -> String {
    let settings_part = entries
        .iter()
        .map(|(id, val)| format!("{id}:{val}"))
        .collect::<Vec<_>>()
        .join(",");
    format!("{settings_part}|{window}|0|m,a,s,p")
}

impl fmt::Display for H2Capture {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.akamai_string)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Hand-craft an HTTP/2 connection preface + SETTINGS + WINDOW_UPDATE
    /// byte stream matching the Chrome 147 persona's documented Akamai
    /// settings shape.
    fn chrome_147_preface_and_frames() -> Vec<u8> {
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);

        // SETTINGS frame matching Chrome 147 persona:
        // 1:65536, 2:0, 3:1000, 4:6291456, 6:262144  (5 entries × 6 bytes = 30)
        let settings: Vec<(u16, u32)> =
            vec![(1, 65536), (2, 0), (3, 1000), (4, 6291456), (6, 262144)];
        let mut settings_payload = Vec::with_capacity(settings.len() * 6);
        for (id, val) in &settings {
            settings_payload.extend_from_slice(&id.to_be_bytes());
            settings_payload.extend_from_slice(&val.to_be_bytes());
        }
        // Frame header: length=30, type=SETTINGS, flags=0, stream=0.
        push_frame_header(&mut buf, settings_payload.len() as u32, 0x04, 0, 0);
        buf.extend_from_slice(&settings_payload);

        // WINDOW_UPDATE (stream 0, increment 15663105 — matches Chrome
        // 147 persona's Akamai window section).
        let window_inc: u32 = 15_663_105;
        push_frame_header(&mut buf, 4, 0x08, 0, 0);
        buf.extend_from_slice(&window_inc.to_be_bytes());

        buf
    }

    fn push_frame_header(
        buf: &mut Vec<u8>,
        length: u32,
        frame_type: u8,
        flags: u8,
        stream_id: u32,
    ) {
        buf.push(((length >> 16) & 0xff) as u8);
        buf.push(((length >> 8) & 0xff) as u8);
        buf.push((length & 0xff) as u8);
        buf.push(frame_type);
        buf.push(flags);
        buf.extend_from_slice(&stream_id.to_be_bytes());
    }

    #[test]
    fn parses_chrome_147_handcrafted_preface_and_settings() {
        let buf = chrome_147_preface_and_frames();
        let captured = parse_h2_initial_frames(&buf).expect("parse");

        assert_eq!(
            captured.settings_entries,
            vec![(1, 65536), (2, 0), (3, 1000), (4, 6291456), (6, 262144)]
        );
        assert_eq!(captured.initial_window_increment, 15_663_105);
        assert_eq!(
            captured.akamai_string,
            "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
        );
    }

    #[test]
    fn rejects_missing_preface() {
        let buf = vec![0x00, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00]; // SETTINGS w/o preface
        let err = parse_h2_initial_frames(&buf).expect_err("must fail");
        assert!(matches!(err, H2CaptureError::BadPreface), "got: {err:?}");
    }

    #[test]
    fn rejects_first_frame_not_settings() {
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);
        // PRIORITY frame instead of SETTINGS.
        push_frame_header(&mut buf, 5, 0x02, 0, 1);
        buf.extend_from_slice(&[0, 0, 0, 0, 16]);
        let err = parse_h2_initial_frames(&buf).expect_err("must fail");
        assert!(
            matches!(err, H2CaptureError::FirstFrameNotSettings(0x02)),
            "got: {err:?}"
        );
    }

    #[test]
    fn handles_settings_with_ack_flag_set() {
        // Server's SETTINGS ack arriving from the captured side wouldn't
        // contain entries — make sure we still mark saw_settings so the
        // parser doesn't error out, but settings_entries remains empty.
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);
        // SETTINGS frame with ACK flag (bit 0 of flags = 1), no payload.
        push_frame_header(&mut buf, 0, 0x04, 0x01, 0);
        // WINDOW_UPDATE follows.
        push_frame_header(&mut buf, 4, 0x08, 0, 0);
        buf.extend_from_slice(&100_u32.to_be_bytes());

        let captured = parse_h2_initial_frames(&buf).expect("parse");
        assert!(
            captured.settings_entries.is_empty(),
            "ACK frames carry no entries"
        );
        assert_eq!(captured.initial_window_increment, 100);
    }

    #[test]
    fn returns_zero_window_when_no_window_update_in_buffer() {
        // Only SETTINGS, no WINDOW_UPDATE — captured value should default
        // to 0 (matches Akamai shape for clients that don't bump the
        // connection window).
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);
        push_frame_header(&mut buf, 6, 0x04, 0, 0);
        buf.extend_from_slice(&1u16.to_be_bytes());
        buf.extend_from_slice(&65536u32.to_be_bytes());

        let captured = parse_h2_initial_frames(&buf).expect("parse");
        assert_eq!(captured.initial_window_increment, 0);
        assert_eq!(captured.akamai_string, "1:65536|0|0|m,a,s,p");
    }

    #[test]
    fn ignores_priority_and_other_frames_between_settings_and_window() {
        // SETTINGS, then PRIORITY (skip), then WINDOW_UPDATE.
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);
        // SETTINGS: 1 entry
        push_frame_header(&mut buf, 6, 0x04, 0, 0);
        buf.extend_from_slice(&1u16.to_be_bytes());
        buf.extend_from_slice(&65536u32.to_be_bytes());
        // PRIORITY (type 0x02) on stream 1, 5 bytes payload — ignored.
        push_frame_header(&mut buf, 5, 0x02, 0, 1);
        buf.extend_from_slice(&[0u8; 5]);
        // WINDOW_UPDATE
        push_frame_header(&mut buf, 4, 0x08, 0, 0);
        buf.extend_from_slice(&1234u32.to_be_bytes());

        let captured = parse_h2_initial_frames(&buf).expect("parse");
        assert_eq!(captured.initial_window_increment, 1234);
    }

    #[test]
    fn akamai_string_round_trips_through_h2settings_from_akamai() {
        use carbonyl_fingerprint::http::{H2Settings, H2WindowUpdate};

        let buf = chrome_147_preface_and_frames();
        let captured = parse_h2_initial_frames(&buf).expect("parse");

        // The composed string must parse back through the production
        // parser to the same settings + window. That's the contract
        // Layer 2.4 uses for fixture diffing.
        let parsed_settings =
            H2Settings::from_akamai(&captured.akamai_string).expect("Akamai parse must succeed");
        assert_eq!(parsed_settings.entries, captured.settings_entries);

        let parsed_window = H2WindowUpdate::from_akamai(&captured.akamai_string)
            .expect("Akamai window parse must succeed");
        assert_eq!(parsed_window.0, captured.initial_window_increment);
    }

    #[test]
    fn truncated_frame_returns_truncated_error() {
        let mut buf = Vec::new();
        buf.extend_from_slice(H2_CONNECTION_PREFACE);
        // SETTINGS header claims 100-byte payload, but we only provide
        // a 10-byte payload — should error.
        push_frame_header(&mut buf, 100, 0x04, 0, 0);
        buf.extend_from_slice(&[0u8; 10]);

        let err = parse_h2_initial_frames(&buf).expect_err("must fail");
        assert!(matches!(err, H2CaptureError::Truncated(_)), "got: {err:?}");
    }
}

/// Integration test: drive an actual h2 client against an in-memory
/// duplex stream, capture the bytes the client emits, and parse them
/// through `parse_h2_initial_frames`.
///
/// This exercises the full byte-level shape that Layer 2.4 will see
/// when a real wreq client is on the other side of the LocalTlsResponder.
#[cfg(test)]
mod h2_client_round_trip {
    use super::*;
    use tokio::io::{AsyncReadExt, DuplexStream};

    /// Pump bytes from an in-memory client side into a Vec — emulates
    /// what the server-side TLS stream would deliver in the real
    /// integration.
    async fn drain_to_vec(mut stream: DuplexStream, max_ms: u64) -> Vec<u8> {
        let mut buf = Vec::with_capacity(4096);
        let mut tmp = [0u8; 1024];
        loop {
            match tokio::time::timeout(
                std::time::Duration::from_millis(max_ms),
                stream.read(&mut tmp),
            )
            .await
            {
                Ok(Ok(0)) | Err(_) => break,
                Ok(Ok(n)) => buf.extend_from_slice(&tmp[..n]),
                Ok(Err(_)) => break,
            }
        }
        buf
    }

    #[tokio::test]
    async fn h2_client_emits_parseable_preface_and_settings() {
        // Duplex pair: client writes → server reads.
        let (client_side, server_side) = tokio::io::duplex(8192);

        let client_task = tokio::spawn(async move {
            // h2's high-level client.handshake() sends the preface +
            // initial SETTINGS automatically. We drive a request just
            // far enough to flush those frames, then drop the client.
            let (h2, connection) = h2::client::handshake(client_side).await.unwrap();
            tokio::spawn(async move {
                // The connection task consumes incoming frames; we
                // don't have a server replying, so this will eventually
                // error out, but that's fine — we just need the
                // outbound frames flushed first.
                let _ = connection.await;
            });
            // Make a noop request to trigger the connection setup.
            let mut h2 = h2;
            let req = http::Request::builder()
                .method("GET")
                .uri("https://example.com/")
                .body(())
                .unwrap();
            let _ = h2.send_request(req, true);
            // Give the connection task a moment to flush.
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        });

        let captured_bytes = drain_to_vec(server_side, 500).await;
        let _ = client_task.await;

        assert!(
            captured_bytes.starts_with(H2_CONNECTION_PREFACE),
            "captured bytes must start with H2 preface (first {} bytes captured: {:?})",
            captured_bytes.len(),
            &captured_bytes[..captured_bytes.len().min(32)]
        );

        let captured = parse_h2_initial_frames(&captured_bytes).expect("parse h2 frames");
        // h2 0.4's default client may send an empty SETTINGS frame
        // (all values match the protocol defaults). The parser still
        // accepts this — the Akamai shape just has an empty settings
        // section. The key contract is round-trip integrity.
        let parsed = carbonyl_fingerprint::http::H2Settings::from_akamai(&captured.akamai_string)
            .expect("Akamai must parse back");
        assert_eq!(
            parsed.entries, captured.settings_entries,
            "round-trip: composed Akamai string must parse back to the same entries"
        );
        // And the composed string must follow the persona-schema shape:
        // 4 pipe-separated sections.
        let sections: Vec<&str> = captured.akamai_string.split('|').collect();
        assert_eq!(
            sections.len(),
            4,
            "Akamai shape: settings|window|prio|pseudo"
        );
    }
}
