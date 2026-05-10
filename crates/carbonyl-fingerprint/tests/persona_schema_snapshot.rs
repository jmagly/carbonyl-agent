//! Regression test for the committed `persona.schema.json` snapshot.
//!
//! Generates a fresh JSON Schema from the current `Persona` Rust types
//! via `schemars::schema_for!` and compares it against the snapshot
//! checked into the repo. When you intentionally evolve the schema:
//!
//! ```text
//! UPDATE_PERSONA_SCHEMA=1 cargo test --test persona_schema_snapshot
//! ```
//!
//! …and commit the regenerated `persona.schema.json` alongside the code
//! change. CI will fail on any uncommitted drift.
//!
//! Refs: roctinam/carbonyl-agent#43 (W3A AC: Persona JSON schema documented).

use carbonyl_fingerprint::Persona;
use pretty_assertions::assert_eq;
use schemars::schema_for;
use std::fs;
use std::path::PathBuf;

fn snapshot_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("persona.schema.json")
}

fn current_schema_pretty() -> String {
    let schema = schema_for!(Persona);
    serde_json::to_string_pretty(&schema).expect("schema_for!(Persona) must serialize to JSON")
}

#[test]
fn persona_json_schema_matches_committed_snapshot() {
    let generated = current_schema_pretty();
    let path = snapshot_path();

    if std::env::var_os("UPDATE_PERSONA_SCHEMA").is_some() {
        fs::write(&path, format!("{generated}\n"))
            .expect("UPDATE_PERSONA_SCHEMA: failed to write snapshot");
        // Read back and assert clean round-trip so a `cargo test` rerun
        // succeeds without the env var.
        let written = fs::read_to_string(&path).expect("re-read snapshot");
        assert_eq!(written.trim_end(), generated);
        return;
    }

    let committed = fs::read_to_string(&path)
        .expect("persona.schema.json missing — run with UPDATE_PERSONA_SCHEMA=1 to seed it");
    assert_eq!(
        committed.trim_end(),
        generated,
        "Persona JSON schema drifted from committed snapshot.\n\
         Run `UPDATE_PERSONA_SCHEMA=1 cargo test --test persona_schema_snapshot` \
         and commit the regenerated persona.schema.json."
    );
}

#[test]
fn persona_json_schema_has_expected_top_level_shape() {
    // Smoke check independent of the snapshot file: the generated
    // schema must be a JSON object describing a top-level `Persona`
    // type with a `persona` property (PersonaInner).
    let schema = schema_for!(Persona);
    let value = serde_json::to_value(&schema).expect("schema serializes");

    assert_eq!(
        value
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or_default(),
        "Persona",
        "schema title should be `Persona`"
    );

    let properties = value
        .get("properties")
        .and_then(|v| v.as_object())
        .expect("Persona schema must have `properties`");
    assert!(
        properties.contains_key("persona"),
        "Persona must expose a `persona` property; got keys {:?}",
        properties.keys().collect::<Vec<_>>()
    );
}
