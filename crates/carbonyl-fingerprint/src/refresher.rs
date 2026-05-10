//! Bounded version-drift refresh for [`Persona`] (W3A.7 — `Refs:
//! roctinam/carbonyl-agent#70`).
//!
//! A refresh bumps fields that age (Chrome version, UA-CH brand list,
//! JA4 / H2 wire fingerprints, UA string) without changing the
//! persona's stable identifiers. The contract:
//!
//! **A refresh MUST**:
//! - Preserve `persona.id` — that is the contract that lets the warm
//!   cache survive
//! - Preserve `canvas.noise_seed` / `audio.noise_seed` — they are
//!   cryptographically derived from `persona.id` (rule H, see
//!   [`crate::seed`]); changing them = changing the persona
//! - Validate post-refresh — sampler-style guarantee that the result
//!   passes [`crate::validator::validate_with_registry`]
//!
//! **A refresh MAY**:
//! - Bump `browser_version` to the latest non-stale major in the
//!   registry (Chrome-only today; per-family registries TBD)
//! - Update `user_agent.full` to embed the new version
//! - Update `user_agent.ua_ch.brands` so the `Google Chrome` /
//!   `Chromium` entries carry the new major
//! - Update `network.ja4`, `network.http2_akamai`, `network.alpn`
//!   from registry reference data
//! - Update `generator_version` to the current corpus version
//! - Set `stale = true` if the registry has no non-stale entry that
//!   could host this persona
//!
//! # API
//!
//! ```ignore
//! use carbonyl_fingerprint::{
//!     refresher::{CorpusRefresher, Refresher, RefreshOutcome},
//!     registry::ChromeRegistry,
//! };
//!
//! let registry = ChromeRegistry::inline_default();
//! let refresher = CorpusRefresher::new(&registry);
//! match refresher.refresh(&mut persona)? {
//!     RefreshOutcome::NoOp => { /* persona was already current */ }
//!     RefreshOutcome::Updated(delta) => { /* persona bumped — write it back */ }
//!     RefreshOutcome::Stale => { /* registry has no path forward; re-sample */ }
//! }
//! ```

use crate::registry::{ChromeReference, ChromeRegistry};
use crate::schema::{BrowserFamily, Persona};
use crate::validator::{self, ValidationReport};
use thiserror::Error;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Bounded version-drift refresh interface.
pub trait Refresher {
    /// Refresh `persona` in place. Returns the outcome:
    /// [`RefreshOutcome::NoOp`] when no fields changed,
    /// [`RefreshOutcome::Updated`] with a delta when fields drifted
    /// forward, [`RefreshOutcome::Stale`] when the registry has no
    /// path that can host this persona.
    fn refresh(&self, persona: &mut Persona) -> Result<RefreshOutcome, RefreshError>;
}

/// Outcome of a refresh attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RefreshOutcome {
    /// Persona was already current. Nothing changed.
    NoOp,
    /// Persona was bumped forward. Fields enumerated in [`RefreshDelta`].
    Updated(RefreshDelta),
    /// Registry has no non-stale entry that could host this persona.
    /// `persona.stale` is set to `true` so consumers can route around
    /// it; identity (id + noise seeds) preserved.
    Stale,
}

/// Field-by-field summary of what a refresh changed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefreshDelta {
    pub from_browser_version: String,
    pub to_browser_version: String,
    pub from_ja4: String,
    pub to_ja4: String,
    pub from_h2_akamai: String,
    pub to_h2_akamai: String,
}

/// Errors surfaced by [`Refresher::refresh`].
#[derive(Debug, Error)]
pub enum RefreshError {
    #[error("persona.browser_version `{0}` is not parseable as MAJOR.MINOR.BUILD.PATCH")]
    BrowserVersionMalformed(String),

    #[error(
        "persona's UA full string `{ua_full}` does not contain its own \
         browser_version `{browser_version}` — refresh cannot rewrite it \
         safely (caller should re-sample instead)"
    )]
    UserAgentNotRewritable {
        ua_full: String,
        browser_version: String,
    },

    #[error("registry has no Chrome entries at all — cannot refresh")]
    RegistryEmpty,

    #[error(
        "refresher does not yet support browser_family `{0}` — only Chrome is \
         backed by a refresh registry today (W3A.6 follow-ups will add per-family \
         registries for Firefox/Safari)"
    )]
    UnsupportedBrowserFamily(&'static str),

    #[error("post-refresh validation failed: {0}")]
    PostValidateFailed(ValidationReport),
}

// ---------------------------------------------------------------------------
// CorpusRefresher
// ---------------------------------------------------------------------------

/// Refresher backed by a [`ChromeRegistry`]. Picks the latest non-stale
/// major as the refresh target.
#[derive(Debug, Clone)]
pub struct CorpusRefresher<'r> {
    registry: &'r ChromeRegistry,
}

impl<'r> CorpusRefresher<'r> {
    pub fn new(registry: &'r ChromeRegistry) -> Self {
        Self { registry }
    }

    /// Pick the highest non-stale major in the registry. `None` if all
    /// entries are stale or the registry is empty.
    fn target(&self) -> Option<&ChromeReference> {
        self.registry
            .known_majors()
            .into_iter()
            .rev() // descending
            .filter_map(|m| self.registry.lookup(m))
            .find(|r| !r.stale)
    }
}

impl Refresher for CorpusRefresher<'_> {
    fn refresh(&self, persona: &mut Persona) -> Result<RefreshOutcome, RefreshError> {
        // The corpus refresher only knows the Chrome registry. Per-family
        // refreshers (Firefox, Safari) will land in W3A.6 follow-ups, each
        // with its own registry type. For now, fail explicitly rather than
        // silently corrupting a non-Chrome persona's UA / UA-CH / JA4.
        if persona.persona.browser_family != BrowserFamily::Chrome {
            return Err(RefreshError::UnsupportedBrowserFamily(
                persona.persona.browser_family.as_str(),
            ));
        }

        if self.registry.is_empty() {
            return Err(RefreshError::RegistryEmpty);
        }

        let Some(target) = self.target() else {
            // Every entry is stale; no migration path. Mark and bail.
            persona.persona.stale = true;
            return Ok(RefreshOutcome::Stale);
        };

        // Snapshot current state for delta + identity-preservation.
        let id_before = persona.persona.id.clone();
        let canvas_before = persona.persona.canvas.noise_seed;
        let audio_before = persona.persona.audio.noise_seed;
        let from_browser_version = persona.persona.browser_version.clone();
        let from_ja4 = persona.persona.network.ja4.clone();
        let from_h2_akamai = persona.persona.network.http2_akamai.clone();

        let from_major = parse_chrome_major(&from_browser_version)
            .ok_or_else(|| RefreshError::BrowserVersionMalformed(from_browser_version.clone()))?;
        let to_major = target.major;

        // Bump browser_version + UA full + UA-CH brands. The UA full
        // string format we trust is the SCHEMA.md exemplar shape:
        // `... Chrome/<browser_version> Safari/537.36`. We splice
        // browser_version verbatim, so the substring contract from
        // validator rule 1 holds by construction.
        if !persona
            .persona
            .user_agent
            .full
            .contains(&from_browser_version)
        {
            return Err(RefreshError::UserAgentNotRewritable {
                ua_full: persona.persona.user_agent.full.clone(),
                browser_version: from_browser_version,
            });
        }

        persona.persona.user_agent.full = persona
            .persona
            .user_agent
            .full
            .replace(&from_browser_version, &target.version);

        // UA-CH brand bump: replace any "Google Chrome" or "Chromium"
        // entry's version field with `to_major.to_string()`. Other
        // brands (Not_A Brand, etc.) keep their own quirks.
        let to_major_str = to_major.to_string();
        for (name, ver) in persona.persona.user_agent.ua_ch.brands.iter_mut() {
            if name == "Google Chrome" || name == "Chromium" {
                *ver = to_major_str.clone();
            }
        }

        persona.persona.browser_version = target.version.clone();
        persona.persona.network.ja4 = target.ja4.clone();
        persona.persona.network.http2_akamai = target.h2_akamai.clone();
        persona.persona.network.alpn = target.alpn.clone();

        // Identity invariants — the whole point of the refresh
        // contract. These are debug_assert! because the code above
        // never touches them; if a future edit breaks the invariant we
        // want the test suite to catch it before release.
        debug_assert_eq!(persona.persona.id, id_before);
        debug_assert_eq!(persona.persona.canvas.noise_seed, canvas_before);
        debug_assert_eq!(persona.persona.audio.noise_seed, audio_before);

        // Validate post-refresh against the same registry we used for
        // the bump. If this fails, the persona is in an inconsistent
        // state and we should NOT return success.
        if let Err(report) = validator::validate_with_registry(persona, self.registry) {
            return Err(RefreshError::PostValidateFailed(report));
        }

        // Detect NoOp: nothing observable drifted.
        if from_major == to_major
            && from_ja4 == persona.persona.network.ja4
            && from_h2_akamai == persona.persona.network.http2_akamai
        {
            return Ok(RefreshOutcome::NoOp);
        }

        Ok(RefreshOutcome::Updated(RefreshDelta {
            from_browser_version,
            to_browser_version: persona.persona.browser_version.clone(),
            from_ja4,
            to_ja4: persona.persona.network.ja4.clone(),
            from_h2_akamai,
            to_h2_akamai: persona.persona.network.http2_akamai.clone(),
        }))
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn parse_chrome_major(version: &str) -> Option<u32> {
    version.split('.').next()?.parse::<u32>().ok()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sampler::{PersonaClass, Sampler};
    use pretty_assertions::assert_eq;
    use rand::rngs::StdRng;
    use rand::SeedableRng;

    fn fresh_persona() -> Persona {
        let s = Sampler::new();
        let mut rng = StdRng::seed_from_u64(0x1357_9bdf_2468_ace0);
        s.sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .expect("sampler emits valid persona")
    }

    /// Registry with Chrome 147 only (matches `inline_default`).
    fn registry_with_only_147() -> ChromeRegistry {
        ChromeRegistry::inline_default()
    }

    /// Registry where 147 is stale and 146 is the fallback. Lets the
    /// "Stale" branch fire without inventing JA4/H2 values.
    fn registry_all_stale() -> ChromeRegistry {
        // Construct via the corpus-loader path is nontrivial; build a
        // hand-rolled registry instead by writing a temp corpus.
        let dir = scratch_dir();
        let chrome_dir = dir.path().join("corpus").join("chrome");
        std::fs::create_dir_all(&chrome_dir).unwrap();
        std::fs::write(
            chrome_dir.join("chrome-147.toml"),
            r#"
[chrome]
major = 147
version = "147.0.7727.94"
stable_since = "2026-04-10"
marked_stale_at = "2026-05-01"

[chrome.fingerprints]
ja4 = "t13d1516h2_8daaf6152771_02713d6af862"
h2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
post_quantum = true
"#,
        )
        .unwrap();
        let reg = ChromeRegistry::from_corpus_dir(dir.path()).expect("load");
        std::mem::forget(dir); // leak the scratch dir — registry borrows nothing
        reg
    }

    // -------- NoOp: persona is already current --------

    #[test]
    fn refresh_is_noop_when_persona_matches_target() {
        let mut p = fresh_persona();
        let reg = registry_with_only_147();
        let r = CorpusRefresher::new(&reg);
        let outcome = r.refresh(&mut p).expect("refresh ok");
        assert_eq!(outcome, RefreshOutcome::NoOp);
    }

    // -------- Identity preservation --------

    #[test]
    fn refresh_preserves_persona_id_and_seeds() {
        let mut p = fresh_persona();
        let id_before = p.persona.id.clone();
        let canvas_before = p.persona.canvas.noise_seed;
        let audio_before = p.persona.audio.noise_seed;

        let reg = registry_with_only_147();
        let r = CorpusRefresher::new(&reg);
        let _ = r.refresh(&mut p).expect("refresh ok");

        assert_eq!(p.persona.id, id_before);
        assert_eq!(p.persona.canvas.noise_seed, canvas_before);
        assert_eq!(p.persona.audio.noise_seed, audio_before);
    }

    // -------- Stale outcome: every registry entry is stale --------

    #[test]
    fn refresh_marks_stale_when_no_fresh_entry_in_registry() {
        let mut p = fresh_persona();
        let reg = registry_all_stale();
        let r = CorpusRefresher::new(&reg);
        let outcome = r.refresh(&mut p).expect("refresh returns Ok(Stale)");
        assert_eq!(outcome, RefreshOutcome::Stale);
        assert!(
            p.persona.stale,
            "persona.stale must be set on Stale outcome"
        );
    }

    // -------- Empty-registry error --------

    #[test]
    fn refresh_errors_on_empty_registry() {
        let mut p = fresh_persona();
        let dir = scratch_dir();
        std::fs::create_dir_all(dir.path().join("corpus").join("chrome")).unwrap();
        let reg = ChromeRegistry::from_corpus_dir(dir.path()).unwrap();
        assert!(reg.is_empty());

        let r = CorpusRefresher::new(&reg);
        let err = r.refresh(&mut p).expect_err("must error");
        assert!(matches!(err, RefreshError::RegistryEmpty));
    }

    // -------- UA-rewrite guard --------

    #[test]
    fn refresh_errors_when_ua_full_does_not_contain_browser_version() {
        let mut p = fresh_persona();
        // Corrupt the UA so the browser_version substring isn't present.
        p.persona.user_agent.full = "Mozilla/5.0 (Custom UA without version)".to_string();
        let reg = registry_with_only_147();
        let r = CorpusRefresher::new(&reg);
        let err = r.refresh(&mut p).expect_err("must error");
        assert!(matches!(err, RefreshError::UserAgentNotRewritable { .. }));
    }

    #[test]
    fn refresh_errors_for_non_chrome_family() {
        // Per-family refreshers will land alongside per-family templates in
        // W3A.6 follow-ups. Until then, the corpus refresher (Chrome-only)
        // must reject non-Chrome personas explicitly rather than corrupting
        // their UA / UA-CH / JA4 by treating them as Chrome.
        let mut p = fresh_persona();
        p.persona.browser_family = BrowserFamily::Firefox;
        let reg = registry_with_only_147();
        let r = CorpusRefresher::new(&reg);
        let err = r.refresh(&mut p).expect_err("must error");
        assert!(matches!(
            err,
            RefreshError::UnsupportedBrowserFamily("firefox")
        ));
    }

    // -------- Round-trip: refresh then validate --------

    #[test]
    fn refresh_then_validate_round_trip_passes() {
        let mut p = fresh_persona();
        let reg = registry_with_only_147();
        let r = CorpusRefresher::new(&reg);
        r.refresh(&mut p).expect("refresh ok");
        validator::validate_with_registry(&p, &reg).expect("post-refresh validate");
    }

    // Property: any sampler output, after refresh against any
    // non-empty registry, validates cleanly. The strongest guarantee
    // the refresher offers. (Using a normal comment because rustdoc
    // doesn't render docs from inside the proptest! macro.)
    proptest::proptest! {
        #[test]
        fn prop_refresh_then_validate_holds_for_any_seed(seed: u64) {
            let mut rng = StdRng::seed_from_u64(seed);
            let s = Sampler::new();
            let mut p = s
                .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
                .expect("sample ok");
            let reg = ChromeRegistry::inline_default();
            let r = CorpusRefresher::new(&reg);
            let outcome = r.refresh(&mut p).expect("refresh ok");
            // Stale outcome is never produced by inline_default (147 is
            // fresh), so we should always see NoOp here.
            proptest::prop_assert!(matches!(outcome, RefreshOutcome::NoOp));
            validator::validate_with_registry(&p, &reg)
                .expect("post-refresh validate");
        }
    }

    // ----- helpers (mirror of registry::tests::tempdir, local to this module) -----

    struct ScratchDir(std::path::PathBuf);
    impl ScratchDir {
        fn path(&self) -> &std::path::Path {
            &self.0
        }
    }
    impl Drop for ScratchDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    fn scratch_dir() -> ScratchDir {
        let base = std::env::temp_dir().join("carbonyl-fingerprint-tests");
        let _ = std::fs::create_dir_all(&base);
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default();
        let pid = std::process::id();
        let path = base.join(format!("refresher-test-{pid}-{stamp}"));
        std::fs::create_dir_all(&path).unwrap();
        ScratchDir(path)
    }
}
