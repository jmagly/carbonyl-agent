# Iteration 001 Plan — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09
- **Iteration length**: 2 weeks
- **Phase**: Construction (pre-v0.1.0 release hardening)
- **Owner**: Joseph Magly (solo)

## Sprint Goal

> Establish CI baseline, supply-chain integrity, and expanded test coverage so v0.1.0 can ship to PyPI with confidence.

## Success Criteria (Definition of Done for the Iteration)

- CI pipeline green on Gitea Actions and GitHub Actions across the declared support matrix.
- Package coverage ≥ 80% line / ≥ 70% branch; `install.py` and `daemon.py` ≥ 90% line.
- `mypy --strict` passes on `src/carbonyl_agent/`.
- SHA256 verification enforced in `install.py`.
- Self-review checklist completed for every story (see §5).
- `CHANGELOG.md` scaffolded with `0.1.0` entry ready for release.

## Backlog

| ID | Title | Acceptance Criteria | Estimate | Owner |
|----|-------|---------------------|----------|-------|
| **US-001** | CI workflow on Gitea + GitHub Actions | `.gitea/workflows/ci.yml` and `.github/workflows/ci.yml` run lint, type-check, unit, integration, smoke across py3.11/3.12/3.13 on ubuntu-latest; macos-latest covered for 3.12 only. Green on `main`. | M | Maintainer |
| **US-002** | SHA256 verification in `install.py` | `install.py` downloads `SHA256SUMS` alongside the tarball, verifies, and aborts on mismatch. `--checksum <hex>` override for pinning. Unit tests cover match, mismatch, and missing-file cases. Threat T1 mitigated. | M | Maintainer |
| **US-003** | Daemon integration tests + socket permission hardening | New `tests/integration/test_daemon.py` spins up a daemon against a stub browser duck-type, exercises every JSON command, validates error envelopes, and asserts socket mode `0600` and parent dir `0700`. Threat T3 mitigated. | L | Maintainer |
| **US-004** | Session integration tests + name validation | `SessionManager` rejects names outside `^[A-Za-z0-9_.-]{1,64}$`. Tests cover create/list/delete/exists, fork and snapshot metadata round-trip, path-traversal rejection. Threat T4 mitigated. | M | Maintainer |
| **US-005** | `screen_inspector` unit + property tests | Line/region/row/col semantics covered by table-driven tests. `hypothesis` strategy feeds random rows and validates invariants. Coverage ≥ 95% for the module. | S | Maintainer |
| **US-006** | Type hint pass + `mypy --strict` clean | Add missing annotations to `browser.py`, `daemon.py`, `install.py`, `session.py`. `mypy --strict src/carbonyl_agent/` exits 0. Added to CI. | M | Maintainer |
| **US-007** | Dependency pinning + `pip-audit` in CI | `constraints.txt` generated; CI installs from constraints; `pip-audit` runs and fails on known CVEs. Threat T6 mitigated. | S | Maintainer |
| **US-008** | Docker fallback digest pin + opt-in gate | Docker fallback requires `CARBONYL_ALLOW_DOCKER=1`; pulls by digest constant in code. Threat T7 mitigated. | S | Maintainer |
| **US-009** | `CHANGELOG.md` scaffolding (Keep a Changelog format) | Root `CHANGELOG.md` with `Unreleased` and `0.1.0` sections; README link added; release runbook references it. | S | Maintainer |
| **US-010** | Release checklist doc | `.aiwg/deployment/release-runbook.md` linked from `CONTRIBUTING.md` (created as needed). Dry-run performed end-to-end on TestPyPI. | S | Maintainer |

**Total**: 10 stories, mix S/M/L. Parallelism map: US-001 unblocks US-006/US-007; US-002, US-003, US-004, US-005 are independent and can proceed in any order; US-008 and US-009 are trivial polish; US-010 is final.

## Definition of Done (per story)

- Implementation merged to `main` on Gitea (origin) and mirrored to GitHub.
- Unit + integration tests added where applicable.
- CI green on both providers.
- Coverage thresholds maintained.
- `mypy --strict` clean.
- Self-review checklist completed (solo substitute for peer review):
  - [ ] Story acceptance criteria met
  - [ ] Tests cover happy path + at least one failure mode
  - [ ] Docstrings updated
  - [ ] README or CHANGELOG updated if user-visible
  - [ ] ADR written if a design decision was made
  - [ ] No new `TODO`/`FIXME` without an issue link

## Risks

| Risk | Mitigation |
|------|-----------|
| Gitea Actions runner availability | Fall back to GitHub Actions as the required gate; Gitea treated as advisory until stable |
| `mypy --strict` uncovers deep typing issues in PTY/pyte integration | Scope `--strict` to modules that are clean; use `# type: ignore[code]` with issue links elsewhere |
| SHA256 rollout requires coordinated change in `roctinam/carbonyl` release process | Publish `SHA256SUMS` manually for existing tags before enforcing verification |
| Solo bandwidth shortfall | Defer US-008 and US-010 to iteration 002 if needed; US-001, US-002, US-003 are the non-negotiable release blockers |

## Dependencies

None external. All stories are internal to `carbonyl-agent`. US-002 requires the `roctinam/carbonyl` release process to publish `SHA256SUMS` — coordinated within the same maintainer account so not a blocking external dependency.

## Exit Review

At iteration close: run `project-status`, verify success criteria, update `CHANGELOG.md`, and proceed to release runbook.
