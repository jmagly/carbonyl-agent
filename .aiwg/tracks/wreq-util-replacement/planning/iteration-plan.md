# Iteration Plan — wreq-util Replacement

**Status**: Draft
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Refs**: roctinam/carbonyl-agent#99, blocks #88

The track is split into three iterations. Each iteration is independently mergeable, leaves the workspace in a green state, and produces a measurable outcome. The break points are deliberately chosen so a rollback at any iteration boundary leaves the prior state intact.

## Iteration A — Chrome desktop proof of concept

**Scope units**: 6 atomic items
**Agent count**: 2 (implementer + reviewer)
**Estimated passes to quality gate**: 2–3

### Goals

- Confirm `wreq` 5.x API can be driven directly without `wreq_util::Emulation`. If yes, proceed; if no, the design doc gains an addendum and an upstream PR is filed before continuing.
- Vendor the Chrome 147 desktop preset under the new typed registry.
- Wire it behind an off-by-default feature flag `carbonyl-wreq/preset-registry`.
- Capture the Chrome 147 desktop fixture per `fixtures-plan.md`.
- Build out Layer 2 conformance scaffolding (responder, capture machinery) for one family.

### Scope items

1. Investigation: `wreq` 5.x exposes the lower-level TLS profile API we need — write an addendum to ADR-W02 documenting the actual shape.
2. Create `crates/carbonyl-wreq/src/presets/{mod.rs,chrome.rs}` per `design-preset-registry.md` with one entry: `CHROME_147_DESKTOP`.
3. Capture `chrome-147-desktop` fixture per `fixtures-plan.md` (HITL step).
4. Add the `preset-registry` feature flag to `Cargo.toml`. When enabled, `WreqClient::build` uses the new path; when disabled, the existing `wreq_util::Emulation` path.
5. Build the L2 responder (`tests/conformance/responder.rs`) and one wire-conformance test for Chrome 147 desktop.
6. CI workflow update: add a job that runs `cargo test -p carbonyl-wreq --features preset-registry --test conformance_layer2` against the new path.

### Quality gate

- `cargo build --workspace --features carbonyl-wreq/preset-registry` succeeds.
- `cargo test --workspace --features carbonyl-wreq/preset-registry` succeeds.
- `cargo test --workspace` (without the feature) still succeeds — old path intact.
- The Chrome 147 desktop L2 conformance test passes against the new path.
- The same test, run against the OLD path, also passes against the same fixture (sanity: the fixture isn't wrong).

### Out of scope for Iteration A

- Firefox / Safari presets (Iteration B).
- Removal of `wreq-util` (Iteration B → C).
- Persona-version drift handling beyond Chrome 147 (Iteration C).

### Decision-points / parallelism

Items 1–3 are independent and can run in parallel. Item 4 blocks on items 1 and 2. Item 5 blocks on item 4 and the fixture from item 3. Item 6 blocks on item 5.

Sequential gate: investigation outcome (item 1) determines whether item 2's design is mostly as drafted or requires significant rework. The investigation is the first pass.

## Iteration B — Five-family rollout and wreq-util removal

**Scope units**: 8 atomic items
**Agent count**: 2 (implementer + reviewer); parallelizable for items 1–5
**Estimated passes to quality gate**: 3–4

### Goals

- Extend the registry to all five canonical families.
- Capture the four remaining fixtures.
- Flip the feature flag default to ON.
- Remove `wreq_util::Emulation` references from `client.rs`.
- Remove `wreq-util` from `Cargo.toml`.

### Scope items

1. Capture `chrome-147-mobile`, `firefox-150-desktop`, `safari-26-macos`, `safari-26-ios` fixtures (HITL; four parallel items).
2. Add `presets/firefox.rs::FIREFOX_150_DESKTOP`.
3. Add `presets/safari.rs::{SAFARI_26_MACOS, SAFARI_26_IOS}`.
4. Add `presets/chrome.rs::CHROME_147_MOBILE_ANDROID`.
5. Extend L2 conformance with the remaining four fixtures.
6. Flip `preset-registry` feature default to ON in `Cargo.toml`.
7. Delete `persona_to_emulation`, `PendingConfig::emulation`, and `use wreq_util::*` from `client.rs`. Rewrite the module docstring (current lines 1–33) to describe the persona-first model.
8. Remove `wreq-util` from `Cargo.toml`. Confirm `cargo build --workspace` still succeeds.

### Quality gate

- `cargo build --workspace` succeeds with `wreq-util` absent from `Cargo.toml` and `Cargo.lock`.
- `cargo test --workspace --features carbonyl-wreq/python` succeeds.
- All five L1 + L2 conformance tests pass against the new path.
- `grep -r wreq_util crates/carbonyl-wreq/src/` returns zero results.
- License audit on the full workspace: `cargo about generate` succeeds; output contains no copyleft licenses.

### Decision-points / parallelism

Items 1, 2, 3, 4 are independent and parallel. Item 5 batches per fixture (parallel with item 1's individual captures). Items 6, 7, 8 are sequential after items 1–5.

Item 7 has a structural risk: if a downstream consumer of `carbonyl-wreq` (e.g., the Python binding) imports `wreq_util` types via re-export, the deletion will break them. Confirm `cargo build --features carbonyl-wreq/python` does not break before deleting.

## Iteration C — Conformance closeout, license-audit unblock, runbook

**Scope units**: 5 atomic items
**Agent count**: 2 (implementer + reviewer)
**Estimated passes to quality gate**: 1–2

### Goals

- Update `scripts/gen-third-party-licenses.sh` to scan the full workspace.
- Regenerate and commit `THIRD_PARTY_LICENSES.txt`.
- Confirm the wheel builds via `hatchling` and ships the new license file.
- Author the "adding a new preset" runbook (closes the NFR-W-04 verification).
- Close #99 and notify #88 that the blocker is cleared.

### Scope items

1. Edit `scripts/gen-third-party-licenses.sh` — remove the `-p carbonyl-fingerprint` scope restriction.
2. Run the script and commit `THIRD_PARTY_LICENSES.txt`.
3. Build the wheel via `python -m build` (or the project's standard invocation) and verify the new license file is bundled.
4. Author `.aiwg/architecture/runbooks/adding-a-preset.md` covering: capture fixture → add preset entry → add conformance test → run `cargo test`.
5. Close #99 with a summary linking the track's `track-ready-brief.md`; comment on #88 that the dependency is removed.

### Quality gate

- `scripts/gen-third-party-licenses.sh` runs without `-p` flags and produces a clean output.
- Built wheel inspected with `unzip -l <wheel>` shows `THIRD_PARTY_LICENSES.txt`.
- Runbook exists; a dry-run walkthrough on paper completes a hypothetical Chrome 148 add in under one hour.

### Parallelism

Items 1, 4 are independent. Items 2, 3 are sequential after 1. Item 5 is the final step.

## Summary table

| Iteration | Atomic items | Parallel batches | Sequential gates | Quality gate |
|-----------|--------------|------------------|------------------|--------------|
| A | 6 | 1, 2, 3 || 4 | 5 | 6 | 3 | wreq investigation outcome; Chrome L2 green |
| B | 8 | 1a, 1b, 1c, 1d, 2, 3, 4 || 5 || 6 | 7 | 8 | 3 | All 5 families green; `wreq-util` absent |
| C | 5 | 1, 4 || 2 | 3 | 5 | 2 | License file regenerated; wheel ships it; #99 closed |

## Risk profile by iteration

| Iteration | Top risk | Mitigation |
|-----------|----------|------------|
| A | `wreq` API doesn't expose what we need | Investigation is item 1; if blocked, file upstream PR or pivot design before continuing |
| B | Captured fixtures inconsistent with persona declarations | JA4 cross-check at capture time; conformance test catches drift |
| C | Wheel doesn't pick up the new license file | Inspect built wheel before tagging; CI gate on wheel contents |

## References

- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W01-vendor-preset-registry.md
- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W02-persona-to-wreq-direct.md
- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W03-wire-conformance-coverage.md
- @.aiwg/tracks/wreq-util-replacement/planning/migration-strategy.md
- @.aiwg/tracks/wreq-util-replacement/testing/test-plan.md
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md
