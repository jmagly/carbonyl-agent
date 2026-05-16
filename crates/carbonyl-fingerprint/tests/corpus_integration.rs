//! Integration test exercising [`ChromeRegistry::from_corpus_dir`]
//! against the real `roctinam/carbonyl-fingerprint-corpus` checkout if
//! one is available as a sibling directory.
//!
//! Skipped silently when the sibling repo is absent — keeps `cargo
//! test` green for contributors who haven't cloned the corpus.
//!
//! Refs: roctinam/carbonyl-agent#68 (consumer), roctinam/carbonyl-fingerprint-corpus#1 (data).

use carbonyl_fingerprint::registry::ChromeRegistry;
use carbonyl_fingerprint::validator;
use std::path::PathBuf;

/// Resolve the corpus path. Order:
///
/// 1. `CARBONYL_FINGERPRINT_CORPUS_DIR` env var (CI override)
/// 2. `../carbonyl-fingerprint-corpus` relative to this crate (the
///    sibling-clone layout used in `/srv/vmshare/dev-inbox/carbonyl/`)
///
/// Returns `None` if neither resolves to a directory.
fn corpus_dir() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("CARBONYL_FINGERPRINT_CORPUS_DIR") {
        let path = PathBuf::from(p);
        if path.is_dir() {
            return Some(path);
        }
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let sibling = manifest
        .parent() // crates/
        .and_then(|p| p.parent()) // carbonyl-agent/
        .map(|p| p.join("../carbonyl-fingerprint-corpus"))
        .map(|p| {
            // Canonicalize fails on missing paths; fall back to the
            // raw join so the existence check below sees a sensible
            // path either way.
            std::fs::canonicalize(&p).unwrap_or(p)
        })?;
    sibling.is_dir().then_some(sibling)
}

#[test]
fn loads_chrome_148_from_real_corpus_when_available() {
    let Some(corpus) = corpus_dir() else {
        eprintln!(
            "skipping: carbonyl-fingerprint-corpus not found as a sibling. \
             Set CARBONYL_FINGERPRINT_CORPUS_DIR to override."
        );
        return;
    };

    let reg = ChromeRegistry::from_corpus_dir(&corpus).expect("real corpus should load cleanly");

    // Chrome 148 must be present (per corpus#1 seeding).
    let r = reg
        .lookup(148)
        .expect("real corpus should carry chrome-148 entry (corpus#1)");
    assert_eq!(r.major, 148);
    assert_eq!(r.version, "148.0.7778.167");
    assert_eq!(r.ja4, "t13d1516h2_8daaf6152771_773c5fd3846b");
    assert!(!r.stale);
    // ALPN order is part of the wire contract.
    assert_eq!(r.alpn, vec!["h2".to_string(), "http/1.1".to_string()]);
}

#[test]
fn corpus_chrome_148_matches_inline_default() {
    // Single source of truth check: the corpus's chrome-148 entry must
    // produce the exact same ChromeReference projection as
    // `ChromeRegistry::inline_default()`. Drift here means the inline
    // fallback is lying about Chrome 148's fingerprints.
    let Some(corpus) = corpus_dir() else {
        eprintln!("skipping: corpus not available");
        return;
    };

    let from_corpus =
        ChromeRegistry::from_corpus_dir(&corpus).expect("real corpus should load cleanly");
    let from_inline = ChromeRegistry::inline_default();

    assert_eq!(
        from_corpus.lookup(148).expect("corpus has 148"),
        from_inline.lookup(148).expect("inline has 148"),
        "inline default has drifted from the corpus chrome-148.toml — \
         update one or the other"
    );
}

#[test]
fn validate_with_corpus_registry_matches_validate_default() {
    // The same persona must validate the same way whether the caller
    // uses validate() (inline default) or validate_with_registry()
    // with the real corpus. A divergence here is a regression.
    let Some(corpus) = corpus_dir() else {
        eprintln!("skipping: corpus not available");
        return;
    };

    // Use the canonical persona-test-valid TOML the validator's own
    // tests rely on. Inline copy here — keeping the integration test
    // self-contained avoids reaching across module boundaries.
    let toml = canonical_persona_toml();
    let persona: carbonyl_fingerprint::Persona =
        toml::from_str(&toml).expect("canonical persona parses");

    validator::validate(&persona).expect("validate() passes");
    let reg = ChromeRegistry::from_corpus_dir(&corpus).expect("corpus loads");
    validator::validate_with_registry(&persona, &reg)
        .expect("validate_with_registry(corpus) passes");
}

fn canonical_persona_toml() -> String {
    // Lifted from the validator's VALID_PERSONA_TOML fixture (id
    // "persona-test-valid", Chrome 148, derived noise seeds). Kept in
    // sync by hand; if the validator's fixture changes, this test
    // will fail loudly rather than silently diverge.
    r#"
[persona]
id = "persona-test-valid"
generator_version = "2026.04.18"
browser_family = "chrome"
browser_version = "148.0.7778.167"
release_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7778.167 Safari/537.36"

[persona.user_agent.ua_ch]
brands = [["Chromium", "148"], ["Not_A Brand", "8"], ["Google Chrome", "148"]]
mobile = false
platform = "Linux"
platform_version = "6.8.0"
architecture = "x86"
bitness = "64"

[persona.locale]
accept_language = "en-US,en;q=0.9"
timezone = "America/New_York"
languages = ["en-US", "en"]

[persona.device]
screen_width = 1920
screen_height = 1080
color_depth = 24
device_pixel_ratio = 1.0
hardware_concurrency = 8
device_memory = 8
max_touch_points = 0

[persona.webgl]
vendor = "Google Inc. (Intel)"
renderer = "ANGLE (Intel, Mesa Intel(R) UHD Graphics, OpenGL 4.6)"
vendor_unmasked = "Intel Inc."
renderer_unmasked = "Intel(R) UHD Graphics"

[persona.canvas]
noise_seed = 2449990554173590707

[persona.audio]
noise_seed = 524190668593274066

[persona.fonts]
available = ["Arial", "DejaVu Sans"]

[persona.network]
ja4 = "t13d1516h2_8daaf6152771_773c5fd3846b"
ja4h_template = "po11nn12enus"
http2_akamai = "1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
http3_enabled = false

[persona.behavior]
typing_persona = "normal"
mouse_persona = "desk_mouse_windmouse"

[persona.profile]
user_data_dir = "/tmp/persona-test-valid"
"#
    .to_string()
}
