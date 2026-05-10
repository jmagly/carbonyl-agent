//! Carbonyl fingerprint registry — owned persona bundles for trusted automation.
//!
//! See `roctinam/carbonyl` →
//! `.aiwg/working/trusted-automation/07-fingerprint-registry-design.md`
//! for the authoritative spec.
//!
//! # Layout
//!
//! - [`schema`] — `Persona` struct and nested types; TOML (de)serialization
//! - [`http`] — persona-binding trait for TLS-fingerprint-aware HTTP clients
//!   (ADR-005; backend impls live in W3B / #44)
//! - [`conformance`] — public test scaffold that any [`http::HttpClient`]
//!   backend plugs into to prove its persona → wire output mapping is
//!   correct (W3B prereq, #62)
//! - [`sampler`] — joint-distribution sampler (W3A.2; v1 single-class)
//! - [`validator`] — consistency rules (W3A.3; all 12 SCHEMA.md hard rules)
//! - [`seed`] — deterministic noise-seed derivation (rule H, HKDF-Expand)
//! - [`registry`] — corpus-backed Chrome reference loader (W3A.5; #68)
//! - [`applier`] — persona → Carbonyl CLI flags + content-script bundle (placeholder; Phase 3C)
//! - [`python`] — PyO3 bindings (gated behind the `python` Cargo feature)

pub mod conformance;
pub mod http;
pub mod refresher;
pub mod registry;
pub mod sampler;
pub mod schema;
pub mod seed;
pub mod validator;

#[cfg(feature = "python")]
pub mod python;

pub mod applier {
    //! Persona → Carbonyl application — Phase 3C.
    //!
    //! Derives:
    //! - Carbonyl CLI flags (UA, lang, DPR, user-data-dir)
    //! - Content-script bundle (UA-CH override, navigator.* overrides,
    //!   canvas/audio noise hooks)
    //! - `wreq` client config (via a companion crate)
}

pub use schema::Persona;
