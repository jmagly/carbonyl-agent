# Persona-Completeness Audit — wreq-util Replacement (Pre-Iteration-A Gate)

**Status**: Final
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Author**: carbonyl-agent maintainer
**Refs**: closes [#104](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/104), unblocks [#101](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/101), informs [ADR-W02](../architecture/adr-002-persona-as-source-of-truth.md)

## TL;DR — sign-off statement

**The persona schema is sufficient for the persona-first inversion as drafted in ADR-W02, with two narrow caveats** that are addressable as parser-side follow-ups, not schema changes:

1. Two parsed sub-fields of `network.http2_akamai` are presently discarded by the parser (PRIORITY frame detail, pseudo-header order). The schema *carries* them — the parser just doesn't surface them. Filed as follow-up to Iteration A, not a schema/ADR blocker.
2. The persona schema records JA4 as a **hash**, not a recipe. By construction a JA4 hash cannot reconstruct the underlying ClientHello byte sequence, so the preset must always supply extension order, cipher order, GREASE positions, supported groups, and signature algorithms. ADR-W02 is consistent with this — its "persona-first, preset-backstop" framing is exactly right — but it deserves a one-paragraph clarification that JA4 functions as an *integrity check* on preset selection, not as a wire-byte source. Filed as ADR-W02 addendum, not a revision.

ADR-W02 proceeds without revision. Iteration A may begin.

## Method

For each of the four wire surfaces enumerated in [#104](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/104) — TLS ClientHello composition, h2 SETTINGS frame composition, header order/content, body-emission semantics — this audit asks:

1. What fields does the in-house preset registry need to declare to drive that surface? (per [`design-preset-registry.md`](../architecture/design-preset-registry.md) §2)
2. Which of those fields does the persona schema declare today? (per [`crates/carbonyl-fingerprint/src/schema.rs`](../../../../crates/carbonyl-fingerprint/src/schema.rs) and the working parsers in [`http.rs`](../../../../crates/carbonyl-fingerprint/src/http.rs))
3. For fields the persona does NOT declare: is the gap one the persona *should* close (schema change, ADR-W02 revision needed), or one the preset is correctly positioned to backstop (no schema change, ADR-W02 stands)?

The five canonical personas — Chrome 147 desktop, Chrome 147 mobile (Android), Firefox 150 desktop, Safari 26 macOS, Safari 26 iOS — share the same persona schema. The only family-specific schema variation is the UA-CH sub-table (populated for Chrome; empty for Firefox/Safari per the validator's family-gated rules). Per-surface analysis therefore applies uniformly across all five; family-specific notes are called out where they exist.

The Chrome 147 desktop persona has a real example file at `examples/personas/desktop-chrome-linux.toml`. The other four personas do not have example files yet — they will be produced by the Iteration A/B fixture-capture HITL steps. The schema applies uniformly across families, so audit conclusions transfer.

## Per-persona summary table

| Persona | TLS ClientHello | h2 SETTINGS | Headers | Body |
|---|---|---|---|---|
| Chrome 147 desktop | YELLOW | GREEN | YELLOW | N/A |
| Chrome 147 mobile (Android) | YELLOW | GREEN | YELLOW | N/A |
| Firefox 150 desktop | YELLOW | GREEN | YELLOW | N/A |
| Safari 26 macOS | YELLOW | GREEN | YELLOW | N/A |
| Safari 26 iOS | YELLOW | GREEN | YELLOW | N/A |

Color key:
- **GREEN** — persona declares everything required at sufficient fidelity; preset has no work to do.
- **YELLOW** — persona declares the *discriminative* signal but not the full byte recipe; preset correctly backstops the rest. Aligned with ADR-W02 by design.
- **RED** — persona is missing a field it should declare; ADR-W02 needs revision before construction.

There are no RED findings.

## Detailed per-surface audit

### 1. TLS ClientHello composition

#### What the preset needs to drive

Per `design-preset-registry.md` §2.1, `TlsProfile` declares:

- `extension_order: &'static [u16]` — ClientHello extension type IDs in wire order
- `cipher_order: &'static [u16]` — cipher suite IDs in wire order
- `alpn_default: &'static [&'static str]` — ALPN offer list (persona overrides)
- `grease_positions: &'static [usize]` — GREASE values' positions within `extension_order`
- `supported_groups: &'static [u16]` — supported_groups extension contents in order
- `signature_algorithms: &'static [u16]` — signature_algorithms extension contents in order

#### What the persona declares

[`schema::Network`](../../../../crates/carbonyl-fingerprint/src/schema.rs#L153) carries:

```rust
pub struct Network {
    pub ja4: String,
    pub ja4h_template: String,
    pub http2_akamai: String,
    pub alpn: Vec<String>,
    pub http3_enabled: bool,
}
```

`ja4` is a hash of TLS version + ALPN hint + cipher count + extension count + signature algorithm count, plus a digest of the cipher list, extension list, and signature algorithm list (per the FoxIO JA4 spec). It is **one-way by design**. You can't run a JA4 hash backward to obtain the cipher order or the extension order; you can only use it to *check* that a candidate ClientHello matches.

`alpn` is the only structural TLS field the persona declares directly.

#### Gap classification

| Preset field | Persona declares? | Persona-side fix? | Preset backstop appropriate? |
|---|---|---|---|
| `extension_order` | No (only the JA4 hash) | No — would require duplicating the byte recipe in every persona, defeating JA4's purpose | Yes — preset is the canonical source |
| `cipher_order` | No (only the JA4 hash) | No, same reason | Yes |
| `alpn_default` | Yes (`network.alpn`) | N/A | Persona wins; preset only used when persona omits |
| `grease_positions` | No | No — GREASE positions are an artifact of the browser implementation, not a persona attribute | Yes |
| `supported_groups` | No (only the JA4 hash digest contains a sum of these) | No, same reason | Yes |
| `signature_algorithms` | No (only the JA4 hash digest contains a sum of these) | No, same reason | Yes |

**Verdict**: YELLOW. Persona declares the discriminative signal (JA4 + ALPN). Preset must backstop the byte recipe. This matches ADR-W02 exactly.

**Important framing point for ADR-W02 addendum**: JA4's role in the persona-first model is **integrity check**, not constructor. The build flow is:

1. Persona declares JA4.
2. Preset selected by `(family, version, platform)` triple supplies the ClientHello byte recipe.
3. Conformance test (Layer 2) re-computes JA4 from the emitted ClientHello bytes and asserts it matches the persona-declared JA4.

If step 3 fails, the preset is wrong (or the persona's JA4 is from a different browser build than the preset captured). The check catches preset/persona mismatch at test time.

#### Family-specific notes

- **Chrome (desktop + mobile)**: GREASE positions differ between desktop and mobile (Chrome on Android uses a slightly different extension layout). Two presets, one per platform, handle this. No persona difference.
- **Firefox 150**: No GREASE in Firefox's ClientHello (Firefox does not emit GREASE values). `grease_positions` will be `&[]`. No persona difference.
- **Safari 26**: macOS and iOS Safari differ in `supported_groups` order (iOS prefers x25519 first; macOS still leads with secp256r1 for some builds). Two presets handle this. No persona difference.

### 2. h2 SETTINGS frame composition

#### What the preset needs

Per `design-preset-registry.md` §2.1, `H2Profile`:

- `settings_default: &'static [(u16, u32)]` — SETTINGS entries in declared order
- `initial_connection_window: u32`
- `pseudo_header_order: &'static [&'static str]` — for HEADERS frames

#### What the persona declares

`network.http2_akamai` is the Akamai H2 fingerprint format: `settings|window|priority|pseudo` separated by `|`. Each section:

1. `settings`: comma-separated `id:value` pairs in order. Parsed by [`H2Settings::from_akamai`](../../../../crates/carbonyl-fingerprint/src/http.rs#L48) into `Vec<(u16, u32)>` — **preserves both keys/values and order**.
2. `window`: a single u32. Parsed by [`H2WindowUpdate::from_akamai`](../../../../crates/carbonyl-fingerprint/src/http.rs#L84).
3. `priority`: PRIORITY frame encoding. Parsed by [`H2Priority::from_akamai`](../../../../crates/carbonyl-fingerprint/src/http.rs#L116) but the parser **discards detail** today (returns `Default::default()` even when the section is non-empty). The schema *carries* the data inside the string; the Rust struct doesn't surface it.
4. `pseudo`: a comma-separated list like `m,a,s,p` (`:method, :authority, :scheme, :path`). **No parser exists** for this section yet. The schema carries it inside the `http2_akamai` string; no struct exposes it.

The example persona's `http2_akamai = "1:65536,2:0,3:1000,4:6291456,6:262144|15663105|0|m,a,s,p"` carries all four sections.

#### Gap classification

| Preset field | Persona declares? | Persona-side fix? | Preset backstop appropriate? |
|---|---|---|---|
| `settings_default` (entries + order) | Yes — fully parsed | N/A | Persona wins; preset only used when persona omits |
| `initial_connection_window` | Yes — fully parsed | N/A | Persona wins |
| `pseudo_header_order` | **Yes in the schema string, no in the parser** | Parser-side: add a parser for the 4th `\|` section. No schema change needed. | Preset is the fallback when persona omits |
| PRIORITY frame detail | **Yes in the schema string, no in the parser** | Parser-side: extend `H2Priority::from_akamai` to surface frame detail, or replace the empty-default behavior. No schema change needed. | Preset is the fallback when persona omits |

**Verdict**: GREEN at the schema level. The persona's `http2_akamai` string already carries every byte the preset declares for h2 SETTINGS. The two yellow-flag items are **parser gaps**, not schema gaps — the data is in the persona; the Rust types just don't pull it out.

**Follow-up filed**: parser-side work to surface pseudo-header order and PRIORITY frame detail. Tracked as Iteration A scope item (item 1 — investigation phase — discovers what wreq's API needs and whether the parser gaps need to close in the same window).

#### Family-specific notes

- **Chrome 147**: SETTINGS list typically `1, 2, 3, 4, 6` (HEADER_TABLE_SIZE, ENABLE_PUSH, MAX_CONCURRENT_STREAMS, INITIAL_WINDOW_SIZE, MAX_HEADER_LIST_SIZE). Pseudo-header order `m,a,s,p`. No persona difference.
- **Firefox 150**: SETTINGS list differs (Firefox often omits `MAX_CONCURRENT_STREAMS`; sends only `1, 4, 5`). Pseudo-header order matches Chrome. The persona's `http2_akamai` string captures this byte-for-byte.
- **Safari 26**: SETTINGS list smaller still; Safari typically sends `2, 3, 4`. Pseudo-header order can differ on iOS. The persona's `http2_akamai` captures whatever the actual capture revealed.

### 3. Header order and content

#### What the preset needs

Per `design-preset-registry.md` §2.1, `HeaderProfile`:

- `default_order: &'static [&'static str]` — header names in emission order. Values come from the persona; the preset only fixes ordering.

#### What the persona declares

Persona-declared header *values* (via [`apply_persona`](../../../../crates/carbonyl-fingerprint/src/http.rs#L164)):

- `user_agent.full` → `User-Agent`
- `locale.accept_language` → `Accept-Language`
- `user_agent.ua_ch.brands` → `sec-ch-ua` (Chrome family only)
- `user_agent.ua_ch.mobile` → `sec-ch-ua-mobile` (Chrome family only)
- `user_agent.ua_ch.platform` → `sec-ch-ua-platform` (Chrome family only)

Per-family gating is correct: Firefox and Safari personas do NOT emit `sec-ch-ua*` headers (those would unmask the bot). The validator enforces this at persona-build time.

Persona does NOT declare:

- `Accept` — typically `text/html,...` for navigations; varies by family but not by persona instance
- `Accept-Encoding` — typically `gzip, deflate, br, zstd` for Chrome; differs for Safari
- `Upgrade-Insecure-Requests`
- `sec-fetch-*` family (`-site`, `-mode`, `-user`, `-dest`)
- `Priority` (HTTP)
- `sec-ch-ua-arch`, `sec-ch-ua-bitness`, `sec-ch-ua-full-version-list`, `sec-ch-ua-model`, `sec-ch-ua-platform-version`, `sec-ch-ua-wow64` (the high-entropy UA-CH family — only Chrome, only on certain navigations)
- The full ordered list of default headers
- Header casing convention (Chrome lowercases h2 headers; Safari preserves more capitalization in some flows)

The high-entropy UA-CH headers are technically derivable from `user_agent.ua_ch.full_version_list`, `user_agent.ua_ch.architecture`, etc., which the persona schema *does* carry — but no current code emits them.

#### Gap classification

| Preset field | Persona declares? | Persona-side fix? | Preset backstop appropriate? |
|---|---|---|---|
| `default_order` (full ordered list) | No | Could declare it, but order is family-determined, not persona-determined — putting it in the persona forces every persona to repeat the same family-default | Yes — preset is the right home |
| Header values for `User-Agent`, `Accept-Language`, `sec-ch-ua` family (low-entropy) | Yes | N/A | Persona wins |
| Header values for `Accept`, `Accept-Encoding`, `Upgrade-Insecure-Requests`, `sec-fetch-*`, `Priority` | No | These are navigation/request-type-specific, not persona-instance-specific. Belongs in preset | Yes |
| Header values for high-entropy UA-CH (`sec-ch-ua-arch`, `sec-ch-ua-bitness`, `sec-ch-ua-full-version-list`, `sec-ch-ua-model`, `sec-ch-ua-platform-version`, `sec-ch-ua-wow64`) | Yes — fields exist in `UaCh` schema | Parser-side: extend `apply_persona` to emit these for Chrome family. No schema change needed. | Persona wins when emitter is added |
| Header casing | No | Casing is a wire-protocol-level concern, not a persona attribute | Yes |

**Verdict**: YELLOW. Persona declares the discriminative low-entropy headers (UA, Accept-Language, sec-ch-ua trio) plus carries the data for high-entropy UA-CH headers in the `UaCh` schema. Preset must backstop the always-on family-default headers (Accept, Accept-Encoding, sec-fetch-*, etc.) and the full default order. ADR-W02 is consistent with this.

**Follow-up filed**: `apply_persona` should emit high-entropy UA-CH headers for Chrome family from existing schema data. Parser-side; no schema change. Tracked as a small follow-up to Iteration B (when the Chrome 147 mobile preset goes in — mobile profiles routinely demand the high-entropy UA-CH set).

### 4. Body-emission semantics

#### What the preset needs

Nothing in `design-preset-registry.md` §2 specifies body-emission fields. Body shape (Content-Length vs. Transfer-Encoding: chunked, body compression, h2 DATA frame chunking, etc.) is a per-request concern, not a persona-level constant. wreq handles this based on the request body the caller supplies.

#### Gap classification

N/A. Body emission is not in the persona's scope and shouldn't be. No preset backstop needed; no persona schema change needed.

**Verdict**: N/A. ADR-W02 is silent on body for the same reason — correctly so.

## Cross-cutting findings

### Finding 1: parser surfaces less than the schema carries

The Rust `H2Priority` parser returns `Default::default()` for non-empty PRIORITY sections, and no parser exists for the 4th Akamai section (pseudo-header order). The persona schema (the TOML string itself) carries this data; the Rust types just don't extract it.

Impact on ADR-W02: low. The wreq build flow needs pseudo-header order. Today it would fall through to the preset (correct fallback per ADR-W02). Tomorrow, when the parser is extended, the persona will win for personas that captured non-default values. Both states are sound.

Recommendation: file Iteration A scope-item-1 (investigation) discovery output to include a quick check on the priority/pseudo-order parser gap and either close it in the same iteration or defer to Iteration B. Already aligned with the iteration plan's "investigation gate" framing.

### Finding 2: JA4 is a check, not a constructor

JA4 is a one-way hash. The persona-first language in ADR-W02 ("persona declares value? use it; persona silent? use preset") reads naturally for fields like ALPN or h2 SETTINGS where the persona carries the actual value. For TLS ClientHello byte fields, no persona ever "declares the value" — the persona only declares the JA4 hash; the preset always supplies the bytes; the conformance test asserts JA4-of-bytes equals persona-JA4.

ADR-W02 is internally consistent with this — its sequence diagram shows wire fields the persona "declares" and ones it doesn't. The clarification needed is one paragraph to make explicit that for TLS ClientHello fields specifically, "persona declares" means "persona declares the JA4 hash, used as a check against the preset's recipe".

Recommendation: file an addendum to ADR-W02 (`adr-002-addendum-ja4-as-check.md`) covering this clarification. Not a revision; the ADR's decision and rationale are unchanged.

### Finding 3: high-entropy UA-CH data is in the schema but not emitted

`UaCh` carries `full_version_list`, `architecture`, `bitness`, `model`, `wow64`, `platform_version` — exactly the fields needed to emit high-entropy `sec-ch-ua-*` headers for Chrome. The current `apply_persona` only emits the low-entropy trio. For Chrome desktop, this is mostly fine (high-entropy headers are server-requested via Accept-CH on subsequent requests, not initial). For Chrome mobile, the high-entropy set is more frequently emitted on first-request, and a captured fixture will show their absence as a divergence.

Recommendation: file as Iteration B follow-up. Schema unchanged; emitter extended.

## Recommendations to the track

| # | Recommendation | Owner | Tracked as |
|---|---|---|---|
| 1 | ADR-W02 stands. Proceed with Iteration A as planned. | track | (this report's sign-off) |
| 2 | File ADR-W02 addendum clarifying JA4-as-check for ClientHello fields | track | new issue (post-#101 kickoff) |
| 3 | Investigate `H2Priority` parser detail and 4th-section pseudo-header parser during Iteration A item 1 (investigation) | implementer | folded into [#101](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/101) item 1 |
| 4 | Extend `apply_persona` to emit high-entropy `sec-ch-ua-*` headers from `UaCh` for Chrome family | implementer | folded into [#102](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/102) Chrome mobile preset (item 4) |
| 5 | When the four remaining canonical persona fixtures are captured (Iteration A/B), spot-check this audit's per-family notes against the captured ground truth | implementer | folded into [#101](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/101)/[#102](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/102) capture steps |

No persona schema changes recommended.
No ADR-W02 revisions recommended.

## Sign-off

> **Persona schema is sufficient for the persona-first inversion as drafted.** ADR-W02 proceeds without revision. Iteration A may begin.

— carbonyl-agent maintainer, 2026-05-15

## References

- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md §2 (preset types)
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md (capture procedure for the four remaining personas)
- @crates/carbonyl-fingerprint/src/schema.rs (persona schema)
- @crates/carbonyl-fingerprint/src/http.rs (apply_persona, Akamai parsers)
- @crates/carbonyl-wreq/src/client.rs §lines 1–33 (current module docstring; rewritten in Iteration B item 7)
- @examples/personas/desktop-chrome-linux.toml (sole existing canonical persona example; the other four produced by Iteration A/B fixture capture)
- [FoxIO JA4 specification](https://github.com/FoxIO-LLC/ja4) (JA4 as one-way hash — supports Finding 2)
