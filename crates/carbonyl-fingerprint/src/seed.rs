//! Deterministic noise-seed derivation for `Persona` (`Refs:
//! roctinam/carbonyl-agent#43`, rule H).
//!
//! `canvas.noise_seed` and `audio.noise_seed` are not random — they
//! must be reproducibly derivable from `persona.id`. This lets the
//! validator catch tampering (someone editing a stored persona's id
//! without re-deriving the seeds) and lets the sampler emit personas
//! whose seeds round-trip through serialize → deserialize → validate
//! cleanly.
//!
//! # Construction
//!
//! [HKDF][rfc5869] with SHA-256 as the underlying PRF:
//!
//! ```text
//! IKM  = persona.id (UTF-8 bytes)
//! salt = b"carbonyl-fingerprint-seed-v1"   (public, fixed)
//! info = b"canvas-noise-v1"  for canvas seed
//! info = b"audio-noise-v1"   for audio seed
//! OKM  = first 8 bytes of HKDF-Expand output, big-endian → u64
//! ```
//!
//! Why HKDF and not `SHA-256(id || label)`:
//!
//! - HKDF gives mathematically independent outputs per `info` label, so
//!   `derive_canvas_noise(id)` and `derive_audio_noise(id)` cannot
//!   accidentally collide even for adversarially chosen ids.
//! - HKDF's salt parameter version-prefixes the construction so
//!   bumping to v2 (e.g., changing labels or output length) is a
//!   single-line change; ad-hoc `concat`-and-hash schemes have no
//!   versioning hook.
//! - Follows AIWG `no-adhoc-kdf` rule.
//!
//! Because `persona.id` carries ≥64 bits of entropy (the sampler emits
//! 16-hex-char tags from `rng.gen::<u64>()`), HKDF-Extract treats it as
//! high-entropy IKM. No password-stretching KDF (Argon2id, PBKDF2) is
//! required.
//!
//! # Stability
//!
//! The salt and info labels are part of the wire contract. Changing
//! either invalidates every committed `corpus/personas/*.toml` file.
//! Any change to constants in this module must bump the major schema
//! version in `SCHEMA.md`.
//!
//! [rfc5869]: https://datatracker.ietf.org/doc/html/rfc5869

use hkdf::Hkdf;
use sha2::Sha256;

/// Versioned salt — domain-separates this seed-derivation construction
/// from any other HKDF use of `persona.id` we might add later.
const SEED_DOMAIN_SALT: &[u8] = b"carbonyl-fingerprint-seed-v1";

const CANVAS_INFO: &[u8] = b"canvas-noise-v1";
const AUDIO_INFO: &[u8] = b"audio-noise-v1";

/// Derive `canvas.noise_seed` for the persona with the given id.
pub fn derive_canvas_noise(persona_id: &str) -> u64 {
    derive_u64(persona_id, CANVAS_INFO)
}

/// Derive `audio.noise_seed` for the persona with the given id.
pub fn derive_audio_noise(persona_id: &str) -> u64 {
    derive_u64(persona_id, AUDIO_INFO)
}

fn derive_u64(persona_id: &str, info: &[u8]) -> u64 {
    let hk = Hkdf::<Sha256>::new(Some(SEED_DOMAIN_SALT), persona_id.as_bytes());
    let mut okm = [0u8; 8];
    // HKDF-Expand into 8 bytes — 8 << 32 (max for SHA-256), so .expand
    // cannot fail on length. Unwrap is therefore unreachable in
    // practice; the `.expect` makes that explicit if a future change
    // increases the output size.
    hk.expand(info, &mut okm)
        .expect("HKDF-Expand into 8 bytes is well within the SHA-256 output limit");
    // Mask the top bit so the value always fits in TOML's signed
    // 64-bit integer range (i64::MAX). 63 bits of entropy is far
    // beyond what canvas/audio noise needs and avoids a TOML
    // round-trip surprise where `u64 > i64::MAX` fails to deserialize.
    u64::from_be_bytes(okm) & (i64::MAX as u64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    #[test]
    fn canvas_seed_is_deterministic_for_an_id() {
        let a = derive_canvas_noise("persona-test-valid");
        let b = derive_canvas_noise("persona-test-valid");
        assert_eq!(a, b);
    }

    #[test]
    fn audio_seed_is_deterministic_for_an_id() {
        let a = derive_audio_noise("persona-test-valid");
        let b = derive_audio_noise("persona-test-valid");
        assert_eq!(a, b);
    }

    #[test]
    fn canvas_and_audio_seeds_are_independent() {
        // Same id, different labels → different outputs. Asserting
        // only inequality (rather than a specific value) keeps this
        // test robust to future salt/label tweaks while still catching
        // accidental label collisions.
        let id = "persona-test-valid";
        assert_ne!(derive_canvas_noise(id), derive_audio_noise(id));
    }

    #[test]
    fn distinct_ids_produce_distinct_seeds() {
        // Probability of a 64-bit collision over a handful of ids is
        // ~0; if this fails the construction is broken.
        assert_ne!(
            derive_canvas_noise("persona-a"),
            derive_canvas_noise("persona-b"),
        );
        assert_ne!(
            derive_audio_noise("persona-a"),
            derive_audio_noise("persona-b"),
        );
    }

    #[test]
    fn known_answer_for_persona_test_valid() {
        // Pinned test vector — protects against accidental changes to
        // the salt or label constants. If you intentionally bump
        // SEED_DOMAIN_SALT or *_INFO labels (i.e., schema version bump
        // in SCHEMA.md), regenerate these values and update both the
        // constants and any committed personas in
        // carbonyl-fingerprint-corpus.
        assert_eq!(
            derive_canvas_noise("persona-test-valid"),
            0x2200_1d6d_ac94_4cb3,
            "canvas KAT drifted — see comment for upgrade procedure"
        );
        assert_eq!(
            derive_audio_noise("persona-test-valid"),
            0x0746_4ca2_c413_9cd2,
            "audio KAT drifted — see comment for upgrade procedure"
        );

        // Masked range — top bit always zero so values fit i64.
        assert!(
            derive_canvas_noise("persona-test-valid") <= i64::MAX as u64,
            "canvas seed must fit in TOML signed integer range"
        );
        assert!(
            derive_audio_noise("persona-test-valid") <= i64::MAX as u64,
            "audio seed must fit in TOML signed integer range"
        );
    }
}
