# ADR-W01: In-House Preset Registry — Typed Rust vs. Embedded TOML vs. build.rs Codegen

**Status**: Proposed
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Deciders**: carbonyl-agent maintainer (sole)
**Refs**: roctinam/carbonyl-agent#99, @.aiwg/architecture/adr-005-tls-fingerprint-http-client.md

## Context

UC-W01 requires an in-house registry of browser emulation preset data — TLS ClientHello extension ordering, h2 SETTINGS defaults, ALPN offer lists, header ordering — for the five canonical persona families (Chrome 147 desktop/mobile, Firefox 150 desktop, Safari 26 desktop/mobile).

The registry must be:

- Compile-time consistent (a typo in an extension ID should fail to compile, not produce wrong wire output at runtime).
- Auditable (every value traceable to a captured fixture).
- Extensible (adding Chrome 148 should be a small, mechanical edit).
- License-clean (no copying from `wreq-util`'s tables; values sourced from independent fixture capture).

Three structural options are on the table.

### Option A: Hand-written typed Rust constants

Each preset is a `const PresetTable` with explicit struct fields. The registry is a `match` or a `phf_map!` on `(family, version, platform)`.

```rust
pub static CHROME_147_DESKTOP: PresetTable = PresetTable {
    family: BrowserFamily::Chrome,
    version: BrowserVersion::new(147, 0),
    platform: Platform::Desktop,
    tls: TlsProfile {
        extension_order: &[0x00, 0x17, 0x0b, /* ... */],
        // ...
    },
    h2: H2Profile { /* ... */ },
};
```

**Pros**: zero build-time machinery, type-checked at compile, IDE-friendly, easy diff in code review.
**Cons**: verbose, easy to forget a field, harder to keep five families in sync visually.

### Option B: Embedded TOML via `include_str!` + parse at startup

Preset data lives in `crates/carbonyl-wreq/data/presets/chrome-147-desktop.toml`. Loaded once at registry init via `OnceLock<HashMap<_, PresetTable>>`.

```rust
static PRESETS: OnceLock<HashMap<PresetKey, PresetTable>> = OnceLock::new();
fn load() -> HashMap<...> {
    let chrome_147 = toml::from_str(include_str!("data/presets/chrome-147-desktop.toml")).unwrap();
    // ...
}
```

**Pros**: data and code separated cleanly, non-Rust contributors can edit, diff is readable.
**Cons**: runtime parsing cost (small but nonzero), errors deferred to first-use, less compile-time guarantees, requires `serde` derive on every wire struct.

### Option C: `build.rs` codegen from TOML to Rust

TOML files in `data/presets/` are compiled to Rust source by a `build.rs` step at compile time. The generated module is `include!`d.

**Pros**: data/code separation AND compile-time guarantees, generated Rust can be inspected (`cargo expand` or generated under `target/`).
**Cons**: build.rs adds maintenance burden, generated code is harder to debug, slower clean builds, the indirection adds cognitive overhead for a small data set (five entries today).

## Decision

**Option A: Hand-written typed Rust constants** for the initial implementation.

The data set is small (five entries today, plausibly twenty within two years). The compile-time guarantees and IDE introspection are worth more than the marginal verbosity. The diff readability concern is mitigated by structuring each `PresetTable` with named fields and grouping related data into substructs (`TlsProfile`, `H2Profile`, `HeaderProfile`).

If the registry grows past ~30 entries or non-Rust contributors begin editing presets, revisit Option C. Option B is rejected outright because deferring "did I get this preset right" to runtime is the exact failure mode the in-house registry is supposed to eliminate.

## Consequences

### Positive

- One file per preset family (`presets/chrome.rs`, `presets/firefox.rs`, `presets/safari.rs`) keeps diffs scoped.
- Rust's `const` evaluation catches obvious typos (a `u8` literal that overflows, an out-of-bounds slice index).
- Cross-preset structural consistency enforced by the type system — every preset has the same set of fields.
- No `build.rs` complexity; clean `cargo doc` output.
- The `PresetTable` struct doubles as the `ApplyInspector`-friendly assertion shape for Layer 1 conformance.

### Negative

- Adding a new preset is a manual code edit (vs. dropping a TOML file). Mitigation: the new-preset runbook in Iteration C documents the exact lines to add.
- Verbosity: five families × five wire profile substructs is ~600 lines of declarative data. Acceptable.
- Code review on a preset edit requires Rust competence. For a single-maintainer project, acceptable.

### Neutral

- The provenance of every preset value lives in `fixtures/PROVENANCE.md`, not inline in the source — both options would have this property.

## Module layout

```
crates/carbonyl-wreq/src/presets/
├── mod.rs              # PresetTable, TlsProfile, H2Profile, HeaderProfile, preset_for()
├── chrome.rs           # CHROME_147_DESKTOP, CHROME_147_MOBILE
├── firefox.rs          # FIREFOX_150_DESKTOP
└── safari.rs           # SAFARI_26_DESKTOP, SAFARI_26_MOBILE_IOS
```

See `@.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md` for concrete type sketches.

## Reasoning

1. **Problem Analysis**: We need preset data in a form that's safe to edit, reviewable in PRs, and compile-time-checked. The choice is between code, data files, and codegen.
2. **Constraint Identification**: Five entries today; sole maintainer; Rust workspace; need diff-friendly storage. Compile-time guarantees on extension IDs etc. are worth more than data-format flexibility at this scale.
3. **Alternative Consideration**: All three options enumerated above. Option B was the temptation (clean data/code split) but the runtime-parse cost in correctness terms outweighs the maintainability gain.
4. **Decision Rationale**: At five entries, Option A is the lowest-friction choice. Revisit at 30+.
5. **Risk Assessment**: Risk that the registry grows large enough to make Option A painful. Mitigation: explicit revisit threshold (30 entries). Risk that hand-edits introduce subtle bugs. Mitigation: conformance tests (UC-W03) catch wire-level wrongness; type system catches structural wrongness.

## References

- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W01-vendor-preset-registry.md - The use case this implements
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md - Concrete Rust shape
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md - Companion ADR on the binding model
- @crates/carbonyl-wreq/src/client.rs - Integration site
