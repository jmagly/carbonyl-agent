//! Corpus-backed Chrome reference registry (`Refs:
//! roctinam/carbonyl-agent#68`).
//!
//! Loads `corpus/chrome/chrome-{major}.toml` files from
//! [carbonyl-fingerprint-corpus][corpus] and exposes them to the
//! validator via [`ChromeRegistry::lookup`]. The `inline_default()`
//! constructor returns a built-in registry containing only Chrome 148
//! (the SCHEMA.md exemplar) — preserves the validator's behavior from
//! before #68 landed for callers that haven't checked out the corpus
//! repo.
//!
//! [corpus]: https://git.integrolabs.net/roctinam/carbonyl-fingerprint-corpus
//!
//! # Quick start
//!
//! ```no_run
//! use carbonyl_fingerprint::{registry::ChromeRegistry, validator};
//! use std::path::Path;
//!
//! fn run(persona: &carbonyl_fingerprint::Persona) -> Result<(), Box<dyn std::error::Error>> {
//!     let reg = ChromeRegistry::from_corpus_dir(Path::new(
//!         "/path/to/carbonyl-fingerprint-corpus",
//!     ))?;
//!     validator::validate_with_registry(persona, &reg)?;
//!     Ok(())
//! }
//! ```
//!
//! # Stale references
//!
//! A reference with a non-empty `marked_stale_at` field is still
//! returned by [`ChromeRegistry::lookup`] — the persona's reference
//! data is authoritative within its stable window even after that
//! window closes. Consumers wanting stale-aware behavior call
//! [`ChromeRegistry::lookup_with_status`] instead.

use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use thiserror::Error;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Chrome major-version reference projected for the validator. Mirrors
/// the fields the validator's check functions actually consume.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChromeReference {
    pub major: u32,
    pub version: String,
    pub ja4: String,
    pub h2_akamai: String,
    pub alpn: Vec<String>,
    pub stale: bool,
}

/// Errors surfaced when loading the corpus.
#[derive(Debug, Error)]
pub enum RegistryError {
    #[error("corpus directory not found: {0}")]
    CorpusDirMissing(PathBuf),

    #[error("`corpus/chrome` subdirectory not found in {0}")]
    ChromeDirMissing(PathBuf),

    #[error("failed to read corpus file {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },

    #[error("failed to parse {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: toml::de::Error,
    },

    #[error(
        "filename `{filename}` advertises Chrome major {filename_major} but \
         file's [chrome] section says major = {body_major}"
    )]
    MajorMismatch {
        filename: String,
        filename_major: u32,
        body_major: u32,
    },

    #[error("two corpus files claim Chrome major {major}: {first} and {second}")]
    DuplicateMajor {
        major: u32,
        first: PathBuf,
        second: PathBuf,
    },
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/// In-memory registry of Chrome reference data. Keyed by major version.
#[derive(Debug, Clone)]
pub struct ChromeRegistry {
    by_major: BTreeMap<u32, ChromeReference>,
}

impl ChromeRegistry {
    /// Built-in fallback registry containing only Chrome 148. Used as
    /// the default when no corpus directory is configured. Preserves
    /// the validator's pre-#68 behavior for callers that don't pass a
    /// registry explicitly.
    pub fn inline_default() -> Self {
        let mut by_major = BTreeMap::new();
        by_major.insert(
            148,
            ChromeReference {
                major: 148,
                version: "148.0.7778.167".to_string(),
                ja4: "t13d1516h2_8daaf6152771_773c5fd3846b".to_string(),
                h2_akamai: "1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p".to_string(),
                alpn: vec!["h2".to_string(), "http/1.1".to_string()],
                stale: false,
            },
        );
        Self { by_major }
    }

    /// Load every `corpus/chrome/chrome-{major}.toml` under the given
    /// corpus directory.
    pub fn from_corpus_dir(corpus_dir: &Path) -> Result<Self, RegistryError> {
        if !corpus_dir.is_dir() {
            return Err(RegistryError::CorpusDirMissing(corpus_dir.to_path_buf()));
        }

        let chrome_dir = corpus_dir.join("corpus").join("chrome");
        if !chrome_dir.is_dir() {
            return Err(RegistryError::ChromeDirMissing(corpus_dir.to_path_buf()));
        }

        let mut by_major: BTreeMap<u32, ChromeReference> = BTreeMap::new();
        let mut path_by_major: BTreeMap<u32, PathBuf> = BTreeMap::new();

        let entries = std::fs::read_dir(&chrome_dir).map_err(|e| RegistryError::Io {
            path: chrome_dir.clone(),
            source: e,
        })?;

        for entry in entries {
            let entry = entry.map_err(|e| RegistryError::Io {
                path: chrome_dir.clone(),
                source: e,
            })?;
            let path = entry.path();

            // Skip anything that isn't a chrome-NNN.toml file.
            let Some(filename) = path.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            let Some(filename_major) = parse_chrome_filename_major(filename) else {
                continue;
            };

            let body = std::fs::read_to_string(&path).map_err(|e| RegistryError::Io {
                path: path.clone(),
                source: e,
            })?;
            let parsed: ChromeReferenceFile =
                toml::from_str(&body).map_err(|e| RegistryError::Parse {
                    path: path.clone(),
                    source: e,
                })?;

            if parsed.chrome.major != filename_major {
                return Err(RegistryError::MajorMismatch {
                    filename: filename.to_string(),
                    filename_major,
                    body_major: parsed.chrome.major,
                });
            }

            let projected = parsed.into_reference();
            let major = projected.major;

            if let Some(prev) = path_by_major.get(&major).cloned() {
                return Err(RegistryError::DuplicateMajor {
                    major,
                    first: prev,
                    second: path.clone(),
                });
            }

            by_major.insert(major, projected);
            path_by_major.insert(major, path);
        }

        Ok(Self { by_major })
    }

    /// Look up a Chrome reference by major version. Returns the
    /// reference even when its `stale` flag is set — staleness is
    /// informational, not authoritative within the persona's stable
    /// window.
    pub fn lookup(&self, major: u32) -> Option<&ChromeReference> {
        self.by_major.get(&major)
    }

    /// Like [`lookup`] but returns an explicit `(reference, is_stale)`
    /// tuple for callers that care about staleness routing.
    pub fn lookup_with_status(&self, major: u32) -> Option<(&ChromeReference, bool)> {
        self.by_major.get(&major).map(|r| (r, r.stale))
    }

    /// All Chrome majors present in this registry, ascending.
    pub fn known_majors(&self) -> Vec<u32> {
        self.by_major.keys().copied().collect()
    }

    /// Number of Chrome majors loaded.
    pub fn len(&self) -> usize {
        self.by_major.len()
    }

    /// `true` if the registry has no entries at all.
    pub fn is_empty(&self) -> bool {
        self.by_major.is_empty()
    }
}

impl Default for ChromeRegistry {
    fn default() -> Self {
        Self::inline_default()
    }
}

// ---------------------------------------------------------------------------
// Internal: TOML deserialization
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct ChromeReferenceFile {
    chrome: ChromeMeta,
}

#[derive(Debug, Deserialize)]
struct ChromeMeta {
    major: u32,
    version: String,
    #[allow(dead_code)]
    #[serde(default)]
    stable_since: String,
    #[serde(default)]
    marked_stale_at: String,
    fingerprints: ChromeFingerprints,
    // `ua_ch` and `capture` blocks are intentionally left unparsed for
    // v1 — they're informational metadata the validator does not
    // consume. Extending them is mechanical when the consumer arrives.
    #[allow(dead_code)]
    #[serde(default)]
    ua_ch: Option<toml::Value>,
    #[allow(dead_code)]
    #[serde(default)]
    capture: Option<toml::Value>,
}

#[derive(Debug, Deserialize)]
struct ChromeFingerprints {
    ja4: String,
    h2_akamai: String,
    alpn: Vec<String>,
    #[allow(dead_code)]
    #[serde(default)]
    post_quantum: bool,
}

impl ChromeReferenceFile {
    fn into_reference(self) -> ChromeReference {
        ChromeReference {
            major: self.chrome.major,
            version: self.chrome.version,
            ja4: self.chrome.fingerprints.ja4,
            h2_akamai: self.chrome.fingerprints.h2_akamai,
            alpn: self.chrome.fingerprints.alpn,
            stale: !self.chrome.marked_stale_at.is_empty(),
        }
    }
}

/// Parse `chrome-148.toml` → `Some(148)`. Anything else (no prefix,
/// non-numeric major, wrong extension) returns `None`.
fn parse_chrome_filename_major(filename: &str) -> Option<u32> {
    let stripped = filename.strip_prefix("chrome-")?.strip_suffix(".toml")?;
    stripped.parse::<u32>().ok()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    fn write_corpus<I, P>(dir: &Path, files: I)
    where
        I: IntoIterator<Item = (P, &'static str)>,
        P: AsRef<Path>,
    {
        let chrome_dir = dir.join("corpus").join("chrome");
        std::fs::create_dir_all(&chrome_dir).expect("create chrome dir");
        for (rel, body) in files {
            std::fs::write(chrome_dir.join(rel.as_ref()), body).expect("write fixture");
        }
    }

    const CHROME_148_TOML: &str = r#"
[chrome]
major = 148
version = "148.0.7778.167"
stable_since = "2026-04-10"
marked_stale_at = ""

[chrome.fingerprints]
ja4 = "t13d1516h2_8daaf6152771_773c5fd3846b"
h2_akamai = "1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
post_quantum = true

[chrome.ua_ch]
full_version_list_template = "Chromium;{minor};{build};{patch}"

[chrome.capture]
captured_at = "2026-04-18T19:00:00Z"
captured_by = "schema-md-exemplar"
source_urls = ["https://tls.peet.ws/api/all"]
"#;

    const CHROME_146_STALE_TOML: &str = r#"
[chrome]
major = 146
version = "146.0.7100.50"
stable_since = "2026-03-01"
marked_stale_at = "2026-04-10"

[chrome.fingerprints]
ja4 = "t13d1516h2_DEADBEEF146_02713d6af862"
h2_akamai = "1:65536,2:0,4:6291456,6:262144|15663105|0|m,a,s,p"
alpn = ["h2", "http/1.1"]
post_quantum = true
"#;

    // -------- inline_default --------

    #[test]
    fn inline_default_carries_chrome_148() {
        let reg = ChromeRegistry::inline_default();
        assert_eq!(reg.known_majors(), vec![148]);
        let r = reg.lookup(148).expect("148 present");
        assert_eq!(r.ja4, "t13d1516h2_8daaf6152771_773c5fd3846b");
        assert!(!r.stale);
    }

    #[test]
    fn inline_default_returns_none_for_unknown_major() {
        assert!(ChromeRegistry::inline_default().lookup(999).is_none());
    }

    // -------- from_corpus_dir happy path --------

    #[test]
    fn loads_single_chrome_file_from_corpus() {
        let tmp = tempdir();
        write_corpus(tmp.path(), [("chrome-148.toml", CHROME_148_TOML)]);

        let reg = ChromeRegistry::from_corpus_dir(tmp.path()).expect("loads");
        assert_eq!(reg.known_majors(), vec![148]);
        let r = reg.lookup(148).unwrap();
        assert_eq!(r.major, 148);
        assert_eq!(r.version, "148.0.7778.167");
        assert_eq!(r.ja4, "t13d1516h2_8daaf6152771_773c5fd3846b");
        assert_eq!(r.alpn, vec!["h2".to_string(), "http/1.1".to_string()]);
    }

    #[test]
    fn loads_multiple_majors_sorted() {
        let tmp = tempdir();
        write_corpus(
            tmp.path(),
            [
                ("chrome-148.toml", CHROME_148_TOML),
                ("chrome-146.toml", CHROME_146_STALE_TOML),
            ],
        );
        let reg = ChromeRegistry::from_corpus_dir(tmp.path()).expect("loads");
        assert_eq!(reg.known_majors(), vec![146, 148]);
        assert_eq!(reg.len(), 2);
        assert!(!reg.is_empty());
    }

    #[test]
    fn skips_non_chrome_files_silently() {
        let tmp = tempdir();
        write_corpus(
            tmp.path(),
            [
                ("chrome-148.toml", CHROME_148_TOML),
                ("README.md", "# not a chrome file"),
                (".gitkeep", ""),
                ("notes.txt", "ignored"),
            ],
        );
        let reg = ChromeRegistry::from_corpus_dir(tmp.path()).expect("loads");
        assert_eq!(reg.known_majors(), vec![148]);
    }

    // -------- staleness routing --------

    #[test]
    fn stale_entry_is_returned_by_lookup_with_status() {
        let tmp = tempdir();
        write_corpus(tmp.path(), [("chrome-146.toml", CHROME_146_STALE_TOML)]);
        let reg = ChromeRegistry::from_corpus_dir(tmp.path()).unwrap();

        let (r, is_stale) = reg.lookup_with_status(146).expect("146 present");
        assert!(is_stale);
        assert!(r.stale);
        assert_eq!(r.ja4, "t13d1516h2_DEADBEEF146_02713d6af862");
    }

    #[test]
    fn fresh_entry_reports_not_stale() {
        let tmp = tempdir();
        write_corpus(tmp.path(), [("chrome-148.toml", CHROME_148_TOML)]);
        let reg = ChromeRegistry::from_corpus_dir(tmp.path()).unwrap();
        let (_, is_stale) = reg.lookup_with_status(148).unwrap();
        assert!(!is_stale);
    }

    // -------- error cases --------

    #[test]
    fn missing_corpus_dir_errors_with_path() {
        let err = ChromeRegistry::from_corpus_dir(Path::new(
            "/nonexistent/path/that/should/not/exist/abc123",
        ))
        .expect_err("must error");
        match err {
            RegistryError::CorpusDirMissing(p) => {
                assert!(p.to_string_lossy().contains("nonexistent"));
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn missing_chrome_subdir_errors() {
        let tmp = tempdir();
        // Create only `corpus/`, not `corpus/chrome/`.
        std::fs::create_dir_all(tmp.path().join("corpus")).unwrap();
        let err = ChromeRegistry::from_corpus_dir(tmp.path()).expect_err("must error");
        assert!(matches!(err, RegistryError::ChromeDirMissing(_)));
    }

    #[test]
    fn malformed_toml_errors_with_path() {
        let tmp = tempdir();
        write_corpus(
            tmp.path(),
            [("chrome-148.toml", "this is not = [valid toml")],
        );
        let err = ChromeRegistry::from_corpus_dir(tmp.path()).expect_err("must error");
        match err {
            RegistryError::Parse { path, .. } => {
                assert!(path.to_string_lossy().contains("chrome-148.toml"));
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn filename_body_major_mismatch_errors() {
        let tmp = tempdir();
        // Filename says 999 but body says 148.
        write_corpus(tmp.path(), [("chrome-999.toml", CHROME_148_TOML)]);
        let err = ChromeRegistry::from_corpus_dir(tmp.path()).expect_err("must error");
        match err {
            RegistryError::MajorMismatch {
                filename_major,
                body_major,
                ..
            } => {
                assert_eq!(filename_major, 999);
                assert_eq!(body_major, 148);
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    // -------- filename parser --------

    #[test]
    fn parse_chrome_filename_major_accepts_canonical() {
        assert_eq!(parse_chrome_filename_major("chrome-148.toml"), Some(148));
        assert_eq!(parse_chrome_filename_major("chrome-1.toml"), Some(1));
        assert_eq!(
            parse_chrome_filename_major("chrome-99999.toml"),
            Some(99999)
        );
    }

    #[test]
    fn parse_chrome_filename_major_rejects_non_canonical() {
        assert_eq!(parse_chrome_filename_major("chrome-abc.toml"), None);
        assert_eq!(parse_chrome_filename_major("chrome-148.txt"), None);
        assert_eq!(parse_chrome_filename_major("README.md"), None);
        assert_eq!(parse_chrome_filename_major(".gitkeep"), None);
        assert_eq!(parse_chrome_filename_major("chrome.toml"), None);
        assert_eq!(parse_chrome_filename_major("chrome-148"), None);
    }

    // -------- Default impl --------

    #[test]
    fn default_returns_inline_default() {
        let d: ChromeRegistry = Default::default();
        assert_eq!(
            d.known_majors(),
            ChromeRegistry::inline_default().known_majors()
        );
    }

    // ----- helpers -----

    /// Tiny tempdir stand-in so we don't pull `tempfile` as a real dep.
    /// proptest's transitive `tempfile` is a dev-dep already; using
    /// it directly here would tighten that coupling. A hand-rolled
    /// scratch dir under `target/` keeps the surface honest.
    struct ScratchDir(PathBuf);

    impl ScratchDir {
        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for ScratchDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn tempdir() -> ScratchDir {
        // `CARGO_TARGET_TMPDIR` is only set for integration tests in
        // `tests/`; unit tests in `src/` use the OS temp dir instead.
        let base = std::env::temp_dir().join("carbonyl-fingerprint-tests");
        let _ = std::fs::create_dir_all(&base);
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default();
        let pid = std::process::id();
        let path = base.join(format!("registry-test-{pid}-{stamp}"));
        std::fs::create_dir_all(&path).expect("create tempdir");
        ScratchDir(path)
    }
}
