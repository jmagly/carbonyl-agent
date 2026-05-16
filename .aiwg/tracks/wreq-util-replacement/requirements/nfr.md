# Non-Functional Requirements — wreq-util Replacement Track

**Track**: wreq-util-replacement
**Status**: Draft
**Date**: 2026-05-15
**Refs**: roctinam/carbonyl-agent#99

This document declares the cross-cutting non-functional constraints that govern UC-W01, UC-W02, and UC-W03.

## NFR-W-01: License Compliance (CRITICAL)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-01 |
| Category | Compliance / Supply chain |
| Priority | Critical — gates wheel distribution |
| Verification | `cargo about generate --config about.toml --output-file THIRD_PARTY_LICENSES.txt` succeeds on the full workspace and the output contains zero copyleft licenses |

**Requirement**: Every direct and transitive dependency in `Cargo.lock` after the replacement must carry a license compatible with MIT distribution: `MIT`, `Apache-2.0`, `MIT OR Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `Zlib`, `Unicode-DFS-2016`, or `0BSD`.

**Forbidden licenses**: `GPL-*`, `AGPL-*`, `LGPL-*` (except via dynamic linkage with documented exception), `MPL-*` unless an explicit waiver is filed, `SSPL-*`, anything `CC-BY-NC-*` or `CC-BY-SA-*`.

**Verification gate**:

```bash
cargo about generate --config about.toml --output-file THIRD_PARTY_LICENSES.txt
# Must succeed on the full workspace, not just carbonyl-fingerprint
grep -iE '(GPL|AGPL|LGPL|MPL|SSPL|CC-BY-NC|CC-BY-SA)' THIRD_PARTY_LICENSES.txt && exit 1 || true
```

**Rationale**: The track's existence is justified by this NFR. `wreq-util` 2.x is GPL-3.0; statically linking it forces the carbonyl-agent wheel to GPL-3.0, breaking downstream MIT consumers and blocking pip distribution (#88).

## NFR-W-02: Wire Fidelity (HIGH)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-02 |
| Category | Behavioral correctness |
| Priority | High |
| Verification | Layer 1 + Layer 2 conformance suite per UC-W03 |

**Requirement**: For each of the five canonical persona families, the wire output of the new implementation must match the reference fixture in the following fields:

| Field | Tolerance |
|-------|-----------|
| TLS version | Exact match |
| Cipher suite list (order included) | Exact match |
| ClientHello extension list (order included) | Exact match |
| ALPN offer (list and order) | Exact match |
| GREASE values | Tolerance: any valid GREASE value at the documented positions |
| TLS random | Tolerance: ignored (always ephemeral) |
| Session ticket / SNI | Tolerance: ignored when blank |
| h2 SETTINGS frame entries | Exact match by (id, value) tuple set; order matches persona's `http2_akamai` declaration |
| h2 WINDOW_UPDATE initial | Exact match |
| h2 pseudo-header order | Exact match |
| Default request header order | Exact match for the first request from a fresh client |

**Documented divergences** (tolerated, tracked): listed in `tests/conformance/divergences.toml` per UC-W03.

## NFR-W-03: Performance Budget (MEDIUM)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-03 |
| Category | Performance |
| Priority | Medium |
| Verification | Criterion benchmark in `crates/carbonyl-wreq/benches/persona_to_client.rs` |

**Requirement**: `WreqClient::apply_persona_typed` + `WreqClient::build` must complete in under 5 ms (p99) on the reference CI runner for the Chrome 147 desktop fixture. Today's `wreq_util::Emulation`-mediated path completes in approximately 2–4 ms; the replacement must not regress this measurably.

**Verification gate**:

```bash
cargo bench --bench persona_to_client -- --save-baseline new
# Compared against a baseline captured pre-replacement
critcmp old new  # No measurement > 1.10x of baseline p99
```

## NFR-W-04: Maintainability — Adding New Browser Versions (MEDIUM)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-04 |
| Category | Maintainability |
| Priority | Medium |
| Verification | Documented procedure in `runbooks/adding-a-preset.md` (created in Iteration C) |

**Requirement**: Adding a new browser version preset (e.g., when Chrome 148 ships) must be a procedure of:

1. Capture reference fixture per `fixtures-plan.md` (one-time HITL step).
2. Add one entry to `carbonyl_wreq::presets` registry.
3. Add one conformance test entry referencing the new fixture.
4. Run `cargo test --workspace` and verify pass.

The procedure must be executable by a single maintainer in under one hour assuming the fixture is already captured. No edits to `client.rs` or to `carbonyl-fingerprint` should be required for a routine version bump.

## NFR-W-05: Provenance Documentation (HIGH)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-05 |
| Category | Audit / Legal posture |
| Priority | High |
| Verification | `fixtures/PROVENANCE.md` lists every preset and its capture origin |

**Requirement**: Every preset entry must reference a captured fixture, and every fixture must document:

- Browser product, version, and platform identifier
- Capture date (UTC, ISO 8601)
- Capture method (e.g., `tshark -i lo`, mitmproxy capture, real-network PCAP)
- Cross-reference to at least one external JA4 database entry (FoxIO JA4 corpus, or equivalent) confirming the JA4 hash matches the captured ClientHello
- Capture-tool versions
- Hash of the fixture file (SHA-256)

**Rationale**: The legal posture relies on these fixtures being captured from public observation of real browsers, not derived from `wreq-util`. Provenance is what makes that claim auditable.

## NFR-W-06: No Unsafe Code in Preset Registry (MEDIUM)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-06 |
| Category | Security / Code quality |
| Priority | Medium |
| Verification | `#![forbid(unsafe_code)]` at the top of `crates/carbonyl-wreq/src/presets/mod.rs` |

**Requirement**: The preset registry module is pure data and lookup logic. No `unsafe` blocks. No FFI. No `transmute`. Compiles under `forbid(unsafe_code)`.

## NFR-W-07: Iteration-Boundary Rollback (HIGH)

| Attribute | Specification |
|-----------|---------------|
| ID | NFR-W-07 |
| Category | Migration / Rollback |
| Priority | High |
| Verification | Each iteration merges as a discrete reverting-capable commit; `cargo test --workspace` green at every iteration boundary |

**Requirement** (revised per track decision, 2026-05-15): Rollback safety lives at the **iteration boundary**, not behind a runtime feature flag. Each iteration is its own PR. Each iteration leaves `main` in a buildable, test-green state. If a wire-conformance regression is detected post-merge of any iteration, the rollback procedure is a `git revert` of the iteration's merge commit. The original SDLC docset proposed a `carbonyl-wreq/preset-registry` feature flag for in-binary rollback — that was dropped in favor of git-level rollback after the iteration plan was reviewed (single-PR-per-iteration is sufficient when iterations are small and the legacy path is removed at Iteration B rather than Iteration C).

See `@.aiwg/tracks/wreq-util-replacement/planning/migration-strategy.md` for the simplified rollback procedure.

## Reasoning

1. **Problem Analysis**: The replacement track has both legal hard requirements (license) and behavioral soft requirements (wire fidelity). Both must be measurable.
2. **Constraint Identification**: License compliance is a binary gate — either the workspace builds with permissive licenses only, or distribution is blocked. Wire fidelity is a tolerance-based gate — exact byte-for-byte equality is unachievable (GREASE, randoms) but the structural shape must hold.
3. **Alternative Consideration**: NFR for wire fidelity could be "byte-for-byte exact match" — but that fails on first run due to GREASE. Or "JA4 hash match only" — but that misses h2-level divergence. Field-level tolerance is the middle path.
4. **Decision Rationale**: Each NFR has a concrete verification command, runnable in CI. None of them are aspirational.
5. **Risk Assessment**: NFR-W-04 (maintainability) is the lever that determines whether the in-house registry is sustainable. If adding Chrome 148 takes a week, the project will fall behind upstream over time. Iteration C's runbook is the mitigation.

## References

- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W01-vendor-preset-registry.md
- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W02-persona-to-wreq-direct.md
- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W03-wire-conformance-coverage.md
- @.aiwg/tracks/wreq-util-replacement/planning/migration-strategy.md
- @scripts/gen-third-party-licenses.sh - License audit script
