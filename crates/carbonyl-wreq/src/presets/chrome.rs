//! Chrome family presets.
//!
//! # Status: empty pending fixture capture
//!
//! `CHROME_147_DESKTOP` and `CHROME_147_MOBILE_ANDROID` will land here
//! once their respective fixtures are captured per `fixtures-plan.md`:
//!
//! - `CHROME_147_DESKTOP` — Iteration A item 3 (HITL: real Chrome 147
//!   stable on Linux, captured against a localhost TLS+h2 responder).
//! - `CHROME_147_MOBILE_ANDROID` — Iteration B item 1a (HITL: real
//!   Chrome 147 on Android device or emulator).
//!
//! Adding a placeholder entry with invented values would ship a fake
//! fingerprint through tests that pretend to pass; that defeats the
//! conformance purpose of the registry.
