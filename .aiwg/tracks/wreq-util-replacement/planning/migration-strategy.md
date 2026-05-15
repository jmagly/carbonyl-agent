# Migration Strategy — wreq-util → In-House Preset Registry

**Status**: Draft
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Refs**: roctinam/carbonyl-agent#99, NFR-W-07

This document describes the incremental migration path from the current `wreq_util::Emulation`-mediated path to the persona-first, preset-backstop path. It defines the feature-flag lifecycle, the rollback procedure, and the per-iteration switch state.

## 1. Feature-flag lifecycle

A Cargo feature `carbonyl-wreq/preset-registry` gates the new path. The flag exists for one minor version only — long enough to validate the new path in CI alongside the old, short enough that it does not become a permanent fork.

| Iteration | Feature state | Default | Behavior |
|-----------|--------------|---------|----------|
| Pre-A | Feature absent | n/a | Only old path (`wreq_util::Emulation`) |
| A | Feature added | OFF | Both paths compile; both pass tests; CI runs both |
| B | Feature flipped to ON; old path deleted | ON | Only new path remains; old call sites removed |
| C | Feature removed | n/a | Single path; flag deletion is a no-op cleanup |

The flag is therefore a brief transition device, not a permanent compatibility shim. Its purpose is to allow Iteration A to land independently of Iteration B without committing to the design before the proof of concept passes.

### 1.1 Cargo.toml shape during Iteration A

```toml
# crates/carbonyl-wreq/Cargo.toml
[features]
default = []
preset-registry = []  # Iteration A path; opt-in
python = ["dep:pyo3"]
```

### 1.2 Conditional compilation shape

```rust
// crates/carbonyl-wreq/src/client.rs (during Iteration A only)

impl WreqClient {
    pub fn build(self) -> Result<wreq::Client, WreqError> {
        #[cfg(feature = "preset-registry")]
        return self.build_via_preset_registry();

        #[cfg(not(feature = "preset-registry"))]
        return self.build_via_wreq_util_emulation();
    }
}
```

Two `build_via_*` methods exist side by side. Both call paths are exercised in CI. When Iteration B begins, the `#[cfg(not(...))]` branch and the `build_via_wreq_util_emulation` method are deleted; the flag becomes vacuous and is removed in Iteration C.

## 2. CI matrix during the transition

During Iteration A and into the start of Iteration B, CI runs the test suite twice per PR:

```yaml
# .gitea/workflows/test.yml (sketch)
jobs:
  test-legacy:
    name: Test (wreq-util path, default)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --workspace --features carbonyl-wreq/python

  test-new:
    name: Test (preset-registry path)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --workspace --features carbonyl-wreq/preset-registry,carbonyl-wreq/python
```

Both jobs must pass before a PR merges to main. The dual-job CI is what makes the rollback procedure (§3) cheap: at any point during Iteration A or early Iteration B, reverting the feature default to OFF restores known-good behavior.

When Iteration B reaches its quality gate (all five families green on the new path, `wreq-util` removable), the legacy job is deleted in the same PR that removes `wreq-util` from `Cargo.toml`.

## 3. Rollback procedure

### 3.1 During Iteration A (new path is opt-in)

A regression on the new path does not affect production callers — the feature is OFF by default. Rollback is "revert the PR that introduced the regression" with no urgency. No coordination required with downstream consumers.

### 3.2 During Iteration B (transition window)

Once the feature default flips to ON and the old `wreq_util::Emulation` path is deleted, a regression requires a real rollback. Procedure:

1. **Identify**: a wire-conformance test fails on a fixture that previously passed, OR a downstream consumer reports a fingerprint mismatch in production.
2. **Stop the bleed**: tag the last good commit as `last-good-wreq-util-removal-N`. If a wheel has shipped with the regression, yank it from the index (per the standard release runbook).
3. **Revert**: `git revert <commit>` of the Iteration B PR that deleted the legacy path. This brings back `persona_to_emulation`, `PendingConfig::emulation`, and the `wreq-util` dependency.
4. **Reinstate dual-CI**: confirm both jobs pass post-revert.
5. **Investigate**: open an issue describing the regression with a Layer 2 diff (which field diverged, which fixture, which persona). The investigation may surface a registry bug, a `wreq` API misuse, or a fixture issue.
6. **Re-attempt**: once the regression is understood and fixed, re-do the Iteration B closing PR.

Time budget for steps 1–4: same day. Investigation timeline: depends on root cause.

### 3.3 Post-Iteration C (cleanup complete)

After Iteration C closes, there is no rollback to `wreq-util` short of re-vendoring it. The only legitimate "rollback" path post-C is a forward fix.

This is intentional: the whole point of the track is to remove the GPL dependency. A graceful rollback to a GPL dependency is not a real option for distribution; it is a development-only escape hatch that ceases to be relevant once the track lands.

## 4. Backward compatibility considerations

### 4.1 Public API surface

The `carbonyl-wreq` crate's public API does not change:

- `WreqClient::new()` — unchanged
- `WreqClient::apply_persona_typed(&Persona)` — unchanged signature
- `WreqClient::build() -> Result<wreq::Client, WreqError>` — unchanged signature
- The `HttpClient` and `ApplyInspector` trait implementations — unchanged

The only public-shape change is the deletion of `PendingConfig::emulation` and `persona_to_emulation`. Both are eligible for deletion because:

- `PendingConfig::emulation` is only meaningful when `wreq_util` is on the dep graph; with `wreq-util` removed, the field has no type.
- `persona_to_emulation` is a free function exported from `client.rs`. Searching the workspace (and the W3B Phase 2 dependent crates) for `persona_to_emulation` callers should return zero results outside this crate's own tests; if any callers exist, they are pre-W3B-Phase-2.5 dead code.

Document the deletions in `CHANGELOG.md` at Iteration C's close as a breaking change for any consumer that imported them directly.

### 4.2 Persona schema

No changes. The persona schema is intentionally unchanged by this track — the entire point of the inversion (ADR-W02) is that the persona was already the right source of truth; we're rearranging how the code consumes it.

### 4.3 Behavior at the wire layer

Pre-track: the closest-preset path produced wire output close to (but not exactly matching) the persona's declared shape. The "Chrome 137 stands in for Chrome 147" approximation introduced known divergence captured in #82.

Post-track: the persona-first path produces wire output matching the persona's declared shape exactly, modulo the preset backstop for fields the persona does not declare. The wire output is expected to be CLOSER to the persona spec than the legacy path was — not strictly identical to the legacy path.

This is a feature, not a regression. But it means the L2 conformance baseline must be re-established at Iteration A: the fixture is the real-browser capture, not the legacy path's output. If a downstream consumer was relying on the legacy path's specific output bytes, that reliance was always fragile (the persona could change). The conformance suite documents the new contract.

## 5. Communication plan

| Audience | When | Channel | Message |
|----------|------|---------|---------|
| #99 watchers | Iteration A starts | Issue comment | Track started; PR for Iteration A linked |
| #88 watchers | Iteration C closes | Issue comment | `wreq-util` removed; license blocker cleared; #88 unblocked |
| Downstream consumers (W3B integrators) | Iteration B PR opens | Mention in PR body + an issue tagged `breaking-change` if anyone imported `persona_to_emulation` | Notify of upcoming deletions |
| Sole maintainer (self) | Each iteration's quality gate | n/a | Review checklist against this strategy doc before merging |

## 6. Risk vs. mitigation summary

| Risk | When it manifests | Mitigation |
|------|-------------------|------------|
| New path passes structural tests but emits subtly different bytes than the legacy path produced | Iteration A | Side-by-side L2 conformance runs (both paths against the same fixture) catch divergence before flag flip |
| `wreq` API limitations force unsafe code or upstream PR | Iteration A item 1 | Investigation is first; if blocked, gate-stop and re-evaluate ADR-W02 |
| Downstream consumer breaks on deletion of `persona_to_emulation` | Iteration B item 7 | Workspace grep before deletion; advance-notice issue if any callers found |
| Wheel build picks up stale license file | Iteration C | Inspect built wheel before tagging; CI gate on `THIRD_PARTY_LICENSES.txt` presence in wheel zip |
| Iteration B drags on; flag becomes permanent | Throughout B | Time-boxed: if flag exists at end of Iteration C planning, halt feature work and finish removal first |

## References

- @.aiwg/tracks/wreq-util-replacement/planning/iteration-plan.md
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md
- @.aiwg/tracks/wreq-util-replacement/requirements/nfr.md §NFR-W-07
- @.aiwg/architecture/runbooks/wreq-replacement.md - Parent escape-hatch SOP (different scope: `wreq` itself)
- @crates/carbonyl-wreq/Cargo.toml
- @scripts/gen-third-party-licenses.sh
