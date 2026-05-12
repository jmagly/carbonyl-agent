# Doc-Sync Audit — Pre-Release (2026-05-12)

Pre-release audit run on `main @ 127bd56` to surface documentation drift
before cutting the next release tag. Direction: **code-to-docs**
(code is source of truth). Mode: **dry-run** — no files modified.

## Executive Summary

| Severity | Count | Auto-fixable | Template-fixable | Human-required |
|----------|-------|--------------|------------------|----------------|
| Critical | 1     | 0            | 1                | 0              |
| High     | 3     | 1            | 2                | 0              |
| Medium   | 4     | 2            | 1                | 1              |
| Low      | 3     | 3            | 0                | 0              |
| **Total**| **11**| **6**        | **4**            | **1**          |

**Headline**: CHANGELOG `[Unreleased]` section is empty. 60 commits +
17,516 inserts / 1,101 deletes have landed since `v0.1.0a1` (2026-05-05)
covering the entire W3A.x persona work, the W3B Phase 1 + Phase 2
egress stack, and three new Rust crates. None of it is in the
changelog. **This is the single blocker for a release tag.**

## Inventory (current facts of the codebase)

| Metric | Value | Source |
|--------|-------|--------|
| Project version | `0.1.0a1` | `pyproject.toml` |
| Last release tag | `v0.1.0a1` (2026-05-05) | `git tag` |
| Commits since release | 60 | `git log v0.1.0a1..HEAD` |
| Diff since release | 148 files, +17,516 / −1,101 | `git diff --shortstat` |
| Python source files | 15 | `src/carbonyl_agent/*.py` |
| Rust crates | 2 (carbonyl-fingerprint, carbonyl-wreq) | `crates/` |
| Python test files | 19 | `tests/test_*.py` |
| Python tests collected | 398 | `pytest --collect-only` |
| Rust tests (workspace, default features) | 184 | `cargo test --workspace` |
| Rust tests (workspace, `--features carbonyl-wreq/python`) | 191 | adds 7 lib tests |
| ADRs | 6 | `.aiwg/architecture/adr-*.md` |
| CI workflows | 6 | `.gitea/workflows/*.yml` |

## Findings

### Critical

#### DOC-DRIFT-001 — CHANGELOG `[Unreleased]` is empty
- **File**: `CHANGELOG.md`
- **Code reality**: 60 commits since `v0.1.0a1` covering W3A.1–W3A.7, W3B Phase 1 (egress), W3B Phase 2 (wreq + PyO3), `persona_apply`, `wreq_transport`, runtime pin bump from `dd69bef0ea4b2512` → `9b3ba53adcd8d330`, qa-runner image work, ADR-005 acceptance, cookie-flush flag wiring, conformance.yml workflow.
- **Drift**: The `## [Unreleased]` section in CHANGELOG.md is empty after the file's header.
- **Fix category**: Template-fixable
- **Action**: Generate `[Unreleased]` entries grouped by `Added / Changed / Fixed / Security` from the 60-commit set. Mirror the structure of the existing `[0.1.0a1]` section. Cross-reference the issue numbers in commit subjects (#41, #43, #44, #62, #69, #71-#75, #80-#85).

### High

#### DOC-DRIFT-002 — README "Persona-Bound Egress" example uses `b.egress()` accessor that doesn't exist
- **File**: `README.md` line ~378
- **Doc claim**: The "Persona-Bound Egress" section's example shows:
  ```python
  with CarbonylBrowser(persona=p, viewport=(1280, 800)) as b:
      b.open("https://example.com/login")
      client = EgressClient(p)        # ← explicit constructor
      r = client.get(...)
  ```
- **Note**: ADR-005 and issue #44 originally specified `browser.egress() → EgressClient`. The README example *correctly* uses the explicit `EgressClient(p)` constructor pattern that actually shipped, but ADR-005 still references the unimplemented `b.egress()` accessor in its acceptance criteria.
- **Drift**: ADR-005 § "Acceptance criteria" line listing `helper: browser.egress() -> EgressClient` is unimplemented but unmarked. Issue #44 carries the same unfulfilled item.
- **Fix category**: Template-fixable
- **Action**: Either (a) ship the accessor on `CarbonylBrowser` (one-line method returning `EgressClient(self._persona)`) or (b) update ADR-005 to remove it from acceptance criteria.

#### DOC-DRIFT-003 — README "Bot Detection Flags" claims `--disable-http2`
- **File**: `README.md` line ~436
- **Doc claim**: "`--disable-http2` (HTTP/2 SETTINGS frame is a server-side fingerprint)"
- **Code reality**: With W3B Phase 2 landed, the egress path matches HTTP/2 SETTINGS to the persona. `--disable-http2` on the browser session is now in tension with the broader fingerprinting work — disabling H2 emits a different signature (no SETTINGS frame at all) than the persona's `http2_akamai` field declares.
- **Fix category**: Human-required
- **Action**: Decide whether the flag stays as-is (browser path keeps H2 off; egress path emits matching H2 SETTINGS) or whether the flag is dropped (both paths emit H2). Document the decision in `.aiwg/architecture/` and update the README's reasoning.

#### DOC-DRIFT-004 — `AGENTS.md` and `AIWG.md` show as modified in `git status` but are auto-regenerated
- **File**: `AGENTS.md`, `AIWG.md`, `.aiwg/aiwg.config`
- **Code reality**: These files are AIWG-regenerated context dumps. They drift constantly as upstream AIWG advances. Treating them as tracked artifacts means every `aiwg refresh` produces a "dirty working tree".
- **Drift**: Already-modified state means the next push includes auto-regenerated noise.
- **Fix category**: Auto-fixable
- **Action**: Either commit the regenerated state (one-time noise commit) or add them to `.gitignore`. Repository convention from prior commits is to commit them — recommend a single `chore(aiwg): regenerate context files` commit at release time.

### Medium

#### DOC-DRIFT-005 — README missing test count
- **File**: `README.md`
- **Doc claim**: No mention of test suite size.
- **Code reality**: 398 Python tests + 184 Rust (workspace) tests + 191 with `--features python`.
- **Fix category**: Auto-fixable
- **Action**: Add a "Quality Bar" or "Tests" line: "398 Python tests + 184 Rust tests (191 with the `python` feature) cover the SDK, fingerprint validator, and wreq backend."

#### DOC-DRIFT-006 — Runtime compatibility matrix entry for `runtime-3f5e5a96aa10c4ac` is stale
- **File**: `README.md` line ~470
- **Doc claim** (after #51 bump): `runtime-dd69bef0ea4b2512` is "Backwards-compat tested"
- **Code reality**: The e2e workflow's "prior runtime" matrix slot does test `dd69bef0ea4b2512`. The README's third row says "Older `runtime-*` tags | Best-effort | Not in CI". Accurate now but no mention of the **cookie-flush flag activation** that the 9b3ba53 bump enabled (#51).
- **Fix category**: Auto-fixable
- **Action**: Add a one-liner under the matrix: "The current pin enables `--carbonyl-cookie-flush-interval-ms=1000` (#51) — earlier runtimes silently ignore the flag and require ≥ 30 s drain before `close()`."

#### DOC-DRIFT-007 — ADR-005 Phase 2 transition note says "the sentinel is gone" but it isn't
- **File**: `.aiwg/architecture/adr-005-tls-fingerprint-http-client.md` (the recently-added Phase 2 section)
- **Doc claim**: The newly-added Phase 2 section's intro sentence says fields now carry real JA4. The fallback path still uses the sentinel.
- **Drift**: Phase 2 section accurately describes the dual-path behavior (transport tag), but the parent issue's accept criterion #44 says "audit row's ja4_actual becomes a real wire-captured value (replacing the sentinel)" — which is only true for the `transport: wreq` path. The httpx-fallback path *preserves* the sentinel.
- **Fix category**: Template-fixable
- **Action**: Clarify the Phase 2 section's opening to: "Phase 2 retains the sentinel for fallback rows and surfaces real captured values for wreq rows; the `transport` field disambiguates."

#### DOC-DRIFT-008 — Docs nowhere reference the new `transport` audit field
- **File**: `README.md` "Audit / drift detection" subsection (≥ #44 era), ADR-005, and any consumer integration docs
- **Drift**: The audit log shape changed in ddb8ecf (added `transport` field). Existing consumer scripts that rely on JSON Lines schema won't error but won't know about the new column.
- **Fix category**: Auto-fixable
- **Action**: Add a `transport` row to the audit-row schema documentation: "transport: `wreq` or `httpx-fallback` — added in 0.2.0a1, Phase 2.4 (#83)."

### Low

#### DOC-DRIFT-009 — `.carbonyl-fingerprint-version` header comment still references "TBD-#44 placeholders"
- **File**: `.carbonyl-fingerprint-version`
- **Drift**: The pin file's header comment block talks about `TBD-#44` placeholders and says "values below are TBD placeholders that #44 must replace as its first task." That's stale — #44 + #75 landed. The new `wreq-sha=registry` resolution is documented inline but the top-block comment hasn't been updated.
- **Fix category**: Auto-fixable

#### DOC-DRIFT-010 — Project repo URL split between Gitea + GitHub
- **File**: `pyproject.toml`, `README.md`, `Cargo.toml`s
- **Drift**: Project root mentions both `git.integrolabs.net/roctinam/carbonyl-agent` and `github.com/jmagly/carbonyl-agent`. No `[project.urls]` block in `pyproject.toml` declares the canonical one for PyPI metadata.
- **Fix category**: Auto-fixable
- **Action**: Add a `[project.urls]` table with Homepage / Repository / Issues for the release; needed for #12 trusted publisher metadata anyway.

#### DOC-DRIFT-011 — README "Documentation" section likely points to stale paths
- **File**: `README.md` line ~518 (Documentation section)
- **Action**: Spot-check links during release prep. Not run as part of this audit.

## Sync Plan

### Pre-release (must do before tagging)

1. **DOC-DRIFT-001** — Write `[Unreleased]` entries. Template at the end of this report.
2. **DOC-DRIFT-003** — Resolve the `--disable-http2` policy question. Either commit the flag's continued use with rationale, or remove it.
3. **DOC-DRIFT-004** — Single commit absorbing regenerated AGENTS.md / AIWG.md / aiwg.config.

### Nice to have (block on user decision)

4. **DOC-DRIFT-002** — Decide on `browser.egress()` accessor. Either ship it or update ADR-005.
5. **DOC-DRIFT-005**, **006**, **007**, **008** — Documentation polish.
6. **DOC-DRIFT-009** — Pin file header cleanup.
7. **DOC-DRIFT-010** — Add `[project.urls]` to pyproject.toml (also unblocks #12 trusted publisher).

### Out of scope for release

8. **DOC-DRIFT-011** — Link audit (run separately).

## Suggested CHANGELOG `[Unreleased]` Template

Grouped by major workstream:

```markdown
## [Unreleased]

### Added — Persona system (W3A)
- `carbonyl_fingerprint` Rust crate: typed Persona schema (v2, BrowserFamily enum),
  consistency validator (rules A–H), TOML refresher with bounded drift, sampler
  with five W3A.6 family templates (Chrome 147 Linux, Firefox 150 Linux, Safari
  26 macOS, Mobile Chrome 147 Android, Mobile Safari 26 iOS). PyO3 bindings
  expose `validate_toml()` and `is_valid_toml()`. (#43, #66, #68-#74)
- `CarbonylBrowser(persona=...)` constructor — persona-bound profile management
  with file-locked, per-persona Chromium user-data-dir under
  `CARBONYL_AGENT_PROFILES_DIR`. `purge_profile`, `export_profile`,
  `import_profile`. `from carbonyl_agent import ProfileManager,
  list_personas`. (#41, #45)
- Bounded-drift refresher (`carbonyl_fingerprint::refresher`) — regenerates
  noise seeds via HKDF-Expand on a stable persona id; deterministic re-sample
  for canvas/audio. (#70)

### Added — Persona-bound egress (W3B Phase 1)
- `carbonyl_agent.egress.EgressClient` — persona-aware httpx wrapper.
  Per-(persona, host) connection pool, audit log writer (JSON Lines under
  XDG state dir), drift detector, STRICT/WARN/OFF audit modes via
  `CARBONYL_FP_AUDIT`. Audit row records `ja4_expected`, `ja4_actual`,
  `transport`, latency, drift bool. (#44)

### Added — Persona-bound egress (W3B Phase 2 — wreq backend)
- `carbonyl-wreq` Rust crate — implements `carbonyl_fingerprint::http::HttpClient`
  against the `wreq` 5.x browser-emulating HTTPS client. Persona →
  `wreq_util::Emulation` preset selection (Chrome147→Chrome137,
  Firefox150→Firefox139, Safari26→Safari18_3_1, iOS→SafariIos17_4_1).
  H2 SETTINGS overrides via `Http2Builder`. (#75, #80, #81)
- Layer 2 wire conformance test crate — drives real wreq client through
  a localhost rustls responder, parses ClientHello + h2 preface bytes,
  produces a `WireSnapshot`, asserts `assert_wire_state` against the
  fixture. Per-fixture baseline gap lists document the residual drift
  from wreq-util preset versions lagging the persona target versions. (#82)
- `carbonyl_agent.wreq_transport.WreqTransport` — `httpx.BaseTransport`
  wrapper that delegates to the optional `carbonyl_wreq` native module.
  `EgressClient` auto-detects at construction; falls back to httpx
  silently when the native module isn't built. Audit row's new
  `transport` field disambiguates `wreq` vs `httpx-fallback`. (#83)
- PyO3 binding in `carbonyl-wreq` — single `send_request` function with
  embedded multi-threaded tokio runtime via `OnceLock<Runtime>`. Build
  with `maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml
  --features python`. (#85)
- `pyproject.toml` `[wreq]` extra.
- `.gitea/workflows/conformance.yml` — gates fingerprint / wreq / egress
  PRs on the `--features carbonyl-wreq/python` workspace test path. (#84)

### Added — Operational
- `--carbonyl-cookie-flush-interval-ms=1000` flag wired into
  `CarbonylBrowser._spawn` for persona/session mode — cookies persist
  within the 5 s graceful_timeout (previously required 30+ s drain).
  Requires Carbonyl runtime `9b3ba53adcd8d330` or newer; older runtimes
  silently ignore the flag. (#51)

### Changed
- Runtime pin bumped: `runtime-hash=dd69bef0ea4b2512` →
  `runtime-hash=9b3ba53adcd8d330` (Carbonyl v0.2.0-alpha.4). E2E
  compatibility matrix rotated; `dd69bef0ea4b2512` now occupies the
  prior-runtime slot. (#51)
- ADR-005 marked Accepted. Phase 1 → Phase 2 transition notes added
  covering the sentinel preservation, transport-tag field, and
  cross-references to the new Layer 1 / Layer 2 conformance test
  crates. (#64, #75)
- Workspace conversion: top-level `Cargo.toml` over both Rust crates;
  single root `Cargo.lock`. `check.yml` runs `cargo {fmt,clippy,test}
  --workspace`. (#80)
- `check.yml` installs `clang cmake libclang-dev libssl-dev pkg-config
  python3-dev` before invoking cargo, for the boring-sys2 + PyO3
  build path. (#81, #85)

### Fixed
- `wreq-mirror` workflow skips early when `wreq-sha=registry`; the
  cold mirror anchors on git SHAs and registry-resolved deps use
  crates.io + `Cargo.lock` as the integrity record. (#81)
- CI typecheck + test jobs install `[dev,egress]` extras (not just
  `[dev]`) so httpx is available for mypy. (#44 follow-up)
```

(Add three-section grouping conventions; pull exact commit subjects
into a second pass; flag any items that should be Security-scoped.)

## Validation Notes

This audit was performed manually because the project's small scope
(~15 Python modules, 2 Rust crates) didn't justify dispatching the 8
parallel auditor agents the standard `doc-sync` skill is designed for.
The reviewed surface:

- README.md — full read
- CHANGELOG.md — full read, diffed against git log v0.1.0a1..HEAD
- All ADRs — spot-checked against current code
- pyproject.toml — version + extras inventory
- 60 commit subjects + shortstat — drift detection
- pytest collection — test count
- cargo test counts (workspace, both feature configurations) — Rust test count

What was NOT covered (deliberate scope limits):
- @-mention validation across all files (no @-mentions present in current docs)
- Internal markdown link checking
- Diagram-vs-code drift in software-architecture-doc.md
- Per-module docstring vs README accuracy
