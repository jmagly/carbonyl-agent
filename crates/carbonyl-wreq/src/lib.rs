//! wreq-backed implementation of the
//! [`carbonyl_fingerprint::http::HttpClient`] trait (W3B Phase 2 —
//! `Refs: roctinam/carbonyl-agent#75`).
//!
//! # Phase 2.1 status: scaffold only
//!
//! This crate exists so a downstream PR (Phase 2.2 — #81) can wire
//! the persona's fingerprint fields into wreq's
//! `Emulation`/`ClientBuilder` API. Right now, [`WreqClient`] is a
//! pending-state recorder: the trait setters accept values and
//! store them in [`PendingConfig`], but [`WreqClient::build`] is a
//! `todo!()` until Phase 2.2 lands the mapping.
//!
//! The crate still produces a green CI signal because:
//! 1. The trait surface compiles — proves wreq's API is reachable
//!    from the `HttpClient` shape without a major version bump.
//! 2. [`PendingConfig`] satisfies
//!    [`carbonyl_fingerprint::http::ApplyInspector`], so the Layer 1
//!    conformance harness (#62, #79) can already round-trip a
//!    persona through it.
//!
//! See [`carbonyl_fingerprint::conformance`] for the harness shape
//! that #81 will run against.

#![deny(rust_2018_idioms)]

pub mod client;

pub use client::{persona_to_emulation, PendingConfig, WreqClient, WreqError};

#[cfg(feature = "python")]
mod python;
