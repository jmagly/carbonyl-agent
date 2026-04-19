//! Persona schema — mirrors `SCHEMA.md` in the corpus repo.
//!
//! This module is the canonical Rust representation of a persona TOML file.
//! Schema version 1.0.0. Breaking changes bump the major.

use serde::{Deserialize, Serialize};

/// Frozen bundle of all fingerprintable signals for one automation persona.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Persona {
    pub persona: PersonaInner,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PersonaInner {
    pub id: String,
    pub generator_version: String,
    pub chrome_version: String,
    pub chrome_channel: String,
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Platform {
    pub os_family: String,
    pub os_version: String,
    pub arch: String,
    pub bitness: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserAgent {
    pub full: String,
    pub ua_ch: UaCh,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Locale {
    pub accept_language: String,
    pub timezone: String,
    pub languages: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Device {
    pub screen_width: u32,
    pub screen_height: u32,
    pub color_depth: u32,
    pub device_pixel_ratio: f32,
    pub hardware_concurrency: u32,
    pub device_memory: u32,
    pub max_touch_points: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WebGl {
    pub vendor: String,
    pub renderer: String,
    pub vendor_unmasked: String,
    pub renderer_unmasked: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Canvas {
    pub noise_seed: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Audio {
    pub noise_seed: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Fonts {
    pub available: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Plugin {
    pub name: String,
    pub filename: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Network {
    pub ja4: String,
    pub ja4h_template: String,
    pub http2_akamai: String,
    pub alpn: Vec<String>,
    pub http3_enabled: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Behavior {
    pub typing_persona: String,
    pub mouse_persona: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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
chrome_version = "147.0.7727.94"
chrome_channel = "stable"

[persona.platform]
os_family = "Linux"
os_version = "Ubuntu 24.04"
arch = "x86_64"
bitness = "64"

[persona.user_agent]
full = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7727.94 Safari/537.36"

[persona.user_agent.ua_ch]
brands = [["Chromium", "147"], ["Google Chrome", "147"]]
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
ja4 = "t13d1516h2_8daaf6152771_02713d6af862"
ja4h_template = "po11nn12enus"
http2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"
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
        assert_eq!(p.persona.chrome_version, "147.0.7727.94");
        assert_eq!(p.persona.platform.os_family, "Linux");
        assert_eq!(p.persona.device.hardware_concurrency, 8);
        assert_eq!(p.persona.network.alpn, vec!["h2", "http/1.1"]);
        assert_eq!(p.persona.network.http3_enabled, false);
        assert_eq!(p.persona.stale, false); // default
    }

    #[test]
    fn roundtrips_minimal_persona() {
        let p: Persona = toml::from_str(MINIMAL_PERSONA).expect("parse");
        let serialized = toml::to_string(&p).expect("serialize");
        let p2: Persona = toml::from_str(&serialized).expect("reparse");
        assert_eq!(p, p2);
    }
}
