# carbonyl-fingerprint

Rust crate implementing the persona registry for the Carbonyl Trusted Automation Initiative.

**Spec**: `roctinam/carbonyl` → `.aiwg/working/trusted-automation/07-fingerprint-registry-design.md`
**Corpus**: `roctinam/carbonyl-fingerprint-corpus`

## Status

**Pre-alpha, scaffold only.** Schema module implemented and round-trip tested. Sampler, validator, applier, registry are placeholder modules awaiting Phase 3A implementation.

## Responsibilities

| Module | Phase | Status |
|--------|-------|--------|
| `schema` | 3A.1 | ✅ Scaffold — Persona struct with TOML round-trip test |
| `sampler` | 3A.2 | 📋 Placeholder |
| `validator` | 3A.3 | 📋 Placeholder |
| `applier` | 3C | 📋 Placeholder |
| `registry` | 3A.1 | 📋 Placeholder |

## Consumers

- `carbonyl_agent` Python SDK — via PyO3 bindings (planned)
- `carbonyl-agent-qa` — via direct Rust dependency for persona validation in CI

## Non-goals

- Network I/O (the refresh pipeline lives in `tools/` of the corpus repo, not here)
- Chromium patching (done upstream in the `carbonyl` repo; this crate only computes the config to apply)
- Humanization / behavioral timing (separate crate; persona provides keys referencing that crate's registry)

## Build

```
cd ~/dev/carbonyl-agent/crates/carbonyl-fingerprint
cargo test
```
