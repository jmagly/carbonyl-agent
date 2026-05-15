# Track Ready Brief — wreq-util Replacement

**Track**: wreq-util-replacement
**Status**: Documentation set complete; awaiting maintainer review before construction
**Date**: 2026-05-15
**Tracking issue**: roctinam/carbonyl-agent#99 (blocks #88)
**Parent**: W3B Phase 2 (#44)

## Track Status

The SDLC docset for the wreq-util replacement track is ready for review. All requirements, architecture decisions, design sketches, test strategy, and iteration planning are in place. No production code has been written. The track is ready to enter construction (Iteration A) pending maintainer sign-off on ADR-W01, ADR-W02, and ADR-W03.

### Artifacts produced

**Requirements** (`requirements/`):

- `use-cases/UC-W01-vendor-preset-registry.md` — In-house, MIT-licensed registry of browser emulation preset data for the five canonical persona families.
- `use-cases/UC-W02-persona-to-wreq-direct.md` — Direct persona-to-wreq wiring; deletes `persona_to_emulation` and the `wreq_util::Emulation` indirection.
- `use-cases/UC-W03-wire-conformance-coverage.md` — Layer 1 + Layer 2 wire-conformance against all five canonical families with documented divergence tolerance.
- `nfr.md` — Seven NFRs covering license compliance, wire fidelity, performance, maintainability, provenance, unsafe-code policy, and migration backward compatibility.

**Architecture** (`architecture/`):

- `adr-001-preset-registry-design.md` — Decision: hand-written typed Rust `const PresetTable` constants over embedded TOML or `build.rs` codegen; revisit at ~30 entries.
- `adr-002-persona-as-source-of-truth.md` — Decision: invert the model from closest-preset-then-override to persona-first, preset-backstop. Aligns with ADR-005's framing.
- `adr-003-wire-conformance-coverage-strategy.md` — Decision: snapshot tests against captured fixtures with JA4 cross-validation; rejects live differential testing (Safari uncontainerizable, mobile profiles complex, offline-CI constraint).
- `design-preset-registry.md` — Concrete Rust types (`PresetTable`, `TlsProfile`, `H2Profile`, `HeaderProfile`), module layout, `preset_for` lookup with family-scoped nearest-neighbor fallback, post-replacement `WreqClient::build` shape, and the Iteration A investigation gate on `wreq`'s low-level TLS API.

**Testing** (`testing/`):

- `test-plan.md` — Six-layer pyramid (L0 unit → L5 manual capture), per-layer test inventory, tolerance handling via `divergences.toml`, entry/exit criteria, test-specific risk register.
- `fixtures-plan.md` — HITL fixture-capture procedure with per-platform notes (Linux Chrome reference + variations for Android, macOS Safari, iOS Safari, Firefox), legal-posture argument, refresh policy.

**Planning** (`planning/`):

- `iteration-plan.md` — Three iterations: A (Chrome desktop proof + `wreq` API investigation), B (five-family rollout + `wreq-util` removal), C (license audit cleanup + new-preset runbook). Per-iteration scope items, quality gates, and parallelism map.
- `migration-strategy.md` — Feature-flag lifecycle (`carbonyl-wreq/preset-registry` exists for one minor version), dual-CI matrix during transition, per-iteration rollback procedures, downstream-communication plan.

**Reports** (`reports/`):

- `track-ready-brief.md` — This document.

## Decision log

The three ADRs in summary:

| ADR | Decision | Rationale |
|-----|----------|-----------|
| W01 | Typed Rust constants for preset data | Five entries today; compile-time correctness > runtime flexibility at this scale; revisit at ~30 |
| W02 | Persona is source of truth; presets are data backstops | Aligns with ADR-005; collapses two-step model into one; conformance suite proves equivalence |
| W03 | Snapshot tests against captured fixtures, with JA4 cross-validation | Only path that satisfies offline-CI + reproducibility + cross-browser coverage |

Each ADR has a "Consequences — Negative" section documenting the trade-offs (verbosity for W01; reliance on persona completeness for W02; staleness risk for W03 fixtures).

## Iteration plan summary

| Iteration | Scope | Quality gate |
|-----------|-------|--------------|
| A | Chrome 147 desktop preset; `wreq` API investigation; off-by-default feature flag; L2 responder scaffold; one fixture | Both old and new paths green in CI; Chrome L2 conformance passes |
| B | Four remaining family captures; complete registry; flag flipped ON; `wreq_util::Emulation` deleted; `wreq-util` removed from `Cargo.toml` | All five families green; `cargo about` clean on full workspace; no `wreq_util` references in source |
| C | Full-workspace license script; regenerated `THIRD_PARTY_LICENSES.txt`; wheel verification; adding-a-preset runbook; close #99 | Wheel contains updated license file; runbook is one-hour-actionable; #99 closed; #88 notified |

## Open items / known risks warranting maintainer review

1. **`wreq` API shape unknown** — The single biggest construction risk is whether `wreq` 5.x exposes a low-level TLS profile API or whether we have to either submit an upstream PR or reach into `wreq` internals. Iteration A item 1 is the investigation. If the investigation surfaces surprises, an ADR-W02 addendum is required before continuing. The design doc flags this explicitly.

2. **Persona completeness audit needed** — ADR-W02 assumes the five canonical personas in `carbonyl-fingerprint` declare every wire field with sufficient fidelity that the preset backstop is only filling in family/version/platform-derived defaults, not real wire bytes the persona was supposed to own. Before flipping the inversion in Iteration A, the personas should be audited. If gaps are found, they need to be filled in the persona before the code change, not after. This is a recommended pre-Iteration-A check; not strictly blocking but cheaper to do now than to discover during conformance testing.

3. **Safari iOS fixture capture access** — `fixtures-plan.md` flags this as an open question. We need access to either an iOS device on the target iOS version or an iOS simulator running on Apple silicon. Without it, the `safari-26-ios` fixture cannot be captured and Iteration B's five-family conformance gate can't close. Recommend confirming hardware/simulator access before Iteration B starts; if neither is available, scope-reduce to four families and document Safari iOS as a follow-up.

4. **`fixtures/PROVENANCE.md` not yet populated** — The provenance file is referenced extensively but doesn't exist yet. It gets populated in Iteration A as the first fixture is captured. Worth confirming with the maintainer that the provenance schema (`fixtures-plan.md` §3.2 step 12) is the desired shape before the first capture.

5. **`divergences.toml` close-by enforcement** — ADR-W03 requires every tolerated divergence to have an issue link and a close date. The CI gate enforcing the close date is described but not yet wired. This is part of Iteration A's CI work (item 6 of Iteration A scope).

6. **Conflict with the parent `wreq-replacement.md` runbook** — The existing runbook at `.aiwg/architecture/runbooks/wreq-replacement.md` covers replacing `wreq` itself (the whole crate). This track replaces `wreq-util` (the preset data crate). The two are independent — `wreq` stays (Apache-2.0/MIT, fine); `wreq-util` goes. Worth a brief note in the parent runbook clarifying the two-tier replacement model so future readers do not confuse them.

7. **Performance baseline not captured** — NFR-W-03 demands a performance budget but the baseline benchmark is not in the repo today. Iteration A should capture a pre-change baseline as part of item 6 (CI workflow update) so Iteration B has a comparison point.

## Definition of done for the track

Per the intake form's seven success criteria, plus the artifacts in this docset:

1. `cargo build --workspace` succeeds with `wreq-util` removed from `Cargo.toml` ✓ — Iteration B quality gate
2. Layer 1 and Layer 2 wire-conformance tests pass against the new path ✓ — Iteration B quality gate
3. `scripts/gen-third-party-licenses.sh` succeeds on the full workspace ✓ — Iteration C item 1
4. `THIRD_PARTY_LICENSES.txt` contains only permissively-licensed crates ✓ — Iteration C item 2
5. Built wheel carries the updated license file ✓ — Iteration C item 3
6. No regression in persona → JA4/h2/header mapping for the five canonical families ✓ — Iteration B L1+L2 conformance
7. #99 closes; #88 unblocks ✓ — Iteration C item 5

The new-preset runbook (NFR-W-04) — not in the original intake's seven criteria, added during planning — is Iteration C item 4 and closes out the maintainability NFR.

## Pre-construction checklist (for maintainer)

Before starting Iteration A:

- [ ] Review and accept ADR-W01, ADR-W02, ADR-W03 (status: Proposed → Accepted)
- [ ] Confirm `wreq` 5.x API research can start (no blocker to spending the first iteration's time on it)
- [ ] Confirm fixture-capture environment for Chrome 147 desktop is available
- [ ] Confirm Safari iOS capture access for Iteration B (or scope-reduce explicitly)
- [ ] Decide on the persona-completeness audit (do now, or accept the risk of finding gaps mid-Iteration A)
- [ ] Open a tracking issue for this docset (or use #99) and link the artifacts

Once the checkboxes clear, Iteration A is ready to start. The first construction PR will be the `wreq` API investigation addendum + the empty `presets/` module scaffold.

## References

All artifacts in this docset are under `.aiwg/tracks/wreq-util-replacement/`. Cross-references to repository code use `@<path>` mentions per the project's mention-wiring rule. Cross-references to other AIWG artifacts (notably `@.aiwg/architecture/adr-005-tls-fingerprint-http-client.md` and `@crates/carbonyl-wreq/src/client.rs`) are embedded inline throughout.

- Parent SAD: `@.aiwg/architecture/software-architecture-doc.md`
- Parent decision: `@.aiwg/architecture/adr-005-tls-fingerprint-http-client.md`
- Parent runbook (different scope): `@.aiwg/architecture/runbooks/wreq-replacement.md`
- Tracking issue: roctinam/carbonyl-agent#99
- Blocked: roctinam/carbonyl-agent#88
- Track intake: `@.aiwg/tracks/wreq-util-replacement/intake/intake-form.md`
