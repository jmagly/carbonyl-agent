# ADR-005: TLS-Fingerprint-Aware HTTP Client (`wreq`) for Non-Browser Egress

**Status**: Accepted
**Date**: 2026-05-06 (proposed) / 2026-05-08 (accepted)
**Version**: 1.0 (Baselined)
**Deciders**: Joseph Magly (sole maintainer — acting as architect, security architect, and eng lead)
**Supersedes**: —
**Issue**: #42 (closed on acceptance; unblocks #43 W3A, #44 W3B, #45 W3C; Phase 3 EPIC #34)

---

## Context

Phase 3 of the Trusted Automation Initiative introduces **personas** — bundled, internally-consistent identities composed of a Chromium fingerprint (W3A schema), a network-stack fingerprint (TLS JA3/JA4 + HTTP/2 settings), and a behavioral profile. Browser-driven traffic inherits its TLS fingerprint from the Carbonyl Chromium fork. Non-browser egress (W3B: API calls, RSS polling, OAuth flows, programmatic fetches issued by SDK consumers) currently uses whatever HTTP client the consumer reaches for, which produces a TLS fingerprint that does **not** match the persona's browser fingerprint. The mismatch is the canonical detector signal: "browser says Chrome 120, but the API call says Go-http-client 1.1."

The fix is a TLS-fingerprint-aware HTTP client driven by the same persona record. Library options surveyed:

1. **`wreq`** (Rust crate, fork of `reqwest`) — rustls-based, exposes JA3/JA4/H2 frame controls. Active development, small team.
2. **`reqwest-impersonate`** — fork of `reqwest` using BoringSSL with hardcoded preset profiles ("chrome120", "firefox118"). No per-field control; preset-only.
3. **`rquest`** — `wreq`-adjacent fork; overlapping maintainership and feature set.
4. **`curl-impersonate`** (CLI bridge) — mature, broad fingerprint coverage, but shelling out per request is unacceptable for hot-path egress and adds a process-spawn attack surface.
5. **Hand-rolled rustls** — full control, multi-week build, ongoing maintenance burden indefinite.

Constraints driving the decision:

- **Per-field control is required**. Persona schema (W3A) treats JA3, JA4, ALPN, cipher order, extension order, GREASE, H2 SETTINGS, WINDOW_UPDATE, and PRIORITY as independent fields. Preset-only libraries (reqwest-impersonate) cannot express the persona.
- **Single-maintainer project**. We cannot afford a multi-month rustls build (option 5) before W3B becomes actionable.
- **Bus factor is real**. `wreq` is a small-team project. Dependency on it must come with a documented escape hatch and a swappable abstraction layer.
- **Audit-grade observability**. Every outgoing request must be loggable with persona id + actual outgoing fingerprint; drift between persona spec and emitted bytes must be detectable in CI and at runtime.

The decision in this ADR is mostly settled (working assumption has been `wreq` since #34 was opened). The point of authoring it is to make the **bus-factor mitigation plan** a concrete, reviewable artifact rather than an implicit assumption — that mitigation list is what unblocks W3B construction (#44).

## Decision

The TLS-fingerprint-aware HTTP client for non-browser egress will be **`wreq`**, accessed exclusively through a `carbonyl-fingerprint::http` trait so the underlying crate is swappable without churning the persona schema or W3B integration call sites.

The selection is contingent on the **bus-factor mitigation plan** below being executed before W3B (#44) construction starts. Every checkbox is a concrete deliverable, not an aspiration.

### Bus-factor mitigation plan (gating W3B)

- [ ] **Vendor pin**: exact `wreq` crate version + git SHA recorded in a `.carbonyl-fingerprint-version` pin file (mirrors the `.carbonyl-runtime-version` pattern from ADR-004). CI fails if the resolved Cargo.lock SHA drifts from the pin.
- [ ] **Cold mirror**: source tarball mirrored to a Gitea release in `roctinam/carbonyl-fingerprint` or `roctinam/carbonyl` (TBD during W3A). The mirror is updated on every pin bump. Air-gapped builds resolve from the mirror.
- [ ] **Abstraction layer**: `carbonyl-fingerprint::http` Rust trait with the persona-binding contract below (see § Persona binding contract). `wreq` is one impl behind this trait; consumers in W3B never name `wreq` directly.
- [ ] **Conformance test suite**: JA3/JA4/H2 fingerprint assertions runnable against any backend. The suite emits requests against a controlled responder (or `tlsx`-style probe) and asserts the wire format matches the persona spec. A second backend (probably `rquest` or hand-rolled) is added solely to validate the trait surface holds when swapped — even if it's never the production choice.
- [ ] **Escape hatch SOP**: documented procedure in `.aiwg/architecture/runbooks/wreq-replacement.md` covering: triggers (upstream archived, CVE without patch, license change), candidate replacements ranked, conformance-suite invocation, expected migration cost.

### Library selection rationale

| Library | Per-field control | Maint | License | Verdict |
|---|---|---|---|---|
| `wreq` | ✅ JA3/JA4/H2 explicit | Small team, active | MIT/Apache-2.0 dual | **Selected** |
| `reqwest-impersonate` | ❌ preset-only | Active | MIT | Rejected — preset-only blocks persona schema |
| `rquest` | ✅ similar to wreq | Small team, overlapping | MIT/Apache-2.0 | Held as escape-hatch candidate |
| `curl-impersonate` (CLI) | ✅ broad | Active | MIT | Rejected — process-spawn cost on hot path |
| Hand-rolled rustls | ✅ full | Self | — | Rejected for v0.x — multi-month build |

### Persona binding contract

A `Persona` (W3A schema) maps to the `http` trait via the following fields. The trait surface is normative; the `wreq` impl is one realization.

| Persona field | Trait method | Wire-level effect |
|---|---|---|
| `tls.ja3`, `tls.ja4` | `set_tls_fingerprint(JA3, JA4)` | ClientHello extension order, cipher list, extensions, GREASE pattern |
| `tls.alpn` | `set_alpn(["h2", "http/1.1"])` | ALPN advertised protocols |
| `tls.cipher_order` | folded into JA3 | rustls cipher preference list |
| `http2.settings` | `set_h2_settings(SETTINGS frame)` | initial SETTINGS frame |
| `http2.window_update` | `set_h2_window_update(value)` | initial WINDOW_UPDATE |
| `http2.priority` | `set_h2_priority(weight, deps)` | PRIORITY frame on stream 1 |
| `headers.user_agent` | `set_default_header("User-Agent", ...)` | UA must agree with browser persona |
| `headers.accept_language` | `set_default_header("Accept-Language", ...)` | matches browser's `navigator.languages` |
| `headers.sec_ch_ua_*` | `set_default_header("sec-ch-ua-...", ...)` | Client Hints aligned with persona |

### Audit / observability

- **Per-request log**: structured record of `{persona_id, ja3_actual, ja4_actual, server_cert_san, response_status, request_id}` emitted to the audit sink on every request. Sink is pluggable; default writes to a rotating file under `~/.local/state/carbonyl-fingerprint/audit/`.
- **Drift detector**: synchronous assertion on every request that the emitted `ja3_actual` matches `persona.tls.ja3`. Failure mode is environment-gated:
  - Dev / CI: fail-closed (raise, abort request).
  - Prod: warn-only, log to audit sink, return the response (so a transient mismatch doesn't take down a real consumer).
- **CI conformance gate**: the conformance suite (above) runs in CI on every PR touching `carbonyl-fingerprint` or `wreq`-adjacent code. Drift between the trait spec and the wire output blocks merge.

#### Phase 1 → Phase 2 transition (W3B #44, #75 — landed)

The audit log evolved in two passes:

**Phase 1** (httpx + stdlib SSL): every audit row carried `ja4_actual = "phase1-httpx-stdlib-ssl"`. The sentinel was intentional — `httpx` uses Python's stdlib SSL backend whose JA4 reflects neither the persona's emulation target nor any browser. The sentinel told downstream log consumers "this row predates real fingerprint emission" without forcing them to special-case absent values.

**Phase 2** (wreq + BoringSSL): the audit row gained a `transport` field that tags each row as `wreq` or `httpx-fallback`. The transition is auto-detected at `EgressClient` construction:

- `carbonyl_wreq` native module importable → route through wreq → audit row `transport: wreq`, `ja4_actual` carries the captured value
- Module absent → silent fallback to httpx → audit row `transport: httpx-fallback`, `ja4_actual` keeps the phase1 sentinel

The fallback path preserves Phase 1 behavior exactly so the rollback story is "uninstall the native module"; STRICT mode in the fallback path still raises on the (always-present) sentinel mismatch.

**Layer separation**: Layer 1 (#62, #79) tests assert what the trait setters captured; Layer 2 (#82) drives a real wreq client through a localhost TLS responder and asserts the actual ClientHello + h2 SETTINGS bytes match the persona. The Layer 2 test crate's documented per-fixture gap lists (e.g. `wire.ja4`, `wire.h2_settings` for Chrome 147 against the Chrome 137 emulation preset) are load-bearing — drift in any direction is a regression.

**Cross-references**:

- `conformance.yml` (`.gitea/workflows/conformance.yml`) gates PRs touching fingerprint/wreq/egress paths on the `--features carbonyl-wreq/python` workspace test path — covers the cdylib build that maturin produces.
- `WreqTransport` (`src/carbonyl_agent/wreq_transport.py`) is the Python httpx.BaseTransport wrapper.
- `send_request` (`crates/carbonyl-wreq/src/python.rs`) is the PyO3 entry point with an embedded multi-threaded tokio runtime.

## Consequences

### Positive

- **Persona schema becomes wire-truthful**: the same `Persona` record drives both browser and non-browser egress, eliminating the JA3-mismatch detector signal that motivates Phase 3.
- **Library is swappable**: the `http` trait + conformance suite means an upstream `wreq` failure is a measured, scoped migration (estimated 1–2 weeks given the escape-hatch SOP), not a six-month rebuild.
- **Auditable by construction**: per-request log + drift detector means consumers cannot accidentally bypass the persona, and a misconfigured persona is loud at runtime instead of silently exfiltrating mismatched fingerprints.
- **Aligned with ADR-004 vendor-pin pattern**: `.carbonyl-fingerprint-version` mirrors `.carbonyl-runtime-version`, so dependency-version discipline is consistent across the project.

### Negative

- **`wreq` bus factor is genuinely small**. The mitigation plan reduces but does not eliminate the risk. If the project is archived without a forked maintainer, the escape hatch costs ~1–2 weeks of focused work.
- **Conformance suite is non-trivial**. Asserting JA3/JA4 from inside the test process requires either a controlled TLS responder or external probes (`tlsx`, `tshark`-based capture). Initial suite build is part of W3A scope (#43) and adds ~1 week to that issue's estimate.
- **Audit sink adds I/O**. Default file sink is fine for low-volume consumers; high-volume consumers (e.g. an RSS poller hitting hundreds of feeds) need a rate-limited or aggregating sink. Out of scope for v0.x; documented as future work.
- **Drift detector in fail-closed mode breaks dev iteration when the persona spec lags rustls upgrades**. Mitigation: persona spec versioning + a "persona-compat" lint step in CI that rejects out-of-date persona files before the request layer sees them.
- **`rquest` as the second-impl test backend is not free**. Adding a second backend purely for trait validation costs CI time and one developer-week of integration. We accept this as the price of trait correctness.

### Neutral

- The trait surface lives in `carbonyl-fingerprint` (W3A, #43); `wreq` integration code lives in W3B (#44). Layout is independent of this ADR.
- License compatibility: the `wreq` stack transitively pulls in GPL-3.0 `wreq-util`. The project is AGPL-3.0-only, and GPLv3 §13 makes GPL-3.0 and AGPL-3.0 compatible (attribution required, aggregated in `THIRD_PARTY_LICENSES.txt`; #99). #100 tracks replacing `wreq-util` with an in-house preset registry.

## Alternatives Considered

- **`reqwest-impersonate`** (Option 2): rejected — preset-only fingerprints cannot express per-persona TLS variation, which is the entire point of W3A's per-field schema. A persona that says "Chrome 120 with cipher order modified for resumption" cannot be encoded as a preset name.
- **`rquest`** (Option 3): held as the escape-hatch candidate. Functionally similar to `wreq` with overlapping maintainership; if `wreq` archives, `rquest` is the most likely first-replacement target. Including it as the second backend in the conformance suite is the actionable form of this fallback.
- **`curl-impersonate`** (Option 4): rejected for production hot path. The per-request `fork+exec` cost (~10 ms overhead, plus IPC marshalling for headers/body) is unacceptable for an SDK that may issue dozens of requests per session. Acceptable for one-off CLI debugging or as an external "ground truth" probe inside the conformance suite.
- **Hand-rolled rustls** (Option 5): rejected for v0.x — multi-month build with indefinite ongoing maintenance, blocks W3B by ~6 months. Reconsider if `wreq` and `rquest` both fail simultaneously.
- **Use the same rustls/BoringSSL config as the Carbonyl Chromium fork** (post-survey idea): rejected — Chromium's network stack is not exposed as a consumable library. Trying to extract it would amount to building option 5 on top of a moving Chromium target.

## Acceptance criteria for ADR sign-off

- [x] Sign-off recorded by: architect, security architect, eng lead (sole maintainer acting in all three roles, 2026-05-08).
- [x] Status flipped to `Accepted` and dated.
- [x] This ADR linked from #34 (Phase 3 EPIC) as "construction unblocked."
- [x] All bus-factor mitigation checklist items above converted to filed issues blocking #44 (so they cannot be silently skipped during W3B kickoff). Tracked in #59–#63 (filed alongside ADR acceptance).

## References

- Issue #42 — this ADR's tracking issue
- Issue #34 — Phase 3 EPIC (Owned fingerprint registry)
- Issue #43 — W3A carbonyl-fingerprint crate foundation (persona schema + sampler + validator)
- Issue #44 — W3B wreq egress integration + persona binding + audit
- Issue #45 — W3C Persona → Chromium applier (the browser side; symmetric concern)
- ADR-004 — vendor-pin pattern (`.carbonyl-runtime-version`) mirrored by `.carbonyl-fingerprint-version`
- `wreq` crate — pin SHA recorded in mitigation deliverable (TBD during W3A)
- JA3 spec: https://github.com/salesforce/ja3
- JA4 spec: https://github.com/FoxIO-LLC/ja4
