# Carbonyl-Agent ↔ Carbonyl Runtime Parity Audit & Release Plan

**Date:** 2026-07-04
**Author:** parity audit (maintainer: Joseph Magly)
**Scope:** Audit `carbonyl-agent` against the current upstream `roctinam/carbonyl`
release, plan the updates/refactors needed for parity, and fold in the
outstanding release-blocker + anti-bot backlog.
**Status:** DRAFT for maintainer scoping — no code changed yet.

---

## 0. Executive summary

`carbonyl-agent` (SDK version `2026.5.3`) is pinned to a runtime that is **~12
alpha releases and ~6 runtime-hash cuts behind** upstream, and the pin file is
**internally inconsistent** — the semantic tag and the immutable hash point at
different runtimes. That single inconsistency splits the project into two
runtimes at once (SDK install vs QA-runner/CI), and the older of the two is
missing the exact runtime capability (`alpha.13` input-FFI widening) that the
top-priority outstanding work (anti-bot login flows, #125) depends on.

**Keystone action:** bring the runtime pin to `v0.2.0-alpha.17` /
`099874f855c74a61` in true lockstep. Everything else — the input-FFI SDK
rework, the now-unblocked release-blocker cluster, and the #125 acceptance
environment — sits downstream of this one change.

| Axis | Pinned (agent) | Current (upstream) | Delta |
|------|----------------|--------------------|-------|
| Source release | tag says `v0.2.0-alpha.17` | `v0.2.0-alpha.17` (2026-07-01) | tag OK |
| Runtime hash | `8f070d2720157bd0` (**alpha.5/6, M148, 2026-05-18**) | `099874f855c74a61` (2026-07-01) | **~6 cuts stale** |
| Fingerprint corpus | Chrome 148 + Firefox 150, wreq 5.3.0 | (unchanged upstream) | current |
| Docker fallback | `fathyb/carbonyl` | `ghcr.io/jmagly/carbonyl` (alpha.10) | **stale** |

---

## 1. Finding F1 — Runtime pin split-brain (CRITICAL, bug)

`.carbonyl-runtime-version` currently declares:

```
runtime-tag=v0.2.0-alpha.17          # resolves to CURRENT runtime 099874f855c74a61
runtime-hash=8f070d2720157bd0        # this is the ALPHA.5/6 (M148) runtime, 2026-05-18
```

The last commit (`b1baff0 chore(runtime): pin agent to carbonyl alpha17`)
bumped the **tag** but left the **hash** at its old value. These two anchors now
resolve to different runtimes, and different consumers read different anchors:

| Consumer | Reads | Pulls runtime |
|----------|-------|---------------|
| `carbonyl-agent install` (default) | `runtime-tag` via `resolve_default_tag()` | **alpha.17** `099874f855c74a61` (current) |
| `docker/qa-runner/build.sh` | `runtime-hash` (grep) | **alpha.5/6** `8f070d…` (stale) |
| `.gitea/workflows/build-qa-runner.yml` | `runtime-hash` (grep) | **alpha.5/6** `8f070d…` (stale) |

**Impact:** the QA-runner image — the trusted-input / X11-Ozone environment where
the #125 anti-bot acceptance flows are meant to run — is built on a runtime that
**predates the alpha.13 input-FFI widening**. So the environment cannot emit
right-click, key modifiers, or multi-byte input regardless of SDK changes. This
is not cosmetic drift; it silently caps what the trusted path can do.

**Fix:** set `runtime-hash=099874f855c74a61`, keep `runtime-tag=v0.2.0-alpha.17`,
and re-verify the qa-runner rebuild (the pin change auto-triggers
`build-qa-runner.yml`). Confirm the alpha.17 release actually publishes an
`runtime-x11-099874f855c74a61` tarball (it does — see the 2026-07-01 x11 release).

---

## 2. Finding F2 — SDK trusted-input path predates the widened input FFI (HIGH)

Upstream `alpha.13` (2026-06-22) widened the input FFI: mouse button field
(right-click, #199), multi-byte UTF-8 input (Cyrillic/CJK, #178/#217), key
modifier mask (Shift+Tab, Ctrl/Alt/Meta, #237), and TAB→VKEY_TAB focus (#169).
The SDK has not caught up:

| Capability | Current SDK state | Gap |
|------------|-------------------|-----|
| Left click | `browser.click()` → SGR `\x1b[<0;c;rM` | OK |
| **Right / middle click** | none — no `right_click()`; SGR hardcodes button 0 | **missing** |
| **Key modifiers** (Shift+Tab, Ctrl/Alt/Meta) | `uinput_emitter` has no modifier-combo API | **missing** |
| **Multi-byte / CJK / Cyrillic input** | `uinput_emitter` maps ASCII only (`_PRINTABLE_*`); PTY path sends `text.encode("utf-8")` but the trusted (uinput) path can't | **missing on trusted path** |
| TAB focus advance | `send_key` exists; needs verification against widened FFI | **verify** |

**Impact:** the trusted-input path (`input_backend="uinput"`, `isTrusted=true`)
is precisely the mechanism #125 relies on for Cloudflare/CAPTCHA clicks and form
fills — and it currently cannot right-click, hold modifiers, or type non-Latin
text. This is the SDK-side half of the #125 epic.

**Fix (after F1):** extend `browser.py` (`right_click()`, modifier-aware
`send_key`/`type_text`, IME/multi-byte text entry) and `uinput_emitter.py`
(BTN_RIGHT/BTN_MIDDLE, modifier hold/release, Unicode → keysym path) against the
alpha.17 FFI. Ties directly to #45 (persona→Chromium applier) and #125.

---

## 3. Finding F3 — Runtime feature surface not exposed by the SDK (MEDIUM, enhancement)

Runtime capabilities added since the pinned build that the SDK does not surface:

| Runtime feature | Since | SDK exposure | Opportunity |
|-----------------|-------|--------------|-------------|
| `--dump-text` / `=accessibility` / `=raw-dom` + PDF text | alpha.7-8, .17 | none (SDK screen-scrapes via pyte) | clean headless text/AX extraction API; replaces fragile PTY scraping for text-only + LLM-pipeline use |
| Nav-failure **exit code 6** | alpha.7 | not mapped | deterministic navigation-error signalling |
| `--page-height` full-page capture | alpha.12 | none | full-page screenshot/inspection via `screen_inspector` |
| `--chrome-rows=N`, invert-colors | alpha.12 | none | minor UX passthrough |
| `--framebuffer` `/dev/fb0` backend | alpha.10 (dormant upstream) | none | relevant to qa-runner VM/kiosk modes; track upstream cycle 2 |
| `--carbonyl-cookie-flush-interval-ms` | alpha.4 | **used** (`browser.py:439`) | already wired ✓ |

`--dump-text` is the highest-value addition — a first-class, non-scraping text
extraction surface that pairs naturally with the cookie-import + persona work.

---

## 4. Finding F4 — Distribution / supply-chain drift (MEDIUM)

- **Docker fallback stale:** `install.py:50` and CLAUDE.md binary-search-order
  reference `docker run fathyb/carbonyl`. Upstream publishes
  `ghcr.io/jmagly/carbonyl` since alpha.10. Update both.
- **No GPG verification:** `install.py` verifies SHA-256 checksums (good) but
  upstream alpha.15 added per-asset **GPG signatures** + a signing key
  (`SIGNING.md`, #250). Add optional signature verification — consistent with
  this repo's own `ci-action-pinning` / `dep-source` security posture.
- **Native install packages / container image** (`.deb`/`.rpm`/`.AppImage`,
  `ghcr.io`) exist upstream (alpha.9-10) — document them as install options in
  README alongside `carbonyl-agent install`.

---

## 5. Outstanding backlog — status refreshed against upstream (2026-07-04)

| # | Title | Type | Upstream-refreshed status |
|---|-------|------|---------------------------|
| **98** | Runtime tagged release + multi-arch | release-blocker | **Substantially unblocked.** Upstream now ships tagged releases, macos-arm64 runtime (alpha.7+), native packages (alpha.9), container image (alpha.10), GPG signing (alpha.15). Remaining: linux-aarch64 runtime (upstream carbonyl#67/#116, still pending) + **agent-side** multi-arch CI matrix (`install.py` already handles the macOS triple). |
| **88** | pip-install for `carbonyl-wreq` native module | release-blocker | Unblocked by F1 (stable tarballs now current). Actionable. |
| **99** | AGPL wheel wreq/wreq-util attribution | release-blocker | Small; couples to #100. Actionable this release. |
| **12** | PyPI trusted publisher (OIDC) | release-blocker | **Maintainer-gated** (web-UI setup); not code. |
| **125** | EPIC anti-bot login flows (Google/CF/kubelogin/CAPTCHA) | epic | Depends on **F1 + F2**, #45, #34, upstream carbonyl#57 (isTrusted, "proven host-side"). F1+F2 are the agent-side keystone. |
| 45 | Persona → Chromium applier (Client Hints) | enhancement | Feeds #125 L2/L3; unblocked after F1. |
| 34 | Owned fingerprint registry (carbonyl-fingerprint) | epic | Phase 3; parallel track. |
| 100 | Replace wreq-util with in-house registry | epic | License/provenance track; pairs with #99. |
| 118 | Chromium-family persona variants | enhancement | Next fingerprint iteration. |
| 119 | mobile/safari captures | testing | **HITL-blocked** on physical devices. |

---

## 6. Recommended release plan

Three tracks. **Track A is the keystone and should ship first** — it is low-risk,
closes a real bug, and unblocks both parity and #125.

### Track A — Runtime parity (this release, keystone)
1. **F1** Fix pin lockstep → `runtime-hash=099874f855c74a61`; verify qa-runner +
   e2e + CI green on the new runtime. *(closes the split-brain; touches
   `.carbonyl-runtime-version` + triggers qa-runner rebuild)*
2. **F4a** Update Docker fallback `fathyb/carbonyl` → `ghcr.io/jmagly/carbonyl`
   in `install.py` + CLAUDE.md/AIWG.md.
3. **F4b** Add optional GPG signature verification to `install.py` (alpha.15 keys).
4. **#98 agent-side** multi-arch CI matrix (linux-x86_64 + macos-arm64 now;
   linux-aarch64 gated on upstream). Update the #98 checklist to reflect the
   upstream items now satisfied.
5. Cut a release (CalVer, e.g. `2026.7.0`) documenting the runtime bump.

### Track B — Trusted-input FFI rework (this or next release; enables #125)
6. **F2** `right_click()` / middle-click; modifier-aware key entry; multi-byte
   (CJK/Cyrillic) input on the uinput trusted path; TAB-focus verification.
7. Wire to #45 (persona applier) and stand up the #125 acceptance harness on the
   *corrected* qa-runner (post-F1). Close #125 flows individually as they pass.

### Track C — Release-blocker cleanup (parallel, maintainer-gated items flagged)
8. **#99** wheel attribution for wreq/wreq-util (small).
9. **#88** pip-install native `carbonyl-wreq` (now unblocked by F1).
10. **#12** PyPI OIDC — *maintainer web-UI action*; flip `ENABLE_PYPI_PUBLISH`.
11. **#100** in-house preset registry (removes GPL wreq-util) — larger, own line.

### Track D — Enhancements (opportunistic / next)
12. **F3** `--dump-text`/`=accessibility`/PDF extraction SDK API; `--page-height`
    full-page capture. #118 Chromium-family personas. #119 deferred (HITL).

---

## 7. Risks, verification, open decisions

- **F1 blast radius:** the pin change triggers a qa-runner image rebuild and
  changes what CI/e2e pull. Must verify the new runtime downloads + checksum
  resolves and the trusted-input smoke path still renders before declaring done.
  Reversible (pin-file edit) but coordinated — matches the repo's prior routine
  runtime-bump commits.
- **F2 depends on F1:** don't rework input against an FFI the qa-runner doesn't
  yet run.
- **Maintainer-gated:** #12 (PyPI OIDC web setup), #119 (physical-device
  captures) cannot be automated here.
- **Open decision (for maintainer):** which tracks land in *this* release.
  Recommended minimum for "parity + fixes some outstanding": **Track A + #99**,
  with Track B opening immediately after F1 lands.

---

## Appendix — upstream runtime-hash timeline

`8f070d2720157bd0` (a5/6, M148, **← agent pinned here**) → `e38a57d2afdbe7e7`
(a7: --dump-text, macOS, Rust-in-hash) → `283ca65ffeeaa2dc` (a8/9: AX tree,
native packages) → `1d6058c2494e7a5d` (a10: container image, framebuffer) →
`0343241e9e449cd5` (a11) → … → **`099874f855c74a61`** (a17, current, 2026-07-01).

Input-FFI widening (right-click / modifiers / multi-byte / TAB) landed at
**alpha.13** — after the pinned hash, before current.
