//! PyO3 bindings (`Refs: roctinam/carbonyl-agent#43` — W3A AC item).
//!
//! Minimal surface for v1: the Python refresh pipeline / SDK only need
//! to (1) parse a persona TOML, (2) validate it, and (3) get a list of
//! human-readable error strings on failure. Richer types (Persona
//! struct, ValidationError variants) can be exposed later if call sites
//! demand them — keeping the surface small now keeps the binding stable
//! while the Rust-side schema is still in flux.
//!
//! # Build
//!
//! ```text
//! maturin develop --manifest-path crates/carbonyl-fingerprint/Cargo.toml --features python
//! ```
//!
//! # Python usage
//!
//! ```python
//! import carbonyl_fingerprint as cf
//!
//! ok, errors = cf.validate_toml(persona_toml)
//! if not ok:
//!     for e in errors:
//!         print(f"  - {e}")
//! ```
//!
//! Errors:
//! - `ValueError` — TOML parse failed (malformed input)
//! - successful parse + failed validation returns `(False, [str, ...])`
//!   rather than raising; callers usually want the structured list

// PyO3's `#[pyfunction]` / `#[pymodule]` macros generate wrapper code
// that performs `.into()` between `PyResult<T>` and the wire-level
// `PyResult<PyObject>`. Modern clippy flags those as useless
// conversions even though the macro depends on them. The allow stays
// local to the bindings module — the rest of the crate keeps the
// stricter default.
#![allow(clippy::useless_conversion)]

use crate::schema::Persona;
use crate::validator;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Parse + validate a persona TOML string.
///
/// Returns `(ok, errors)` where `ok` is `True` when validation passed
/// and `errors` is a (possibly empty) list of human-readable violation
/// messages produced by `validator::validate`.
///
/// Raises `ValueError` when the input string is not valid TOML or does
/// not deserialize into a `Persona` (a structural failure, not a
/// validation failure).
#[pyfunction]
#[pyo3(name = "validate_toml")]
fn py_validate_toml(toml_str: &str) -> PyResult<(bool, Vec<String>)> {
    let persona: Persona = toml::from_str(toml_str)
        .map_err(|e| PyValueError::new_err(format!("persona TOML parse error: {e}")))?;

    match validator::validate(&persona) {
        Ok(()) => Ok((true, Vec::new())),
        Err(report) => {
            let messages: Vec<String> = report
                .errors()
                .iter()
                .map(std::string::ToString::to_string)
                .collect();
            Ok((false, messages))
        }
    }
}

/// `True` when the TOML parses AND validates. Convenience wrapper for
/// callers that don't need the error list (e.g., test fixtures).
///
/// Raises `ValueError` on TOML parse failure.
#[pyfunction]
#[pyo3(name = "is_valid_toml")]
fn py_is_valid_toml(toml_str: &str) -> PyResult<bool> {
    let persona: Persona = toml::from_str(toml_str)
        .map_err(|e| PyValueError::new_err(format!("persona TOML parse error: {e}")))?;
    Ok(validator::validate(&persona).is_ok())
}

/// Module entry point. Name MUST match the `name` field in `[lib]` of
/// Cargo.toml so the produced `.so` is importable as
/// `carbonyl_fingerprint`.
#[pymodule]
fn carbonyl_fingerprint(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_validate_toml, m)?)?;
    m.add_function(wrap_pyfunction!(py_is_valid_toml, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
