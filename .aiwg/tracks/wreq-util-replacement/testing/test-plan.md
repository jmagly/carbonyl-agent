# Test Plan — wreq-util Replacement Track

**Status**: Draft
**Date**: 2026-05-15
**Track**: wreq-util-replacement
**Refs**: roctinam/carbonyl-agent#99, UC-W03

## 1. Scope

Verification that the in-house preset registry (UC-W01) and direct-wired `WreqClient` (UC-W02) produce wire output behaviorally equivalent to the existing `wreq_util::Emulation`-mediated path for the five canonical persona families, with explicit tolerance for known divergences.

In scope:

- Unit tests on `PresetTable` construction and `preset_for` lookup
- Layer 1 structural assertions via `ApplyInspector` (existing infrastructure, extended)
- Layer 2 wire-byte conformance against captured fixtures (extended coverage)
- Regression on existing `carbonyl-wreq` test suite
- License-compliance gate on the full workspace

Out of scope:

- Performance regression deep-dives (covered separately by NFR-W-03 benchmark)
- Live differential testing against real browsers (ADR-W03 rejected; future enhancement)
- Coverage for browser families/versions outside the five canonical persona fixtures

## 2. Test pyramid

| Layer | Tool | Runs in CI | Purpose |
|-------|------|-----------|---------|
| L0 — Unit | `cargo test -p carbonyl-wreq --lib` | Every PR | Preset struct integrity, `preset_for` returns expected entries, no preset has empty mandatory fields |
| L1 — Structural | `cargo test -p carbonyl-wreq --test conformance_layer1` | Every PR | Persona → `PendingConfig` records expected fields; `ApplyInspector` assertions |
| L2 — Wire-byte | `cargo test -p carbonyl-wreq --test conformance_layer2` | Every PR | `wreq::Client` request emits ClientHello + h2 SETTINGS matching fixture |
| L3 — Workspace | `cargo test --workspace --features carbonyl-wreq/python` | Every PR | No regression in other crates; Python binding still builds |
| L4 — License | `cargo about generate ...` | Every PR | Full workspace produces clean `THIRD_PARTY_LICENSES.txt` |
| L5 — Manual | `tests/manual/recapture_fixture.sh` | On-demand (HITL) | Re-capture a fixture when a browser updates |

## 3. Test inventory

### 3.1 L0 — Unit tests (new)

In `crates/carbonyl-wreq/src/presets/mod.rs`:

- `preset_for_returns_chrome_147_desktop_for_exact_match` — exact triple resolves correctly.
- `preset_for_returns_safari_ios_for_safari_family_mobile_ios` — platform routing works.
- `preset_for_falls_back_to_nearest_within_family` — Chrome 999 → Chrome 147 desktop with `tracing::warn!`.
- `preset_for_never_crosses_family_boundary` — Firefox 999 → Firefox preset, never Chrome.
- `every_preset_declares_nonempty_tls_extension_order` — iterate all registered presets.
- `every_preset_declares_nonempty_h2_settings` — iterate all registered presets.
- `every_preset_has_provenance_id_matching_a_fixture_file` — iterate all registered presets, assert `data/fixtures/<id>.pcap` exists.

### 3.2 L1 — Structural conformance (extend existing)

`crates/carbonyl-wreq/tests/conformance_layer1.rs` already exists in spirit (current tests at `client.rs` lines 329–387). Rewrite to use the new `WreqClient` shape:

- One test per canonical fixture (5 tests):
  - `chrome_147_stable_linux_records_persona_fields`
  - `firefox_150_stable_linux_records_persona_fields`
  - `safari_26_macos_records_persona_fields`
  - `mobile_chrome_android_records_persona_fields`
  - `mobile_safari_ios_records_persona_fields`

Each test:

1. Builds `WreqClient`.
2. Calls `apply_persona_typed` with the fixture's persona.
3. Invokes the existing `conform(&mut client, &fixture)` from `carbonyl_fingerprint::conformance`.
4. Asserts the new `family`/`version`/`platform` recording shape (replacing today's `emulation == Chrome137` assertions).

- One new test per family asserting persona-override behavior:
  - `chrome_persona_with_custom_h2_settings_overrides_preset` — non-default `http2_akamai` reaches the wire.

### 3.3 L2 — Wire-byte conformance (new test crate / extended)

`crates/carbonyl-wreq/tests/conformance_layer2.rs` is the new path. Structure:

```rust
// tests/conformance_layer2.rs (sketch)

#[tokio::test]
async fn chrome_147_desktop_emits_expected_clienthello() {
    let fixture = Fixture::load("chrome-147-desktop");
    let responder = LocalTlsResponder::bind().await;
    let mut client = WreqClient::new();
    client.apply_persona_typed(&fixture.persona).unwrap();
    let wreq = client.build().unwrap();
    let _ = wreq.get(responder.url("/probe")).send().await; // expected: probe rejected
    let captured = responder.captured_clienthello().await;
    assert_clienthello_equivalent(&captured, &fixture.expected_clienthello, &divergences());
}
```

Five tests, one per canonical fixture. `assert_clienthello_equivalent` walks fields per ADR-W03 §"Tolerance set", applying `divergences.toml`.

A parallel `tokio::test` per fixture asserts the h2 SETTINGS frame, after the TLS handshake completes against a minimal h2-capable responder.

### 3.4 L3 — Workspace regression

`cargo test --workspace --features carbonyl-wreq/python` runs everything. Particular attention:

- `carbonyl-fingerprint` tests — no expected regression (no API change to the trait).
- Python binding build — `cargo build -p carbonyl-wreq --features python` succeeds without `wreq-util`.
- W3B integration tests (if any exist) — pass.

### 3.5 L4 — License compliance

```bash
# Pre-replacement baseline
cargo about generate --config about.toml --output-file THIRD_PARTY_LICENSES.baseline.txt

# After Iteration C
cargo about generate --config about.toml --output-file THIRD_PARTY_LICENSES.txt
diff THIRD_PARTY_LICENSES.baseline.txt THIRD_PARTY_LICENSES.txt
# Expected: wreq-util entry removed; no GPL/AGPL/LGPL/MPL entries
```

Gate: a grep for copyleft license names in the regenerated file fails CI.

### 3.6 L5 — Manual fixture recapture (HITL)

Documented in `fixtures-plan.md`. Not part of CI; invoked when:

- A new browser version ships and we extend the persona schema.
- An external JA4 database update indicates a wire-shape change for an existing version.
- A conformance test fails on a clean local checkout and the divergence is "the real browser changed", not "our code regressed".

## 4. Test data

| Fixture | Persona path | Captured artifact | Notes |
|---------|--------------|-------------------|-------|
| chrome-147-desktop | `carbonyl-fingerprint::ConformanceFixture::chrome_147_stable_linux` | `data/fixtures/chrome-147-desktop.pcap` | Existing W3B fixture |
| chrome-147-mobile | `carbonyl-fingerprint::ConformanceFixture::mobile_chrome_android` | `data/fixtures/chrome-147-mobile.pcap` | Existing W3B fixture |
| firefox-150-desktop | `carbonyl-fingerprint::ConformanceFixture::firefox_150_stable_linux` | `data/fixtures/firefox-150-desktop.pcap` | Existing W3B fixture |
| safari-26-macos | `carbonyl-fingerprint::ConformanceFixture::safari_26_macos` | `data/fixtures/safari-26-macos.pcap` | Existing W3B fixture |
| safari-26-ios | `carbonyl-fingerprint::ConformanceFixture::mobile_safari_ios` | `data/fixtures/safari-26-ios.pcap` | Existing W3B fixture |

Each fixture entry under `data/fixtures/PROVENANCE.md` per NFR-W-05.

## 5. Tolerance and divergence handling

`tests/conformance/divergences.toml`:

```toml
[[divergence]]
fixture = "chrome-147-desktop"
field = "tls.grease_value_position_0"
reason = "RFC 8701 — GREASE values rotate per session"
tolerance = "any-valid-grease"
issue = "n/a"

[[divergence]]
fixture = "chrome-147-desktop"
field = "tls.random"
reason = "ephemeral nonce"
tolerance = "ignore"
issue = "n/a"
```

Each entry has `issue` + (optional) `close_by` field. CI gate fails on any entry past its `close_by`.

## 6. Test execution matrix

| Stage | Tests | Runner | Trigger |
|-------|-------|--------|---------|
| Pre-commit (developer) | L0, L1 | Local | Any change to `crates/carbonyl-wreq/` |
| PR CI | L0–L4 | Gitea Actions | Every PR |
| Nightly | L0–L4 + (manual L5 prompt) | Gitea Actions | Cron 0 6 * * * UTC |
| Release | L0–L4 + license diff vs. previous release | Gitea Actions | Tag push |

## 7. Entry / exit criteria

### Entry (start construction)

- ADR-W01, W02, W03 accepted.
- Fixtures captured per `fixtures-plan.md` for at least Chrome 147 desktop.
- `data/fixtures/PROVENANCE.md` exists.

### Exit (close #99)

- All L0–L4 tests pass on a green CI run.
- `divergences.toml` reviewed; no entries are masking known bugs.
- `THIRD_PARTY_LICENSES.txt` regenerated and committed.
- `wreq-util` absent from `Cargo.lock`.
- Persona schema unchanged.
- Wheel build succeeds via `hatchling` and includes the new license file.

## 8. Risk register (test-specific)

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Fixture captured incorrectly; tests pass on wrong reference | Wire-divergence ships silently | Medium | JA4 cross-check against external corpus at capture time |
| `divergences.toml` grows to mask real bugs | Drift accumulates undetected | Medium | `close_by` field + CI gate on expiry |
| `wreq` API change between intake and construction | L2 tests can't be wired up | Low | Cargo pin in workspace; revisit on every wreq bump |
| Local TLS responder has its own bugs | L2 results unreliable | Low | Responder is ~200 LOC; covered by its own unit tests |
| Persona schema gaps surface during inversion | L1 tests fail on the "persona omits field" path | Medium | Audit personas during Iteration A before flipping inversion |

## References

- @.aiwg/tracks/wreq-util-replacement/requirements/use-cases/UC-W03-wire-conformance-coverage.md
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-003-wire-conformance-coverage-strategy.md
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md
- @crates/carbonyl-wreq/src/client.rs - Existing tests at lines 322–413
- @crates/carbonyl-fingerprint/src/conformance.rs - ApplyInspector and ConformanceFixture types
