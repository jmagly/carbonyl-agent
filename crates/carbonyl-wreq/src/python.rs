//! PyO3 bindings for carbonyl-wreq (W3B.Phase2.4b —
//! `Refs: roctinam/carbonyl-agent#85`).
//!
//! Exposes a single sync function — [`send_request`] — that the Python
//! side's `WreqTransport` (Phase 2.4 — #83) calls. The function builds
//! a wreq client from the persona TOML, dispatches the request on an
//! embedded multi-threaded tokio runtime via `block_on`, and returns a
//! tuple matching the `WreqTransport` contract.
//!
//! # Build
//!
//! ```text
//! maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml --features python
//! ```
//!
//! # Why a single function instead of a class
//!
//! The Python side has its own `WreqTransport` class that holds
//! per-instance state (persona TOML, timeout). Mirroring that as a
//! PyO3 class doubles the surface area without buying anything — the
//! function-based API is stateless on the Rust side and the tokio
//! runtime is shared globally.
//!
//! # JA4 capture status
//!
//! Phase 2.4b returns the persona's *expected* JA4 as `captured_ja4`.
//! wreq doesn't expose introspection of the actual ClientHello it
//! emits — the Layer 2 conformance test crate (#82) measures the
//! real wire divergence against the persona spec. Future work
//! (cross-ref'd in the W3B.Phase2.4b acceptance criteria) can wire
//! a tcpdump-style capture into the audit row when the gap matters
//! at the API layer.

// PyO3 macros emit some clippy noise that's not actionable from here.
#![allow(clippy::useless_conversion)]

use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;

use carbonyl_fingerprint::schema::Persona;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use tokio::runtime::Runtime;

use crate::client::WreqClient;
use crate::WreqError;

/// Shared tokio multi-threaded runtime. Created once on first use and
/// reused for every PyO3 call. Multi-threaded so concurrent requests
/// from different Python threads don't serialize.
fn shared_runtime() -> &'static Runtime {
    static RT: OnceLock<Runtime> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .thread_name("carbonyl-wreq-rt")
            .build()
            .expect("tokio runtime must initialize")
    })
}

/// Send one HTTP/2 request through wreq with the persona's
/// fingerprint configuration applied.
///
/// Returns `(status, response_headers, response_body, captured_ja4)`.
///
/// - `persona_toml` — full persona TOML body (the wrapping
///   `[persona]` section is required; matches `Persona.raw_toml()` on
///   the Python side).
/// - `method` — HTTP verb (case-insensitive). Validated minimally —
///   wreq's underlying http::Method::from_bytes does the strict check.
/// - `url` — absolute URL.
/// - `headers` — additional headers to send. The persona's UA,
///   Accept-Language, and (for Chrome family) sec-ch-ua* are wired
///   into wreq through the emulation preset / default-header path
///   already; entries here ADD or OVERRIDE on a per-request basis.
/// - `body` — optional request body bytes.
/// - `timeout_seconds` — overall request timeout. Maps to wreq's
///   per-request timeout setter.
///
/// Raises `RuntimeError` on transport-layer failures (TLS handshake,
/// connect refused, response timeout, etc.). `ValueError` on
/// malformed persona TOML or invalid method/URL.
#[pyfunction]
#[pyo3(signature = (persona_toml, method, url, headers, body, timeout_seconds))]
#[allow(clippy::type_complexity)]
fn send_request(
    py: Python<'_>,
    persona_toml: &str,
    method: &str,
    url: &str,
    headers: &Bound<'_, PyDict>,
    body: Option<&Bound<'_, PyBytes>>,
    timeout_seconds: f64,
) -> PyResult<(u16, Py<PyList>, Py<PyBytes>, String)> {
    // Parse + apply persona on the Rust side.
    let persona: Persona = toml::from_str(persona_toml)
        .map_err(|e| PyValueError::new_err(format!("persona TOML parse error: {e}")))?;

    let mut wreq_client = WreqClient::new();
    wreq_client
        .apply_persona_typed(&persona)
        .map_err(|e| PyRuntimeError::new_err(format!("persona apply error: {e}")))?;

    // Capture the persona's expected JA4 BEFORE consuming the
    // PendingConfig via build() — the audit row consumer expects a
    // non-empty string here. See module docs on capture status.
    let captured_ja4 = wreq_client
        .pending()
        .ja4
        .clone()
        .unwrap_or_else(|| persona.persona.network.ja4.clone());

    let client = wreq_client
        .build()
        .map_err(|e: WreqError| PyRuntimeError::new_err(format!("wreq build error: {e}")))?;

    // Extract per-request headers from the Python dict before we
    // surrender the GIL to the async block (tokio block_on holds the
    // current thread — PyO3 wants the GIL released during long
    // blocking work).
    let mut extra_headers: HashMap<String, String> = HashMap::with_capacity(headers.len());
    for (k, v) in headers.iter() {
        let k_str: String = k
            .extract()
            .map_err(|e| PyValueError::new_err(format!("header key not str: {e}")))?;
        let v_str: String = v
            .extract()
            .map_err(|e| PyValueError::new_err(format!("header value not str: {e}")))?;
        extra_headers.insert(k_str, v_str);
    }

    let body_vec: Option<Vec<u8>> = body.map(|b| b.as_bytes().to_vec());
    let method_owned = method.to_string();
    let url_owned = url.to_string();
    let timeout = Duration::from_secs_f64(timeout_seconds);

    // Release the GIL for the duration of the (potentially slow) IO.
    let result: Result<(u16, Vec<(String, String)>, Vec<u8>), String> = py.allow_threads(|| {
        shared_runtime().block_on(async move {
            send_request_async(
                &client,
                &method_owned,
                &url_owned,
                &extra_headers,
                body_vec,
                timeout,
            )
            .await
        })
    });

    let (status, response_headers, response_body) = result.map_err(PyRuntimeError::new_err)?;

    // Re-acquire the GIL for the return marshalling.
    let py_headers = PyList::empty_bound(py);
    for (k, v) in response_headers {
        let tuple: Py<PyAny> = (k, v).into_py(py);
        py_headers.append(tuple)?;
    }
    let py_body = PyBytes::new_bound(py, &response_body);

    Ok((status, py_headers.unbind(), py_body.unbind(), captured_ja4))
}

/// Inner async dispatcher. Owned method/url/body so the outer
/// `allow_threads` boundary doesn't need to hold any Python references.
async fn send_request_async(
    client: &wreq::Client,
    method: &str,
    url: &str,
    extra_headers: &HashMap<String, String>,
    body: Option<Vec<u8>>,
    timeout: Duration,
) -> Result<(u16, Vec<(String, String)>, Vec<u8>), String> {
    let method = http::Method::from_bytes(method.as_bytes())
        .map_err(|e| format!("invalid HTTP method: {e}"))?;

    let mut req = client.request(method, url).timeout(timeout);
    for (k, v) in extra_headers {
        req = req.header(k, v);
    }
    if let Some(b) = body {
        req = req.body(b);
    }

    let response = req
        .send()
        .await
        .map_err(|e| format!("request failed: {e}"))?;
    let status = response.status().as_u16();
    let mut response_headers: Vec<(String, String)> = Vec::with_capacity(response.headers().len());
    for (k, v) in response.headers().iter() {
        let v_str = v
            .to_str()
            .map_err(|e| format!("response header {k} not UTF-8: {e}"))?
            .to_string();
        response_headers.push((k.as_str().to_string(), v_str));
    }
    let response_body = response
        .bytes()
        .await
        .map_err(|e| format!("response body read failed: {e}"))?
        .to_vec();

    Ok((status, response_headers, response_body))
}

/// PyO3 module entry. Name MUST match the `name` field in `[lib]` of
/// Cargo.toml so the produced `.so` is importable as `carbonyl_wreq`.
///
/// The Python side's `WreqTransport.is_available()` checks for the
/// `send_request` attribute; keep it stable.
#[pymodule]
fn carbonyl_wreq(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(send_request, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    //! These tests run with `cargo test --features python` — they
    //! cover the Rust internals that don't depend on a Python
    //! interpreter being present. End-to-end (Python → Rust → wire)
    //! exercises live in `tests/test_egress_wreq_transport.py` once
    //! `maturin develop --features python` has built the cdylib.

    use super::*;

    #[test]
    fn shared_runtime_initializes_once() {
        // Two callers see the same runtime instance.
        let r1 = shared_runtime();
        let r2 = shared_runtime();
        assert!(std::ptr::eq(r1, r2));
    }
}
