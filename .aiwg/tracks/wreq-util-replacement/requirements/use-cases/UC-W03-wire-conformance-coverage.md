# UC-W03: Extended Wire-Conformance Coverage

**Track**: wreq-util-replacement
**Status**: Draft
**Priority**: High (gates merge of UC-W02)
**Date**: 2026-05-15
**Refs**: roctinam/carbonyl-agent#99, parent W3B Phase 2 #44

## Primary Actor

CI pipeline + `carbonyl-fingerprint::conformance` test crate.

## Goal

Extend the existing two-layer wire conformance test infrastructure (Layer 1: structural `ApplyInspector` assertions; Layer 2: captured-output comparison) to cover all five canonical persona families against the new in-house preset implementation, with documented tolerance for known divergences vs. the reference browser captures.

## Preconditions

- UC-W01 registry compiles.
- UC-W02 direct wiring functions for at least Chrome 147 desktop (Iteration A scope).
- Reference fixtures captured per `fixtures-plan.md` — at minimum the five canonical families.
- The conformance crate's `ApplyInspector` and `ConformanceFixture` types remain compatible with the new `WreqClient` shape (no `emulation` field).

## Main Success Scenario

1. `tests/conformance_layer1.rs` runs the existing structural assertions (JA4, ALPN, h2 SETTINGS, headers) against each of the five fixtures using the new direct-wired `WreqClient`.
2. `tests/conformance_layer2.rs` (new or extended) issues a probe request through `wreq::Client` to a localhost responder that captures the raw ClientHello + h2 SETTINGS frame bytes.
3. Layer 2 compares captured bytes against the reference fixture from `fixtures/`, asserting equality modulo a documented tolerance set (TLS GREASE values, ephemeral random fields, TLS session ticket nonce).
4. CI runs both layers on every PR touching `crates/carbonyl-wreq/` or `crates/carbonyl-fingerprint/`.
5. A summary report at `target/conformance-report.md` enumerates per-family pass/fail and any divergence fields.

## Alternative Flows

**A1: A known divergence is acceptable**
- The divergence is added to `divergences.toml` under the relevant fixture with a justification, an issue link, and a target close date.
- Layer 2 reads `divergences.toml` and treats listed fields as soft-pass.

**A2: A new browser version arrives and the persona schema extends**
- A new fixture is added under `fixtures/`.
- A new conformance entry is added to `tests/conformance_layer2.rs`.
- The registry (UC-W01) is extended; the test enforces structural and wire-level equivalence.

## Exception Flows

**E1: Layer 2 capture infrastructure fails (port bind, TLS responder crash)**
- CI marks the run as infrastructure-error rather than test-fail.
- The job retries up to twice; persistent failure escalates to a manual issue.

**E2: Wire output diverges from fixture in a field not on the tolerance list**
- Test fails, blocking merge.
- Author must either fix the registry/persona mapping or justify and add to `divergences.toml` with reviewer approval.

## Postconditions

- All five canonical families pass Layer 1 + Layer 2 conformance against the new implementation.
- Tolerance set documented in `divergences.toml`.
- CI gate `rust-crates::conformance` is green on the closing PR for #99.

## Acceptance Criteria

- [ ] `cargo test --workspace --features carbonyl-wreq/python -- --include-ignored conformance` runs to completion.
- [ ] Per-family pass for: Chrome 147 desktop, Chrome 147 mobile (Android), Firefox 150 desktop, Safari 26 desktop (macOS), Safari 26 mobile (iOS).
- [ ] `divergences.toml` exists and is referenced from the test crate's README.
- [ ] CI workflow (`.gitea/workflows/conformance.yml` or equivalent) invokes both layers on PR.
- [ ] Layer 2 capture uses a local TLS responder that does not require network egress — the test must be runnable on an offline CI runner.

## Non-Functional Requirements

- See `@.aiwg/tracks/wreq-util-replacement/requirements/nfr.md` §wire-fidelity.

## Reasoning

1. **Problem Analysis**: Without wire-level conformance, we can't tell the difference between "preset table is correct" and "preset table compiles". The whole point of the replacement is wire-fidelity vs. real browsers; conformance is the only signal that proves it.
2. **Constraint Identification**: Must run offline (CI runner does not have outbound network in our setup). Must tolerate known noise (GREASE, random nonces) without becoming a rubber stamp. Must scale to five families today and N families later.
3. **Alternative Consideration**: (a) Live differential testing against real Chrome via Selenium — flaky, slow, network-dependent, rejected. (b) Captured fixtures + local TLS responder — reproducible, offline, selected. (c) JA4-only assertion — too coarse, misses h2 SETTINGS divergence.
4. **Decision Rationale**: Captured fixtures are the only path to reproducible wire-conformance in CI. The tolerance set (`divergences.toml`) is the escape valve for legitimate divergence (GREASE rotation, ephemeral nonces) and the bug-tracking surface for illegitimate divergence (a real persona-to-wire mismatch).
5. **Risk Assessment**: Fixtures captured incorrectly will silently pass against the wrong reference. Mitigation: each fixture's provenance documented (browser version, capture date, capture method, JA4 cross-check against an external database). Fixture creation is a HITL gate, not an autonomous step.

## References

- @.aiwg/tracks/wreq-util-replacement/testing/test-plan.md - Test strategy across layers
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md - Reference capture procedure
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-003-wire-conformance-coverage-strategy.md - Approach decision
- @crates/carbonyl-fingerprint/src/conformance.rs - Existing ApplyInspector trait
- @crates/carbonyl-wreq/src/client.rs - Tests at lines 322–413 (current Layer 1 invocations)
