# ADR-W02 Addendum — wreq 5.x API Shape Investigation Result

**Status**: Accepted
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Refs**: [ADR-W02](./adr-002-persona-as-source-of-truth.md), [#101](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/101) item 1 (investigation gate)
**Investigated against**: `wreq` 5.3.0 (current pinned version per `crates/carbonyl-wreq/Cargo.toml`)

## Investigation question

Does `wreq` 5.x expose a low-level, public API for driving emulation directly — without depending on `wreq_util::Emulation` presets — sufficient for the persona-first model in ADR-W02?

The three possible outcomes from the design doc (`design-preset-registry.md` §3.1):

1. wreq exposes a `Profile::builder()` or analogous low-level API. Design proceeds as drafted.
2. wreq only accepts `Profile` values produced from `wreq_util::Emulation`. Either upstream PR, internal coupling, or unsafe shim required.
3. Something in between.

## Result: outcome 1 (clean win)

`wreq` 5.3 exposes everything ADR-W02 needs. The design proceeds as drafted. No upstream PR. No internal coupling. No unsafe.

## Evidence

### `wreq::EmulationProvider` is public and constructible

From `~/.cargo/registry/src/index.crates.io-*/wreq-5.3.0/src/client/emulation.rs`:

```rust
#[derive(TypedBuilder, Default, Debug)]
pub struct EmulationProvider {
    #[builder(default, setter(into))]
    pub(crate) tls_config: Option<TlsConfig>,
    #[builder(default, setter(into))]
    pub(crate) http1_config: Option<Http1Config>,
    #[builder(default, setter(into))]
    pub(crate) http2_config: Option<Http2Config>,
    #[builder(default, setter(into))]
    pub(crate) default_headers: Option<HeaderMap>,
    #[builder(default, setter(strip_option, into))]
    pub(crate) headers_order: Option<Cow<'static, [HeaderName]>>,
}

impl EmulationProviderFactory for EmulationProvider {
    fn emulation(self) -> EmulationProvider { self }
}
```

The setters are private (`pub(crate)`) but the **typed builder** (`EmulationProvider::builder()`) is public, so we construct via the builder. `EmulationProvider` itself implements `EmulationProviderFactory`, so it slots straight into `ClientBuilder::emulation()` — the exact same entry point `wreq_util::Emulation::Chrome137` uses today.

### `wreq::TlsConfig` exposes every wire field

From `wreq-5.3.0/src/tls/config.rs`:

```rust
pub struct TlsConfig {
    pub cert_store: Option<Cow<'static, CertStore>>,
    pub cert_verification: bool,
    pub tls_sni: bool,
    pub verify_hostname: bool,
    pub alpn_protos: AlpnProtos,
    pub alps_protos: Option<AlpsProtos>,
    pub alps_use_new_codepoint: bool,
    pub session_ticket: bool,
    pub min_tls_version: Option<TlsVersion>,
    pub max_tls_version: Option<TlsVersion>,
    pub pre_shared_key: bool,
    pub enable_ech_grease: bool,
    pub permute_extensions: Option<bool>,
    pub grease_enabled: Option<bool>,
    pub enable_ocsp_stapling: bool,
    pub enable_signed_cert_timestamps: bool,
    pub record_size_limit: Option<u16>,
    pub psk_skip_session_ticket: bool,
    pub key_shares_limit: Option<u8>,
    pub psk_dhe_ke: bool,
    pub renegotiation: bool,
    pub delegated_credentials: Option<Cow<'static, str>>,
    pub cipher_list: Option<Cow<'static, str>>,
    pub curves: Option<Cow<'static, [SslCurve]>>,
    pub sigalgs_list: Option<Cow<'static, str>>,
    pub cert_compression_algorithm: Option<Cow<'static, [CertCompressionAlgorithm]>>,
    pub extension_permutation_indices: Option<Cow<'static, [u8]>>,
    pub aes_hw_override: Option<bool>,
    pub random_aes_hw_override: bool,
}
```

Every field is `pub`. The mapping from ADR-W02's `TlsProfile` to `TlsConfig`:

| `TlsProfile` field (our design) | `TlsConfig` field (wreq 5.x) | Notes |
|---|---|---|
| `extension_order: &[u16]` | `extension_permutation_indices: Option<Cow<[u8]>>` | wreq uses **u8 indices** into a known-extension table, not raw u16 IDs. Design adjusts to match (see "Design adjustment" below). |
| `cipher_order: &[u16]` | `cipher_list: Option<Cow<str>>` | wreq uses an OpenSSL-style cipher-list **string** (e.g. `"TLS_AES_128_GCM_SHA256:..."`). Design adjusts. |
| `alpn_default: &[&str]` | `alpn_protos: AlpnProtos` | Public newtype wrapping `&'static [u8]` (see `tls/mod.rs`). |
| `grease_positions: &[usize]` | `grease_enabled: Option<bool>` + `permute_extensions: Option<bool>` | wreq does NOT expose per-position GREASE control; it's a global on/off. Per ADR-W02 framing, this is fine — the preset records "Chrome enables GREASE" as `Some(true)` rather than the specific positions. |
| `supported_groups: &[u16]` | `curves: Option<Cow<[SslCurve]>>` | `SslCurve` is a public re-export from `boring2::ssl`. |
| `signature_algorithms: &[u16]` | `sigalgs_list: Option<Cow<str>>` | OpenSSL-style colon-separated string. Design adjusts. |

Three of the design fields adjust (u16 IDs → wreq's preferred encoding). None of them block the design — the preset table just stores wreq's encoding instead of raw IDs. Conversion happens at preset-table-construction time (a one-shot lookup in IANA + boring's enum), not at request time.

### `wreq::Http2Config` covers the h2 surface

The same `EmulationProvider` carries `http2_config: Option<Http2Config>`. We already use `Http2Builder` setters via `ClientBuilder::http2(|h2| ...)` in current `client.rs`; `Http2Config` is the data form of that. The persona's `http2_akamai`-derived data flows through it directly.

### `headers_order` is the header-ordering field we need

`EmulationProvider::headers_order: Option<Cow<'static, [HeaderName]>>` is exactly the "default header emission order" the persona-audit's Header Profile §3 calls for. Persona declares values; preset declares order via this field; both flow through the same `EmulationProvider` to wreq.

### Re-export of underlying types confirms public surface

From `wreq-5.3.0/src/lib.rs`:

```rust
pub use boring2::ssl::{CertCompressionAlgorithm, ExtensionType, SslCurve};
pub use hyper2::{Priority, PseudoOrder, SettingsOrder, StreamDependency, StreamId};
```

`ExtensionType`, `SslCurve`, `PseudoOrder`, `SettingsOrder` are all public types we can name in the preset tables.

## Design adjustment (minor)

`design-preset-registry.md` §2.1 declares preset fields as raw `&'static [u16]`. The actual presets will store wreq-friendly encodings:

```rust
// Design: design-preset-registry.md §2.1 (drafted)
pub struct TlsProfile {
    pub extension_order: &'static [u16],
    pub cipher_order: &'static [u16],
    pub alpn_default: &'static [&'static str],
    pub grease_positions: &'static [usize],
    pub supported_groups: &'static [u16],
    pub signature_algorithms: &'static [u16],
}

// Adjusted to match wreq 5.3 surface:
pub struct TlsProfile {
    /// Indices into wreq's known-extension table. None means "wreq default order".
    pub extension_permutation_indices: Option<&'static [u8]>,
    /// OpenSSL-style cipher list, colon-separated (e.g. "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:...").
    pub cipher_list: Option<&'static str>,
    /// ALPN offer list (default; persona overrides).
    pub alpn_default: &'static [&'static str],
    /// GREASE on/off. Per-position control isn't exposed by wreq;
    /// browsers that emit GREASE get `Some(true)`.
    pub grease_enabled: Option<bool>,
    /// Permute extensions (Chrome 110+ randomizes). `Some(true)` for Chrome.
    pub permute_extensions: Option<bool>,
    /// Supported groups, as wreq's SslCurve re-export.
    pub curves: &'static [wreq::SslCurve],
    /// Signature algorithms, OpenSSL-style colon-separated string.
    pub sigalgs_list: Option<&'static str>,
}
```

Build flow becomes:

```rust
// presets/mod.rs (sketch)
pub fn to_emulation(persona: &Persona, preset: &PresetTable) -> wreq::EmulationProvider {
    let mut tls = wreq::TlsConfig::default();
    tls.alpn_protos = wreq::AlpnProtos::from_persona_or_preset(...); // persona wins
    tls.cipher_list = preset.tls.cipher_list.map(Cow::Borrowed);
    tls.extension_permutation_indices = preset.tls.extension_permutation_indices.map(Cow::Borrowed);
    tls.curves = Some(Cow::Borrowed(preset.tls.curves));
    tls.sigalgs_list = preset.tls.sigalgs_list.map(Cow::Borrowed);
    tls.grease_enabled = preset.tls.grease_enabled;
    tls.permute_extensions = preset.tls.permute_extensions;
    // ... etc

    let h2 = build_h2_config(persona, &preset.h2); // persona's http2_akamai wins per-key

    let mut headers = http::HeaderMap::new();
    apply_persona_headers(&mut headers, persona); // UA, Accept-Language, sec-ch-ua trio (Chrome)
    apply_preset_default_headers(&mut headers, &preset.headers); // Accept, Accept-Encoding, sec-fetch-*

    wreq::EmulationProvider::builder()
        .tls_config(tls)
        .http2_config(h2)
        .default_headers(headers)
        .headers_order(Cow::Borrowed(&preset.headers.default_order))
        .build()
}

// In WreqClient::build:
let provider = presets::to_emulation(&self.persona, preset);
let client = wreq::Client::builder().emulation(provider).build()?;
```

This is exactly the shape ADR-W02's sequence diagram describes — persona-first, preset-backstop, single `emulation()` call to wreq.

## Implications for the iteration plan

Per `iteration-plan.md` Iteration A item 1: "If yes, proceed; if no, addendum to ADR-W02 and possibly upstream PR before continuing."

**Yes.** No upstream PR. The minor design adjustments above don't change ADR-W02's decision or rationale — they just align preset field encodings with wreq's preferred input shapes.

Iteration A items 2 (preset module scaffolding), 4 (test helper), 5 (L2 conformance), 6 (CI job) all proceed as planned.

The fixture-capture HITL items (Iteration A item 3, Iteration B items 1a–1d) are unchanged — capture procedure is browser-side, not wreq-side.

## Outcome summary

| Question | Answer |
|---|---|
| Does wreq 5.x expose a low-level emulation API? | Yes (`EmulationProvider::builder()`) |
| Does it expose `TlsConfig` field-level setters? | Yes (every field `pub`) |
| Can we construct emulation without `wreq_util`? | Yes |
| Is the design from ADR-W02 / `design-preset-registry.md` viable? | Yes, with minor field-encoding adjustments |
| Are upstream PRs to wreq required? | No |
| Is unsafe required? | No |
| Does ADR-W02 need revision? | No (this addendum updates the field encodings in §2.1 of the design doc; ADR's decision and rationale stand) |

## References

- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md §2.1, §3.1
- @.aiwg/tracks/wreq-util-replacement/reports/persona-audit-report.md (companion analysis on persona schema sufficiency)
- `wreq` 5.3.0 source: `~/.cargo/registry/src/index.crates.io-*/wreq-5.3.0/src/{client/emulation.rs,tls/config.rs,tls/mod.rs,client/http.rs}`
- @crates/carbonyl-wreq/src/client.rs (current integration site, to be rewritten in Iteration B item 6)
