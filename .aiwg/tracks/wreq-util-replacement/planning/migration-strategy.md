# Migration Strategy — wreq-util Replacement

**Status**: Revised 2026-05-15 — feature-flag mechanism dropped; rollback at iteration-boundary git granularity
**Track**: wreq-util-replacement
**Refs**: roctinam/carbonyl-agent#99, blocks #88

## Goal

Move `carbonyl-wreq` from `wreq_util::Emulation`-driven configuration to direct persona-driven configuration via an in-house typed preset registry, without breaking the workspace at any merge boundary.

## Rollback model

Rollback lives at the **git commit granularity** of each iteration's merge commit, not behind a runtime feature flag. The original docset proposed an `carbonyl-wreq/preset-registry` Cargo feature for in-binary rollback. That was dropped after track review for the following reasons:

- Iterations are individually small (6, 8, 5 atomic items respectively).
- The legacy `wreq_util::Emulation` path is preserved through Iteration A and only deleted in Iteration B — the dual-path window is on `main` for one PR cycle, not multiple releases.
- `git revert <merge-commit>` produces a buildable, test-green state at every iteration boundary by construction (because each iteration's quality gate requires that).
- A Cargo feature adds permanent surface area for a temporary problem.

## Per-iteration migration mechanics

### Iteration A — add the new path, don't ship it

- New module `crates/carbonyl-wreq/src/presets/` is introduced.
- Production `WreqClient::build()` continues to route through `wreq_util::Emulation` — no behavior change in shipped code.
- The new path is reachable only via a test-only helper (e.g. `WreqClient::build_via_registry()` gated by `#[cfg(test)]` or a `pub(crate)` helper used only from `tests/`).
- L2 wire-conformance test exercises the new path against the captured Chrome 147 desktop fixture.
- **Rollback**: `git revert` of the merge commit. Removes the new module + test. No production cutover happened, so no production regression possible.

### Iteration B — cutover and cleanup

- Production `WreqClient::build()` switches to the new registry path. Test-only helper from Iteration A is deleted.
- All five family presets present.
- All five conformance tests passing.
- `wreq_util::Emulation` use sites deleted from `client.rs`.
- `wreq-util` removed from `Cargo.toml`.
- **Rollback**: `git revert` of the merge commit. Restores both `wreq-util` and the legacy path. The Iteration A artifacts (presets module, conformance scaffolding) remain — they're harmless when the legacy path is restored.

### Iteration C — license closeout

- `scripts/gen-third-party-licenses.sh` updated to scan the full workspace.
- Regenerated `THIRD_PARTY_LICENSES.txt` committed.
- Wheel built and inspected for the new license file.
- `.aiwg/architecture/runbooks/adding-a-preset.md` authored.
- #99 closed.
- **Rollback**: `git revert` restores the per-crate scoped script. Wheel reverts to the prior license file. No code regression — Iteration B already shipped the wreq-util removal.

## Pre-iteration gate

A persona-completeness audit (filed as a separate issue under EPIC #100) must complete before Iteration A. The audit verifies that the persona schema actually carries all the data the in-house presets need (JA4, h2 SETTINGS, ALPN, header order). If it doesn't, ADR-W02 needs revision and the design doc needs updating before construction.

The audit is not part of any iteration — it's a one-shot pre-track verification that produces a written report under `.aiwg/tracks/wreq-util-replacement/reports/persona-audit-report.md`.

## Risk mitigations

### Risk: Iteration A merges, then a real-browser update changes the Chrome 147 fingerprint

If Chrome 147 stops emitting the captured fingerprint between Iteration A merge and Iteration B cutover, the new path stays correct (it matches the captured snapshot) but real-world traffic may drift. Mitigation: the snapshot remains the contract — we re-capture and update the fixture in a separate PR, not as part of the rollout. JA4 cross-validation in CI catches the drift.

### Risk: Downstream consumers import wreq_util types via re-export

If `carbonyl-wreq` (or its Python binding) re-exports `wreq_util::Emulation` types, Iteration B's deletion breaks consumers. Mitigation: before deleting in Iteration B item 7, run `cargo build --features carbonyl-wreq/python` and `grep -r wreq_util` against any known consumers. If found, the re-export is itself a bug (we shouldn't be exposing third-party crate types) — file as a precursor and fix before continuing.

### Risk: Wire conformance test passes for new path but breaks against real browser

The captured fixture is a single point-in-time observation. If our preset doesn't capture some context-dependent behavior (e.g., browser changes ClientHello based on prior server response), the test passes but production fails. Mitigation: JA4 cross-validation against a public database (ja4db or equivalent) at capture time. If our captured JA4 doesn't match the published JA4 for the same browser version, the capture is suspect.

## CI policy

For the duration of Iteration A (when the dual-path window is on `main`), CI runs:

- `cargo test --workspace` — exercises both paths
- `cargo test -p carbonyl-wreq --test conformance_layer2` — the new path's specific conformance test
- `cargo build --features carbonyl-wreq/python` — the Python binding build (catches re-export issues early)

Once Iteration B merges, the `--features` build matrix simplifies (legacy path is gone).

## Definition of done for this strategy

- All three iterations merge cleanly to `main`.
- Each iteration's merge commit is independently revertable on the commit graph (no merges-of-merges, no rebases that bury the iteration boundary).
- The wreq-replacement runbook is published before Iteration C closes.

## References

- @.aiwg/tracks/wreq-util-replacement/planning/iteration-plan.md
- @.aiwg/tracks/wreq-util-replacement/requirements/nfr.md (NFR-W-07, revised)
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md
- @.aiwg/tracks/wreq-util-replacement/testing/test-plan.md
