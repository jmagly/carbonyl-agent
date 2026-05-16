//! Persona schema — mirrors `SCHEMA.md` in the corpus repo.
//!
//! This module is the canonical Rust representation of a persona TOML file.
//! Schema version 2.0.0. Breaking changes bump the major.
//!
//! ## v2 changes (W3A.6 — `Refs: roctinam/carbonyl-agent#69`)
//!
//! - New `browser_family` field (enum: `chrome | firefox | safari`) on
//!   `[persona]`. Required field; no default — every persona must declare
//!   its family explicitly so per-family validator rules apply correctly.
//! - `chrome_version` → `browser_version` (string format varies per
//!   family: Chrome "MAJOR.MINOR.BUILD.PATCH", Firefox "MAJOR.MINOR",
//!   Safari "MAJOR.MINOR")
//! - `chrome_channel` → `release_channel` (still `stable | beta | dev`)
//!
//! Breaking by design: there is no serde alias path back to v1 field
//! names. Personas authored against v1 must be regenerated through the
//! sampler.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Browser family this persona simulates. Drives per-family rule
/// applicability in the validator, the refresher's UA-rewrite logic,
/// and the sampler's template selection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum BrowserFamily {
    Chrome,
    Firefox,
    Safari,
}

impl BrowserFamily {
    /// Human-readable family label, e.g. for error messages.
    pub fn as_str(self) -> &'static str {
        match self {
            BrowserFamily::Chrome => "chrome",
            BrowserFamily::Firefox => "firefox",
            BrowserFamily::Safari => "safari",
        }
    }
}

/// Frozen bundle of all fingerprintable signals for one automation persona.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Persona {
    pub persona: PersonaInner,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct PersonaInner {
    pub id: String,
    pub generator_version: String,
    pub browser_family: BrowserFamily,
    pub browser_version: String,
    pub release_channel: String,
    #[serde(default)]
    pub stale: bool,
    pub platform: Platform,
    pub user_agent: UserAgent,
    pub locale: Locale,
    pub device: Device,
    pub webgl: WebGl,
    pub canvas: Canvas,
    pub audio: Audio,
    pub fonts: Fonts,
    #[serde(default)]
    pub plugins: Vec<Plugin>,
    pub network: Network,
    pub behavior: Behavior,
    pub profile: Profile,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Platform {
    pub os_family: String,
    pub os_version: String,
    pub arch: String,
    pub bitness: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct UserAgent {
    pub full: String,
    pub ua_ch: UaCh,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct UaCh {
    pub brands: Vec<(String, String)>,
    #[serde(default)]
    pub full_version_list: Vec<(String, String)>,
    pub mobile: bool,
    pub platform: String,
    pub platform_version: String,
    #[serde(default)]
    pub model: String,
    pub architecture: String,
    pub bitness: String,
    #[serde(default)]
    pub wow64: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Locale {
    pub accept_language: String,
    pub timezone: String,
    pub languages: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Device {
    pub screen_width: u32,
    pub screen_height: u32,
    pub color_depth: u32,
    pub device_pixel_ratio: f32,
    pub hardware_concurrency: u32,
    pub device_memory: u32,
    pub max_touch_points: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WebGl {
    pub vendor: String,
    pub renderer: String,
    pub vendor_unmasked: String,
    pub renderer_unmasked: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Canvas {
    pub noise_seed: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Audio {
    pub noise_seed: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Fonts {
    pub available: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Plugin {
    pub name: String,
    pub filename: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Network {
    pub ja4: String,
    pub ja4h_template: String,
    pub http2_akamai: String,
    pub alpn: Vec<String>,
    pub http3_enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Behavior {
    pub typing_persona: String,
    pub mouse_persona: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Profile {
    pub user_data_dir: String,
    #[serde(default)]
    pub age_hours: u32,
    #[serde(default)]
    pub sites_warmed: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    const MINIMAL_PERSONA: &str = r#"
[persona]
id = "persona-test-01"
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
brands = [["Chromium", "148"], ["Google Chrome", "148"]]
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
noise_seed = 2134389534

[persona.audio]
noise_seed = 729608453

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
user_data_dir = "/tmp/persona-test-01"
"#;

    #[test]
    fn deserializes_minimal_persona() {
        let p: Persona = toml::from_str(MINIMAL_PERSONA).expect("parse");
        assert_eq!(p.persona.id, "persona-test-01");
        assert_eq!(p.persona.browser_family, BrowserFamily::Chrome);
        assert_eq!(p.persona.browser_version, "148.0.7778.167");
        assert_eq!(p.persona.release_channel, "stable");
        assert_eq!(p.persona.platform.os_family, "Linux");
        assert_eq!(p.persona.device.hardware_concurrency, 8);
        assert_eq!(p.persona.network.alpn, vec!["h2", "http/1.1"]);
        assert!(!p.persona.network.http3_enabled);
        assert!(!p.persona.stale); // default
    }

    #[test]
    fn browser_family_parses_lowercase() {
        let firefox: BrowserFamily = toml::from_str(r#"v = "firefox""#)
            .map(|t: toml::Table| t.get("v").cloned().unwrap())
            .and_then(|v| v.try_into())
            .expect("firefox parse");
        let safari: BrowserFamily = toml::from_str(r#"v = "safari""#)
            .map(|t: toml::Table| t.get("v").cloned().unwrap())
            .and_then(|v| v.try_into())
            .expect("safari parse");
        assert_eq!(firefox, BrowserFamily::Firefox);
        assert_eq!(safari, BrowserFamily::Safari);
        assert_eq!(BrowserFamily::Chrome.as_str(), "chrome");
        assert_eq!(BrowserFamily::Firefox.as_str(), "firefox");
        assert_eq!(BrowserFamily::Safari.as_str(), "safari");
    }

    #[test]
    fn roundtrips_minimal_persona() {
        let p: Persona = toml::from_str(MINIMAL_PERSONA).expect("parse");
        let serialized = toml::to_string(&p).expect("serialize");
        let p2: Persona = toml::from_str(&serialized).expect("reparse");
        assert_eq!(p, p2);
    }
}
