# UC-W01: In-House Browser Emulation Preset Registry

**Track**: wreq-util-replacement
**Status**: Draft
**Priority**: Critical (blocks #88, closes #99)
**Date**: 2026-05-15
**Refs**: roctinam/carbonyl-agent#99

## Primary Actor

`carbonyl-wreq` crate maintainer — sole consumer of preset data when wiring `Persona` → `wreq::Client`.

## Goal

Provide a typed, MIT-licensed Rust registry of browser emulation preset data (TLS ClientHello extension order, h2 SETTINGS defaults, ALPN offer lists, header ordering) covering the five canonical persona families, so `carbonyl-wreq` can drive `wreq::Client::builder()` directly without depending on the GPL-3.0 `wreq-util` crate.

## Preconditions

- The persona schema in `carbonyl-fingerprint` already carries JA4, h2_akamai, ALPN, and UA-CH fields (W3A baseline complete).
- `wreq` (Apache-2.0/MIT) is on the dependency graph and exposes the lower-level `ClientBuilder`/`Http2Builder` setters used today.
- Reference fixtures from public browser observation are available or capturable for the five families: Chrome 147 desktop, Chrome 147 mobile (Android), Firefox 150 desktop, Safari 26 desktop (macOS), Safari 26 mobile (iOS).

## Main Success Scenario

1. The maintainer adds a new module `carbonyl_wreq::presets` containing a typed `PresetTable` for each family/version.
2. Each `PresetTable` declares ClientHello extension ordering, GREASE pattern, ALPN offer, h2 SETTINGS defaults, and pseudo-header ordering.
3. Preset data is sourced from independently-captured observations (PCAP, JA4 databases, real-browser inspection) — never copied from `wreq-util`'s tables.
4. A lookup function `preset_for(family, version, platform)` returns the matching `PresetTable` or the nearest neighbor with a documented diagnostic.
5. The registry compiles cleanly with `cargo build --workspace` once `wreq-util` is removed from `Cargo.toml`.
6. `cargo about generate` on the full workspace produces a `THIRD_PARTY_LICENSES.txt` containing only permissive licenses.

## Alternative Flows

**A1: Persona requests an unmapped family/version**
- Lookup returns the nearest registered preset.
- A `tracing::warn!` event is emitted naming the requested triple and the substituted preset.
- The conformance test crate validates the substitution remains plausible (Layer 1 still passes within documented tolerance).

**A2: A new browser version ships upstream and the persona schema is updated**
- The maintainer extends the registry by adding one new `PresetTable` entry referencing a captured fixture.
- No edits to `carbonyl-wreq::client` are required — the lookup function picks up the new entry by family/version match.

## Exception Flows

**E1: Captured fixture missing for a declared family**
- Registry build fails at compile time via a `compile_error!` or `assert!` in a `build.rs` step.
- Maintainer must produce the fixture (per `fixtures-plan.md`) before merging.

**E2: License audit fails post-merge**
- CI gate `cargo-about` fails the workspace check.
- Maintainer reverts the change and investigates whether a transitive dependency was introduced.

## Postconditions

- `Cargo.toml` no longer references `wreq-util`.
- `THIRD_PARTY_LICENSES.txt` regenerated against the full workspace.
- `client.rs::persona_to_emulation` deleted; replaced by `presets::preset_for` callers.
- All five canonical fixtures still pass `cargo test --workspace --features carbonyl-wreq/python`.

## Acceptance Criteria

- [ ] `carbonyl_wreq::presets` module compiles with `#![forbid(unsafe_code)]`.
- [ ] `PresetTable` is exported and stable enough for the conformance crate to assert against by structural equality.
- [ ] All five canonical persona fixtures resolve to a preset without warnings.
- [ ] `cargo about generate --config about.toml --output-file THIRD_PARTY_LICENSES.txt` succeeds on the full workspace.
- [ ] No file in `crates/carbonyl-wreq/src/presets/` contains content lifted from `wreq-util`; provenance for each preset's reference fixture is documented in `fixtures-plan.md`.
- [ ] License header on every preset module is `MIT OR Apache-2.0` matching the workspace policy.

## Non-Functional Requirements

- See `@.aiwg/tracks/wreq-util-replacement/requirements/nfr.md` (license compliance, wire fidelity, performance budget, maintainability).

## Reasoning

1. **Problem Analysis**: `wreq-util` is GPL-3.0; statically linking it into a MIT wheel forces the combined work to GPL-3.0. The data we need from it (preset tables) is not creative expression but observation of real browser behavior, which we can independently re-capture.
2. **Constraint Identification**: Must replace data tables only — not the `wreq` crate itself, which is permissively licensed. Must keep persona schema unchanged. Must not regress the existing five-family conformance.
3. **Alternative Consideration**: (a) GPL the whole project — rejected, breaks downstream MIT consumers. (b) Dynamic linkage of wreq-util — legally murky for distributing a wheel. (c) Vendored permissively-licensed re-implementation — selected.
4. **Decision Rationale**: Independent observation is the only path that preserves both license compliance and behavioral fidelity. The data tables are factual content (observed bytes) rather than copyrightable expression, but we re-capture rather than rely on that argument.
5. **Risk Assessment**: Capturing PCAPs from real browsers requires reproducible test rigs; if a fixture is captured incorrectly, conformance silently passes against the wrong reference. Mitigation: cross-check fixtures against published JA4 databases before commit.

## References

- @.aiwg/tracks/wreq-util-replacement/intake/intake-form.md - Track scope
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-001-preset-registry-design.md - Registry shape
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md - Concrete module layout
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md - Reference capture procedure
- @crates/carbonyl-wreq/src/client.rs - Integration surface (line 72 emulation field, lines 232–251 mapping)
- @.aiwg/architecture/adr-005-tls-fingerprint-http-client.md - Parent decision; bus-factor mitigation
