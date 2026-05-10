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
//! - [`sampler`] — joint-distribution sampler (placeholder; Phase 3A.2)
//! - [`validator`] — consistency rules (placeholder; Phase 3A.3)
//! - [`applier`] — persona → Carbonyl CLI flags + content-script bundle (placeholder; Phase 3C)
//! - [`registry`] — in-process registry that loads personas from the corpus (placeholder)

pub mod http;
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

pub mod registry {
    //! In-process registry — loads personas by id. Thin wrapper over the
    //! corpus filesystem layout.
}

pub use schema::Persona;
