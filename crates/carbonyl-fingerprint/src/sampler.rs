//! Persona sampler (W3A.2 — `Refs: roctinam/carbonyl-agent#43`).
//!
//! V1 scope is deliberately small: one persona class
//! ([`PersonaClass::DesktopChromeStableLinux`]) backed by the SCHEMA.md
//! exemplar in `carbonyl-fingerprint-corpus`. The sampler clones the
//! template, randomizes the per-instance fields (id, canvas/audio noise
//! seeds, `user_data_dir`), and runs [`validator::validate`] on the
//! result before returning. Any sampled persona that fails validation
//! is a sampler bug — surfaced via [`SampleError::ValidationFailed`].
//!
//! # What v1 deliberately is *not*
//!
//! - **Joint-distribution sampling** over a real corpus (BrowserForge or
//!   captured) — the corpus repo is currently empty (`.gitkeep` only).
//!   Once `corpus/chrome/chrome-{major}.toml` files land, the templates
//!   move out of this file into the corpus loader (separate commit).
//! - **Multi-class** — Firefox, Safari, mobile classes need schema
//!   extensions that aren't in `schema.rs` today.
//! - **Deterministic noise-seed derivation** (rule H) — sampler emits
//!   random `u64` for `canvas`/`audio` seeds. Once the seed-derivation
//!   scheme is locked in (HKDF-Expand with stable info labels per the
//!   `validator::TODO(rule-H)` note), the sampler will derive seeds from
//!   the persona id and the validator will start enforcing rule H.
//!
//! # API
//!
//! ```ignore
//! use carbonyl_fingerprint::sampler::{PersonaClass, Sampler};
//! use rand::thread_rng;
//!
//! let sampler = Sampler::new();
//! let persona = sampler
//!     .sample(PersonaClass::DesktopChromeStableLinux, &mut thread_rng())
//!     .expect("sampler v1 always validates");
//! ```

use crate::schema::Persona;
use crate::seed;
use crate::validator::{self, ValidationReport};
use rand::Rng;
use thiserror::Error;

/// Persona class selector. v1 supports a single class; the enum exists
/// so call-site code is forward-compatible with the multi-class
/// expansion in W3A.2 follow-ups.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum PersonaClass {
    /// Desktop Chrome stable channel on Linux (Ubuntu 24.04 baseline).
    DesktopChromeStableLinux,
    /// Desktop Firefox stable channel on Linux (Ubuntu baseline).
    ///
    /// W3A.6.1 — `Refs: roctinam/carbonyl-agent#71`. Browser-info values
    /// (UA, screen, navigator, headers) sourced from BrowserForge MIT
    /// corpus; TLS-layer values (JA4, H2 Akamai) sourced from FoxIO ja4db
    /// canonical Firefox stable Linux capture references. Provenance
    /// documented inline in [`DESKTOP_FIREFOX_STABLE_LINUX`] and the
    /// matching corpus TOML.
    DesktopFirefoxStableLinux,
}

/// Errors surfaced by the sampler.
#[derive(Debug, Error)]
pub enum SampleError {
    /// The chosen template's TOML embedded in this crate failed to
    /// deserialize. Indicates a programming error in [`Sampler`] —
    /// templates should always parse.
    #[error("internal sampler template failed to deserialize: {0}")]
    TemplateParseFailed(#[from] toml::de::Error),

    /// The sampled persona did not pass [`validator::validate`]. This
    /// is a sampler bug, not a caller bug — templates should always
    /// produce valid personas.
    #[error("sampled persona failed validation: {0}")]
    ValidationFailed(ValidationReport),
}

/// Sampler entry point. Stateless in v1 (the template is a `const &str`)
/// but kept as a struct so future versions can carry a corpus handle,
/// caching, etc., without a breaking API change.
#[derive(Debug, Default, Clone)]
pub struct Sampler {
    _private: (),
}

impl Sampler {
    pub fn new() -> Self {
        Self::default()
    }

    /// Generate a fresh persona of the requested class, seeded from
    /// `rng` for the per-instance fields. The returned persona is
    /// guaranteed to pass [`validator::validate`] in v1 because the
    /// sampler verifies this before returning.
    pub fn sample<R: Rng + ?Sized>(
        &self,
        class: PersonaClass,
        rng: &mut R,
    ) -> Result<Persona, SampleError> {
        let template = template_for(class);
        let mut persona: Persona = toml::from_str(template)?;

        randomize_per_instance_fields(&mut persona, rng);

        match validator::validate(&persona) {
            Ok(()) => Ok(persona),
            Err(report) => Err(SampleError::ValidationFailed(report)),
        }
    }
}

/// Every field a fresh persona must NOT inherit verbatim from the
/// template. v1 randomizes:
///
/// - `persona.id` — short hex tag, only true randomness in the
///   per-instance set
/// - `persona.canvas.noise_seed` — *derived* from the new id via
///   [`seed::derive_canvas_noise`] (rule H)
/// - `persona.audio.noise_seed` — *derived* from the new id via
///   [`seed::derive_audio_noise`] (rule H)
/// - `persona.profile.user_data_dir` — `/tmp/<id>`
///
/// Everything else is template-driven so the persona matches the
/// validator's reference data (Chrome 147 JA4, brand list, etc.).
fn randomize_per_instance_fields<R: Rng + ?Sized>(persona: &mut Persona, rng: &mut R) {
    let id_suffix: u64 = rng.gen();
    let id = format!("persona-sampled-{id_suffix:016x}");
    persona.persona.canvas.noise_seed = seed::derive_canvas_noise(&id);
    persona.persona.audio.noise_seed = seed::derive_audio_noise(&id);
    persona.persona.profile.user_data_dir = format!("/tmp/{id}");
    persona.persona.id = id;
}

fn template_for(class: PersonaClass) -> &'static str {
    match class {
        PersonaClass::DesktopChromeStableLinux => DESKTOP_CHROME_STABLE_LINUX,
        PersonaClass::DesktopFirefoxStableLinux => DESKTOP_FIREFOX_STABLE_LINUX,
    }
}

/// Chrome 147 stable on Linux (Ubuntu 24.04). Lifted from the SCHEMA.md
/// exemplar in `carbonyl-fingerprint-corpus`. JA4 / H2-Akamai / UA-CH
/// brands all match the inline reference data in `validator::CHROME_REFERENCES`.
///
/// Per-instance fields (`id`, `canvas.noise_seed`, `audio.noise_seed`,
/// `profile.user_data_dir`) are placeholders that
/// `randomize_per_instance_fields` overwrites.
const DESKTOP_CHROME_STABLE_LINUX: &str = r#"
[persona]
id = "persona-template-desktop-chrome-stable-linux"
generator_version = "2026.04.18"
browser_family = "chrome"
browser_version = "147.0.7727.94"
release_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.94 Safari/537.36"

[persona.user_agent.ua_ch]
brands = [["Chromium", "147"], ["Not_A Brand", "8"], ["Google Chrome", "147"]]
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
noise_seed = 0

[persona.audio]
noise_seed = 0

[persona.fonts]
available = ["Arial", "DejaVu Sans"]

[persona.network]
ja4 = "t13d1516h2_8daaf6152771_02713d6af862"
ja4h_template = "po11nn12enus"
http2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
http3_enabled = false

[persona.behavior]
typing_persona = "normal"
mouse_persona = "desk_mouse_windmouse"

[persona.profile]
user_data_dir = "/tmp/persona-template-desktop-chrome-stable-linux"
"#;

/// Firefox 150 stable on Linux (Ubuntu baseline). W3A.6.1
/// (`Refs: roctinam/carbonyl-agent#71`).
///
/// # Provenance
///
/// - Browser-info fields (UA, screen, navigator, fonts, WebGL, headers):
///   BrowserForge MIT corpus (`apify-fingerprint-datapoints v0.13.0`),
///   sampled with `FingerprintGenerator(browser=['firefox'], os=['linux'],
///   device=['desktop'])` on 2026-05-12.
/// - TLS-layer fields (JA4, ALPN, H2 Akamai SETTINGS): FoxIO ja4db public
///   reference set, canonical Firefox stable Linux 2024-2025 captures. JA4
///   `t13d1715h2_5b57614c22b0_3d5424432f57` is the documented Firefox 130+
///   NSS-stack value; H2 SETTINGS Akamai-shape `1:65536,4:131072,5:16384`
///   plus PRIORITY frames is Firefox's canonical post-#1500533 wire output.
///
/// Future QA-harness pass: refresh both halves with fresh local captures
/// against a live Firefox 150 instance + tlsx probe; diff is auditable.
///
/// # Schema accommodation
///
/// Firefox does NOT send UA Client Hints. The persona's `ua_ch` block
/// therefore has `brands = []` and the trait's `apply_persona` gates
/// `sec-ch-ua*` header emission on `browser_family == Chrome` (see
/// `http.rs`). UA-CH-dependent validator rules (rule 2, rule 3 JA4, rule F
/// H2) gate on `browser_family == Chrome` (post-#69 W3A schema migration).
///
/// `device.device_memory` is set to 4 GB — Firefox doesn't implement the
/// `navigator.deviceMemory` API, so server-side detectors won't see this
/// value from a real Firefox client. The field carries a representative
/// Linux-laptop value so the schema's required-field contract holds; it
/// is not emitted to the wire by the Firefox apply_persona path.
const DESKTOP_FIREFOX_STABLE_LINUX: &str = r#"
[persona]
id = "persona-template-desktop-firefox-stable-linux"
generator_version = "2026.05.12"
browser_family = "firefox"
browser_version = "150.0"
release_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"

[persona.user_agent.ua_ch]
brands = []
mobile = false
platform = "Linux"
platform_version = ""
architecture = ""
bitness = "64"

[persona.locale]
accept_language = "en-US,en;q=0.5"
timezone = "America/New_York"
languages = ["en-US", "en"]

[persona.device]
screen_width = 1600
screen_height = 900
color_depth = 24
device_pixel_ratio = 1.0
hardware_concurrency = 4
device_memory = 4
max_touch_points = 0

[persona.webgl]
vendor = "Mozilla"
renderer = "Mozilla"
vendor_unmasked = "Intel Inc."
renderer_unmasked = "Mesa Intel(R) UHD Graphics"

[persona.canvas]
noise_seed = 0

[persona.audio]
noise_seed = 0

[persona.fonts]
available = ["Liberation Sans", "DejaVu Sans", "Ubuntu"]

[persona.network]
ja4 = "t13d1715h2_5b57614c22b0_3d5424432f57"
ja4h_template = "fonn11nn05enus"
http2_akamai = "1:65536,4:131072,5:16384|12517377|0|m,p,a,s"
alpn = ["h2", "http/1.1"]
http3_enabled = false

[persona.behavior]
typing_persona = "normal"
mouse_persona = "desk_mouse_windmouse"

[persona.profile]
user_data_dir = "/tmp/persona-template-desktop-firefox-stable-linux"
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use rand::rngs::StdRng;
    use rand::SeedableRng;

    fn deterministic_rng() -> StdRng {
        // Fixed seed so failing tests are reproducible.
        StdRng::seed_from_u64(0xdead_beef_cafe_babe)
    }

    #[test]
    fn sample_desktop_chrome_stable_linux_passes_validator() {
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let persona = sampler
            .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .expect("sampler v1 must always produce valid personas");
        validator::validate(&persona).expect("post-validate consistency");
    }

    #[test]
    fn samples_have_distinct_ids_and_seeds() {
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let a = sampler
            .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .unwrap();
        let b = sampler
            .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .unwrap();
        assert_ne!(a.persona.id, b.persona.id);
        assert_ne!(a.persona.canvas.noise_seed, b.persona.canvas.noise_seed);
        assert_ne!(a.persona.audio.noise_seed, b.persona.audio.noise_seed);
        assert_ne!(
            a.persona.profile.user_data_dir,
            b.persona.profile.user_data_dir
        );
    }

    #[test]
    fn sampled_persona_inherits_template_fingerprint_fields() {
        // Per-instance fields drift; fingerprint-bearing fields must
        // not. If a follow-up commit accidentally randomizes the JA4
        // or browser_version, this test catches it.
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let persona = sampler
            .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .unwrap();
        assert_eq!(
            persona.persona.browser_family,
            crate::schema::BrowserFamily::Chrome
        );
        assert_eq!(persona.persona.browser_version, "147.0.7727.94");
        assert_eq!(
            persona.persona.network.ja4,
            "t13d1516h2_8daaf6152771_02713d6af862"
        );
        assert_eq!(persona.persona.platform.os_family, "Linux");
        assert_eq!(persona.persona.user_agent.ua_ch.platform, "Linux");
    }

    #[test]
    fn sampler_id_and_user_data_dir_agree() {
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let persona = sampler
            .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
            .unwrap();
        let expected_dir = format!("/tmp/{}", persona.persona.id);
        assert_eq!(persona.persona.profile.user_data_dir, expected_dir);
    }

    proptest::proptest! {
        /// For any seed, sampler output must validate. Replaces the
        /// brittle "test with one specific seed" check with full
        /// coverage over the rng space.
        #[test]
        fn prop_every_sampled_persona_validates(seed: u64) {
            let mut rng = StdRng::seed_from_u64(seed);
            let sampler = Sampler::new();
            let persona = sampler
                .sample(PersonaClass::DesktopChromeStableLinux, &mut rng)
                .expect("sampler returned validation failure");
            // Belt-and-suspenders: re-run validate to confirm.
            proptest::prop_assert!(validator::validate(&persona).is_ok());
        }
    }

    // --- Firefox (W3A.6.1 #71) ---

    #[test]
    fn sample_desktop_firefox_stable_linux_passes_validator() {
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let persona = sampler
            .sample(PersonaClass::DesktopFirefoxStableLinux, &mut rng)
            .expect("Firefox sampler must produce valid persona");
        validator::validate(&persona).expect("post-validate consistency");
    }

    #[test]
    fn firefox_sample_carries_firefox_family_marker() {
        let sampler = Sampler::new();
        let mut rng = deterministic_rng();
        let p = sampler
            .sample(PersonaClass::DesktopFirefoxStableLinux, &mut rng)
            .unwrap();
        assert_eq!(
            p.persona.browser_family,
            crate::schema::BrowserFamily::Firefox
        );
        assert_eq!(p.persona.browser_version, "150.0");
        // Firefox UA contains the version string per rule 1.
        assert!(p.persona.user_agent.full.contains("Firefox/150.0"));
        // Firefox does not advertise UA-CH brands — the persona reflects that.
        assert!(p.persona.user_agent.ua_ch.brands.is_empty());
    }

    proptest::proptest! {
        /// Same property as Chrome: any seed must produce a validating
        /// Firefox persona. Catches sampler/validator drift on the
        /// Firefox path (e.g., per-family rule gating regressions).
        #[test]
        fn prop_every_sampled_firefox_persona_validates(seed: u64) {
            let mut rng = StdRng::seed_from_u64(seed);
            let sampler = Sampler::new();
            let persona = sampler
                .sample(PersonaClass::DesktopFirefoxStableLinux, &mut rng)
                .expect("Firefox sampler returned validation failure");
            proptest::prop_assert!(validator::validate(&persona).is_ok());
        }
    }
}
