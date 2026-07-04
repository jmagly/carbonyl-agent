# Doc-Sync Audit — 2026-07-04 (code→docs)

**Direction:** code-to-docs (code is source of truth).
**Scope:** docs that reflect the 2026.7.0 parity-release code/config changes
(runtime pin, Docker fallback, license audit, CI workflows, version). Derived
from `git diff b1baff0..HEAD`. Two bounded auditor lanes (architecture,
deployment); detailed notes in `.aiwg/working/doc-sync/{architecture,deployment}-lane.md`.

## Applied (high-confidence, this pass)

| File | Fix |
|------|-----|
| `README.md` | Badge M147→M148; runtime-compat matrix: `2026.5.x`→`2026.7.x`, dropped the "current **and prior** runtimes" CI claim and the dead `runtime-dd69bef0ea4b2512` link + false backwards-compat-CI row → "Best-effort, not in CI (#127)" |
| `pyproject.toml` | Wheel-attribution comment: "aggregates carbonyl-fingerprint only / GPL finding unresolved" → full-workspace incl. GPL-3.0 wreq-util (AGPL-compatible), #99 |
| `adr-003` | Docker fallback described as automatic → **opt-in (`CARBONYL_ALLOW_DOCKER=1`) + digest-pinned** (step 4 + Positive "graceful first-run" bullet) |
| `adr-004` | "No SHA256 verification (known gap)" → **Resolved** (`_verify_checksum` verifies before extraction); `LATEST_TAG = "runtime-latest" (line 31)` → `runtime_pin.LATEST_SENTINEL` |
| `adr-005` | "MIT/Apache-2.0 dual … no license escape hatch" → GPL-3.0 `wreq-util` is AGPL-compatible (GPLv3 §13), aggregated in `THIRD_PARTY_LICENSES.txt` (#99); #100 tracks removal |
| `software-architecture-doc.md` | References "ADR-001 through ADR-004" → ADR-005; G8 Docker fallback → opt-in + digest |
| `ci-cd-scaffold.md` | Nightly E2E moved from "Future Enhancements" → noted implemented (`e2e.yml` cron) |
| `release-runbook.md` | §1 version-bump note + §2 tag example: SemVer (`0.1.0`) → **CalVer** (`2026.7.0`, `YYYY.M.PATCH`) |

## Deferred (human-review / not auto-fixed)

- **SAD G1/G3 "Python-only surface / no compiled extensions / only pexpect+pyte"** —
  flagged as drifting, but **still correct for the current wheel**: the native
  `carbonyl_wreq` module is dev-only and does not ship in the wheel until #88
  lands. Revisit when #88 bundles the cdylib. (MEDIUM)
- **adr-004 Decision section** ("hosted on Gitea releases … downloaded") — now
  GitHub-assets-first with Gitea fallback (install.py) + semantic default tag.
  Core Gitea-distribution decision still holds; the GitHub-first primary is an
  additive later change. Worth a follow-up ADR note rather than an inline rewrite
  of the historical decision. (MEDIUM)
- **adr-003:42 / SAD line refs** (`browser.py 227–248` → `~461–482`) — line-number
  drift only. (MEDIUM, low value)
- **adr-005:51/53 library table** — could footnote that `wreq`'s own MIT/Apache
  license omits the GPL-3.0 `wreq-util` transitive (the :122 Neutral bullet now
  covers it). (MEDIUM)
- **adr-005:94 "Chrome 147" fixture reference** — persona corpus is Chrome 148;
  verify against the Layer-2 test crate before editing (fixture may intentionally
  lag the runtime). (LOW-MEDIUM)
- **adr-004:26 "v0.1.0, single maintainer"** project-stage marker. (LOW)

## Verified clean (NOT stale)

- No `fathyb/carbonyl` in any architecture/deployment doc except the intended
  historical README acknowledgment.
- No reference to the old runtime hash `8f070d2720157bd0` in any doc.
- No MIT-vs-GPL "wheel conflict" framing remains in the audited docs (the stale
  pyproject comment was the last one — now fixed).

## Validation
- `git diff --stat` bounded to the doc files above; no code touched.
- Auditors ran read-only; fixes applied by the orchestrator.
