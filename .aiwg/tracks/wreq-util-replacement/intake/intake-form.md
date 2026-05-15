# SDLC Track Intake — wreq-util Replacement

**Track ID**: wreq-util-replacement
**Parent project**: carbonyl-agent
**Tracking issue**: #99 (License conflict: wreq-util is GPL-3.0 — blocks distribution of carbonyl-wreq in MIT wheel)
**Blocks**: #88 (pip-install integration for carbonyl-wreq native module)
**Created**: 2026-05-15

## Problem statement

`carbonyl-wreq` currently depends on `wreq-util` 2.x for browser-emulation presets — TLS ClientHello extension orderings, h2 SETTINGS values, header orderings observed from real Chrome/Firefox/Safari builds. `wreq-util` is licensed **GPL-3.0**, which is copyleft.

Distributing a carbonyl-agent wheel that statically links `wreq-util`-derived code forces the combined work to be GPL-3.0 — incompatible with carbonyl-agent's MIT license. This blocks #88 (pip-installable wreq transport) and therefore blocks GA on the W3B Phase 2 path.

## Scope

Replace the dependency on `wreq-util` with an in-house implementation of the browser-emulation presets, licensed compatibly with carbonyl-agent's MIT license.

### In scope

- A typed registry of browser emulation presets (Chrome, Firefox, Safari families, with desktop/mobile variants matching what the persona schema declares)
- Direct wiring from `Persona` fields to `wreq::Client::builder()` lower-level setters — bypassing the `wreq_util::Emulation` enum
- Preset data sourced from public observation (curl invocations, captured PCAPs, JA4 fingerprint databases, real-browser inspection) — all legally clean
- Wire-conformance tests that verify the in-house presets produce identical (or known-divergent and documented) ClientHello/h2 output compared to a reference capture of the real browser
- Replacement of every `wreq_util::Emulation` use site in `crates/carbonyl-wreq/src/client.rs`
- Removal of `wreq-util` from `Cargo.toml`
- Resumed full-workspace `THIRD_PARTY_LICENSES.txt` generation in `scripts/gen-third-party-licenses.sh`

### Out of scope

- Replacing `wreq` itself (Apache-2.0 — fine, keep it)
- Adding new emulation profiles beyond what the persona schema currently supports (Chrome 147, Firefox 150, Safari 26, Mobile Chrome 147, Mobile Safari 26)
- Replacing the persona schema itself (the existing Persona struct already carries JA4, h2_akamai, ALPN — those become the source of truth)

## Why this is solvable cleanly

The persona TOML already encodes everything wreq-util's presets encode:

- `[persona.network] ja4` — TLS ClientHello fingerprint
- `[persona.network] http2_akamai` — h2 SETTINGS frame in Akamai notation
- `[persona.network] alpn` — ALPN offer list
- `[persona.user_agent]` + `[persona.user_agent.ua_ch]` — header content
- Header ordering — implicit in family/version + ua-ch contract

The `wreq_util::Emulation` enum is a convenience wrapper that picks the *closest* preset to a real browser. Since our personas already carry the data, we can feed `wreq` directly without the wrapper. This is closer to what carbonyl-wreq conceptually should do anyway — persona is the source of truth.

## Success criteria

1. `cargo build --workspace` succeeds with `wreq-util` removed from `Cargo.toml`
2. Layer 1 (TLS) and Layer 2 (h2) wire conformance tests pass against the new preset implementation
3. `scripts/gen-third-party-licenses.sh` succeeds on the **full workspace** (currently restricted to `carbonyl-fingerprint`)
4. `THIRD_PARTY_LICENSES.txt` contains only permissively-licensed crates
5. The wheel built via `hatchling` carries the updated `THIRD_PARTY_LICENSES.txt`
6. No regression in the persona → JA4 / h2 / header mapping behavior for the five canonical families (Chrome 147 desktop/mobile, Firefox 150, Safari 26 desktop/mobile)
7. #99 closes; #88 unblocks

## Reference

- `crates/carbonyl-wreq/src/client.rs` — current `wreq_util::Emulation` use sites (lines 72, 232, 337, 349, 361, 373, 385)
- `~/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/wreq-util-2.2.6/src/emulation/device/` — what wreq-util provides (3,455 lines of preset data across chrome.rs, firefox.rs, safari.rs, opera.rs, okhttp.rs)
- Issue #99 — full finding writeup
- Layer 2 wire conformance crate — existing fixture infrastructure we extend

## Type / complexity

- **Type**: Feature replacement / dependency vendoring
- **Complexity**: Moderate. Mechanical replacement at the call sites; the substantive work is the preset data tables and the conformance test extensions.
- **Estimated agent passes**: 3–5 (research/spec, tables, integration + tests)
- **Quality gate**: `cargo test --workspace --features carbonyl-wreq/python` passes; full-workspace `cargo about generate` succeeds
