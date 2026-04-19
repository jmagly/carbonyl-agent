//! Carbonyl fingerprint registry — owned persona bundles for trusted automation.
//!
//! See `roctinam/carbonyl` →
//! `.aiwg/working/trusted-automation/07-fingerprint-registry-design.md`
//! for the authoritative spec.
//!
//! # Layout
//!
//! - [`schema`] — `Persona` struct and nested types; TOML (de)serialization
//! - [`sampler`] — joint-distribution sampler (placeholder; Phase 3A.2)
//! - [`validator`] — consistency rules (placeholder; Phase 3A.3)
//! - [`applier`] — persona → Carbonyl CLI flags + content-script bundle (placeholder; Phase 3C)
//! - [`registry`] — in-process registry that loads personas from the corpus (placeholder)

pub mod schema;

pub mod sampler {
    //! Joint-distribution sampler — Phase 3A.2.
    //!
    //! Will consume the BrowserForge-derived corpus tracked in
    //! `roctinam/carbonyl-fingerprint-corpus` and emit statistically valid
    //! `Persona` bundles given constraint filters.
}

pub mod validator {
    //! Consistency validator — Phase 3A.3.
    //!
    //! Enforces the rules in `SCHEMA.md` of the corpus repo:
    //! - UA ↔ UA-CH agreement
    //! - Chrome version ↔ JA4 / H2 Akamai reference match
    //! - OS ↔ WebGL renderer plausibility
    //! - OS ↔ fonts plausibility
    //! - Locale ↔ timezone plausibility
    //! - hardware_concurrency / device_memory bounds
}

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
