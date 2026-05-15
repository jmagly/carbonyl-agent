# UC-W02: Direct Persona-to-wreq Wiring

**Track**: wreq-util-replacement
**Status**: Draft
**Priority**: Critical (depends on UC-W01)
**Date**: 2026-05-15
**Refs**: roctinam/carbonyl-agent#99, blocks #88

## Primary Actor

`WreqClient::build` — consumer of the persona record at request-construction time.

## Goal

Replace the `wreq_util::Emulation`-mediated path in `crates/carbonyl-wreq/src/client.rs` with a direct mapping from `Persona` fields onto `wreq::ClientBuilder` / `wreq::Http2Builder` setters, using the in-house `PresetTable` (UC-W01) only as a data backstop. The persona becomes the authoritative source of wire shape; presets fill in the gaps the persona does not declare.

## Preconditions

- UC-W01 complete: `carbonyl_wreq::presets` registry exists and resolves the five canonical families.
- `wreq` exposes per-field setters for TLS extension order, ALPN, h2 SETTINGS, pseudo-header order. (Verified against `wreq` 5.x at intake time.)
- `Persona.network.ja4`, `Persona.network.http2_akamai`, `Persona.network.alpn`, `Persona.user_agent` are populated for the five canonical fixtures.

## Main Success Scenario

1. `WreqClient::apply_persona_typed(&Persona)` records persona fields into `PendingConfig` (same as today).
2. `WreqClient::build()` consumes `PendingConfig` and constructs `wreq::Client::builder()`.
3. Build pulls the `PresetTable` via `presets::preset_for(family, version, platform)` to source ClientHello extension order, GREASE pattern, and any wire fields the persona does not declare.
4. Persona-declared fields override preset defaults: `ja4` selects ClientHello extension ordering; `http2_akamai` overrides h2 SETTINGS entries; `alpn` overrides the ALPN offer; UA-CH headers override defaults.
5. `wreq::Client::builder().build()` returns a configured client whose first request produces a ClientHello and h2 SETTINGS matching the captured fixture for that persona.
6. The `emulation` field on `PendingConfig` is removed; `persona_to_emulation` is deleted.

## Alternative Flows

**A1: Persona declares a field the preset does not cover**
- Persona value applied verbatim via the relevant `wreq` setter.
- No fallback to preset for that field.

**A2: Persona omits a field the preset covers**
- Preset value used as the default.
- An assertion records the preset-derived value into `PendingConfig` so the conformance crate can inspect it.

## Exception Flows

**E1: `wreq` does not expose a setter for a wire field the persona declares**
- Build fails with `WreqError::UnsupportedField { field, persona_field, wreq_setter_missing: true }`.
- Track this in the conformance plan as a known divergence; do not silently drop the field.

**E2: Persona declares conflicting values (e.g. ja4 implies TLS 1.3 but alpn lists only http/1.1)**
- `apply_persona_typed` returns `FingerprintError::Inconsistent { reason }` before build is attempted.

## Postconditions

- `PendingConfig::emulation: Option<wreq_util::Emulation>` is removed.
- `persona_to_emulation` is deleted.
- `WreqClient::build()` produces a `wreq::Client` whose wire output is structurally equivalent (within documented tolerance) to the persona spec, validated by Layer 1 + Layer 2 conformance.

## Acceptance Criteria

- [ ] Grep for `wreq_util` across `crates/carbonyl-wreq/src/` returns zero matches.
- [ ] Grep for `Emulation::` across `crates/carbonyl-wreq/src/` returns zero matches.
- [ ] All six existing tests in `client.rs::tests` updated to assert structural preset selection rather than `wreq_util::Emulation::Chrome137` equality, and still pass.
- [ ] At least one new test per family asserts that an explicit persona override (e.g., a non-default `http2_akamai`) reaches the wire — checked through the conformance crate's `ApplyInspector`.
- [ ] `cargo doc --no-deps -p carbonyl-wreq` succeeds with the updated module docs (the current header at line 1–33 of client.rs needs rewriting).

## Non-Functional Requirements

- See `@.aiwg/tracks/wreq-util-replacement/requirements/nfr.md`.

## Reasoning

1. **Problem Analysis**: The current path picks the *closest* `wreq_util::Emulation` to the persona, then overlays h2 SETTINGS overrides on top. This is two-step: preset-then-override. The preset enum is a coarse approximation (Chrome 137 stands in for Chrome 147). When we own the preset data, we no longer need the enum layer — the persona drives directly.
2. **Constraint Identification**: `wreq::ClientBuilder` does not expose every wire field individually. ClientHello extension order, GREASE, ALPN, and pseudo-header order are bundled into `wreq`'s emulation/profile API. We need a wreq-internal way to set these without `wreq_util::Emulation`. Investigation during design will confirm `wreq` exposes a `Profile` or similar lower-level type accepting raw bytes / structured config; if not, the registry directly constructs the same profile type.
3. **Alternative Consideration**: (a) Keep `wreq_util::Emulation`-shaped wrapper but rebuild it in-house — over-engineered, ties our API shape to upstream's. (b) Direct persona-to-wreq wiring — simpler, the persona IS the source of truth as the W3A schema intends. (c) Build a new abstraction layer — premature; the trait in `carbonyl_fingerprint::http` already provides the abstraction.
4. **Decision Rationale**: The persona schema was designed to be the source of truth. The `Emulation` enum was a stopgap because we didn't own the preset data. With UC-W01 closing that gap, the natural shape is direct wiring.
5. **Risk Assessment**: `wreq`'s public API may not expose every setter we need. Mitigation: design doc enumerates each persona field → setter mapping and flags unsupported fields as known divergences before construction begins. Iteration A's Chrome-only proof-of-concept de-risks this concretely.

## References

- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W01-vendor-preset-registry.md - Data registry this depends on
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md - Shift rationale
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md - Integration shape
- @crates/carbonyl-wreq/src/client.rs - Current integration (lines 100–161 build path)
- @.aiwg/architecture/adr-005-tls-fingerprint-http-client.md - Persona binding contract
