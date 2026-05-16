# Runbook: Adding a New Browser Preset

**Status**: Active (covers the in-house preset registry post-Iteration B)
**Last revised**: 2026-05-15
**Owner**: carbonyl-agent maintainer
**Closes**: NFR-W-04 verification (per [#103](https://git.integrolabs.net/roctinam/carbonyl-agent/issues/103) item 4)
**Refs**: [ADR-W02](../../tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md), [ADR-W02 addendum](../../tracks/wreq-util-replacement/architecture/adr-002-addendum-wreq-api-shape.md), [`design-preset-registry.md`](../../tracks/wreq-util-replacement/architecture/design-preset-registry.md), [`fixtures-plan.md`](../../tracks/wreq-util-replacement/testing/fixtures-plan.md)

## Purpose

Add a new browser preset (e.g. Chrome 148 desktop, Firefox 151 mobile) to `crates/carbonyl-wreq/src/presets/` so personas declaring that browser/version/platform get correct wire-level emulation.

A preset is "added" when:
1. A real-browser fixture exists at `crates/carbonyl-wreq/data/fixtures/<id>.{pcap,client_hello.bin,h2_settings.bin,metadata.toml}` with verified provenance.
2. A `PresetTable` constant exists in the appropriate family module (`chrome.rs`, `firefox.rs`, `safari.rs`).
3. `presets::preset_for()` returns it for the target `(family, version, platform)` triple.
4. A Layer 2 wire-conformance test exists exercising the preset against the fixture.
5. `cargo test -p carbonyl-wreq --test conformance_layer2` is green.

Target wall-clock time once the fixture is in hand: **under one hour**.

## Prerequisites

- Real instance of the browser to add (or access to one — see capture procedure below).
- `tcpdump`, `tshark`, `cargo`, `cargo-about`, `ja4` CLI from FoxIO (validation tooling).
- Write access to the corpus repo.

## Procedure

### Step 1 — Capture the fixture (HITL, ~20 min)

Follow [`fixtures-plan.md`](../../tracks/wreq-util-replacement/testing/fixtures-plan.md) §3 for the platform-specific capture procedure. The high-level shape:

1. Stand up a localhost TLS+h2 responder that accepts the connection, completes the handshake, accepts the SETTINGS frame, then closes — logging the raw bytes.
2. Start `tcpdump -i lo -nn -w <fixture-id>.pcap port 8443`.
3. Launch the target browser (verified version match) against `https://127.0.0.1:8443/`.
4. Stop capture. Extract `<fixture-id>.client_hello.bin` (`tshark -r ... -Y 'tls.handshake.type==1' -T fields -e tls.handshake -w ...`) and `<fixture-id>.h2_settings.bin` from the responder log.
5. Compute SHA-256 of all three artifacts.
6. Compute the JA4 hash of `client_hello.bin` with the FoxIO `ja4` CLI.
7. **Cross-check** the JA4 against the FoxIO database for "<browser> <version> <platform>". If it matches, capture is validated. If not, restart from step 1 — environment was wrong (extension installed, language pack, etc.).
8. Write `<fixture-id>.metadata.toml` per the schema in `fixtures-plan.md` §3.2.
9. `cd crates/carbonyl-wreq/data/fixtures/ && git add <fixture-id>.*` and append a row to `PROVENANCE.md`.

**Verification**: `sha256sum <fixture-id>.client_hello.bin` matches the value recorded in `<fixture-id>.metadata.toml`.

### Step 2 — Add the preset entry (~15 min)

Open the family module (`crates/carbonyl-wreq/src/presets/<family>.rs`). Add a new `pub static <UPPER_SNAKE_NAME>: PresetTable = PresetTable { ... };`.

Populate fields from the fixture:

| `PresetTable` field | Source |
|---|---|
| `family`, `version`, `platform` | Constants you set |
| `tls.extension_permutation_indices` | Computed from the ClientHello extension order. See "Computing extension_permutation_indices" below. |
| `tls.cipher_list` | Extract from `client_hello.bin` and emit as OpenSSL-style colon-separated string (e.g. `"TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:..."`). The `tls-parser` crate already in our dev-deps provides the cipher enumeration; convert to OpenSSL names via the IANA-to-OpenSSL mapping table. |
| `tls.alpn_default` | The ALPN list from the ClientHello's `application_layer_protocol_negotiation` extension. |
| `tls.grease_enabled` | `Some(true)` if any extension type matches a GREASE pattern (`0x?A?A`); `Some(false)` for Firefox; `None` only if uncertain. |
| `tls.permute_extensions` | `Some(true)` for Chrome 110+ (the captured order is one sample of a permuted set); `Some(false)` for Firefox/Safari; `None` only if uncertain. |
| `tls.supported_groups` | The `supported_groups` extension contents, as raw `u16` IANA codes. |
| `tls.sigalgs_list` | The `signature_algorithms` extension contents, as OpenSSL-style colon-separated string. |
| `h2.settings_default` | The SETTINGS frame entries in order, as `(u16, u32)` tuples. Read from `h2_settings.bin`. |
| `h2.initial_connection_window` | The initial WINDOW_UPDATE value the browser sends after SETTINGS. |
| `h2.pseudo_header_order` | The pseudo-header order the browser uses in HEADERS frames. Capture by issuing one request and reading the HEADERS frame; or infer from the family's documented behavior (`Chrome: m,a,s,p`; `Firefox: m,p,a,s`; `Safari: m,s,p,a`). |
| `headers.default_order` | The full default header emission order observed in the request. |
| `headers.static_defaults` | Values for headers the persona doesn't declare (e.g. `Accept`, `Accept-Encoding`, `sec-fetch-*`, `Priority`). Read from the request. |
| `provenance_id` | The fixture id (e.g. `"chrome-148-desktop"`). Must match the `<fixture-id>` in `data/fixtures/`. |

#### Computing `extension_permutation_indices`

wreq's `extension_permutation_indices` is a `&[u8]` of indices into wreq's known-extension table, NOT raw IANA u16 IDs. To compute:

1. Parse the captured ClientHello extension order (sequence of u16 IANA IDs).
2. For each ID, look up its position in wreq's `KNOWN_EXTENSIONS` table (search `~/.cargo/registry/src/*/wreq-5.*/src/tls/` for the table; index is u8).
3. Emit the indices in capture order.

If an extension in the capture has no entry in wreq's table (rare; an experimental extension), file a wreq upstream ticket and use `None` for `extension_permutation_indices` (wreq's default order kicks in — captures whatever the underlying boring2 emits, which is usually close enough for the affected build). Document the gap in `divergences.toml`.

### Step 3 — Wire it into `preset_for()` (~2 min)

In `presets/mod.rs`, add a new arm to the match:

```rust
pub fn preset_for(
    family: BrowserFamily,
    version: BrowserVersion,
    platform: Platform,
) -> Option<&'static PresetTable> {
    match (family, version.major, platform) {
        (BrowserFamily::Chrome, 147, Platform::Desktop) => Some(&chrome::CHROME_147_DESKTOP),
        (BrowserFamily::Chrome, 148, Platform::Desktop) => Some(&chrome::CHROME_148_DESKTOP),  // NEW
        // ... other arms
        _ => Some(nearest_neighbor(family, version, platform)),
    }
}
```

### Step 4 — Add the L2 conformance test (~10 min)

In `crates/carbonyl-wreq/tests/conformance_layer2.rs`, add a test that:

1. Loads the persona for the new browser/version/platform (or a synthetic minimal persona declaring just `browser_family`, `browser_version`, `platform`).
2. Calls `WreqClient::build_via_registry(&persona)` to produce an `EmulationProvider`.
3. Connects through the L2 responder (`tests/conformance/responder.rs`) and captures the emitted ClientHello + SETTINGS bytes.
4. Compares against `data/fixtures/<fixture-id>.client_hello.bin` and `<fixture-id>.h2_settings.bin`. Tolerated divergences (per `tests/conformance/divergences.toml`) are skipped.
5. Asserts byte-equality outside the tolerated set, AND asserts JA4-of-emitted matches `<fixture-id>.metadata.toml`'s recorded JA4.

### Step 5 — Run the conformance test (~3 min)

```bash
cargo test -p carbonyl-wreq --test conformance_layer2 -- --nocapture <fixture_id>
```

Expected outcome: the new test passes. If it fails, the typical causes:

| Failure mode | Likely cause | Fix |
|---|---|---|
| `extension_permutation_indices` mismatch | Mis-translated IANA→wreq index | Recompute per step 2 |
| `cipher_list` mismatch | Wrong OpenSSL name in mapping | Check IANA→OpenSSL table |
| `h2 settings` order mismatch | Tuples in wrong order | Reorder per fixture |
| JA4 mismatch | Any of the above + GREASE `Some(true/false)` mismatch | JA4 hashes the cipher list, extension list, sig algs — re-derive |
| Hangs | Responder mismatch | Check `tests/conformance/responder.rs` accepts the new ALPN |

Iteration count target: **2–3 passes**. If you exceed 4, escalate — the fixture may not match the persona's declared JA4 (regenerate fixture from a fresh browser install).

### Step 6 — Run the full workspace and verify nothing regressed (~5 min)

```bash
cargo build --workspace
cargo test --workspace --features carbonyl-wreq/python
```

Both must succeed. The new preset shouldn't affect any existing preset's tests.

### Step 7 — Update the license report (only when adding affects deps)

If the new preset required a new crate dependency (rare; usually unnecessary), regenerate the license report:

```bash
scripts/gen-third-party-licenses.sh
git add THIRD_PARTY_LICENSES.txt
```

If no new dependency, skip this step.

### Step 8 — Commit (~2 min)

```bash
git add crates/carbonyl-wreq/src/presets/<family>.rs \
        crates/carbonyl-wreq/src/presets/mod.rs \
        crates/carbonyl-wreq/data/fixtures/<fixture-id>.* \
        crates/carbonyl-wreq/data/fixtures/PROVENANCE.md \
        crates/carbonyl-wreq/tests/conformance_layer2.rs

git commit -m "wreq(presets): add <browser>-<version>-<platform> preset

Refs: roctinam/carbonyl-agent#<issue>"
```

Push per project delivery policy (`delivery.mode: direct` → straight to main).

## Total time

| Step | Time |
|---|---|
| 1. Capture fixture (HITL) | 20 min |
| 2. Add preset entry | 15 min |
| 3. Wire into `preset_for()` | 2 min |
| 4. Add L2 conformance test | 10 min |
| 5. Run conformance | 3 min |
| 6. Run full workspace | 5 min |
| 7. License report (rare) | 2 min |
| 8. Commit | 2 min |
| **Total** | **~1 hour** |

Target met: under one hour from "browser version exists" to "merged preset entry with passing conformance test". Closes NFR-W-04 verification.

## Paper walkthrough — hypothetical Chrome 148 desktop add

The runbook is validated by a paper walkthrough of adding Chrome 148 desktop assuming Chrome 148 ships in late 2026:

1. **Capture** (20 min): Stand up responder; launch Chrome 148; tcpdump on lo; extract ClientHello + SETTINGS; compute SHA-256s + JA4; cross-check against FoxIO; write metadata.toml; commit fixture artifacts.
2. **Add preset** (15 min): Open `chrome.rs`; copy-modify `CHROME_147_DESKTOP` to `CHROME_148_DESKTOP`; update `version.major = 148`; recompute `extension_permutation_indices` (Chrome 148 may have added/removed an extension); update `cipher_list` (rare to change); update `provenance_id = "chrome-148-desktop"`. Other fields likely unchanged from 147 (h2 settings stable across minor Chrome versions).
3. **Wire** (2 min): Add `(BrowserFamily::Chrome, 148, Platform::Desktop) => Some(&chrome::CHROME_148_DESKTOP),` arm to `preset_for()`.
4. **Test** (10 min): Add `chrome_148_desktop_l2_conformance` test in `conformance_layer2.rs`, copy-modeled on the Chrome 147 test.
5. **Run** (3 min): `cargo test -p carbonyl-wreq --test conformance_layer2 -- chrome_148_desktop_l2_conformance` — passes on first attempt because Chrome 148 is a minor revision of 147 and only `extension_permutation_indices` changed.
6. **Workspace** (5 min): `cargo build --workspace && cargo test --workspace --features carbonyl-wreq/python` — green.
7. **License report**: skip (no new deps).
8. **Commit** (2 min): per template above.

**Walkthrough verdict**: ~57 minutes wall-clock, comfortably under the one-hour target. Runbook is complete; no missing steps.

## When the runbook fails

Cases the runbook does NOT cover:

- **First-of-family preset** (e.g. Edge, Brave, Opera if those families gain dedicated presets): requires creating a new family module file and extending `BrowserFamily` enum in `carbonyl-fingerprint::schema`. ADR-level change.
- **Extension wreq doesn't know about**: requires upstream wreq PR. Document in `divergences.toml`; `extension_permutation_indices = None` as workaround.
- **Browser that doesn't permute extensions but rotates cipher_list across builds**: requires multiple fixtures + a `cipher_list_variants` field on `TlsProfile`. Schema extension.

Each of these is a one-off with its own decision; the runbook is for the steady-state minor-revision-add case.

## References

- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-persona-as-source-of-truth.md
- @.aiwg/tracks/wreq-util-replacement/architecture/adr-002-addendum-wreq-api-shape.md
- @.aiwg/tracks/wreq-util-replacement/architecture/design-preset-registry.md
- @.aiwg/tracks/wreq-util-replacement/testing/fixtures-plan.md
- @crates/carbonyl-wreq/src/presets/mod.rs (preset registry types and `preset_for()`)
- @crates/carbonyl-fingerprint/src/schema.rs (persona schema; `BrowserFamily` enum)
- [FoxIO JA4 specification](https://github.com/FoxIO-LLC/ja4)
