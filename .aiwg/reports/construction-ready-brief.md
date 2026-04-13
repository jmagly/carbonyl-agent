# Construction Ready Brief — carbonyl-agent

**Project**: carbonyl-agent
**Date**: 2026-04-09
**Status**: CONSTRUCTION READY
**Version target**: v0.1.0 (first public release)

## Executive Summary

`carbonyl-agent` is a Python ≥3.11 automation SDK for the [Carbonyl](https://git.integrolabs.net/roctinam/carbonyl) headless browser. It wraps the Carbonyl native binary via pexpect PTY + pyte terminal emulation, providing `CarbonylBrowser`, `SessionManager`, `ScreenInspector`, and a `DaemonClient` + Unix-socket daemon server for long-lived browser instances. A `carbonyl-agent install` CLI downloads the platform-specific runtime tarball from Gitea releases at `roctinam/carbonyl`.

The codebase (~2.3k LoC, 6 modules) was recently extracted from the parent `roctinam/carbonyl` repo's `automation/` directory. It is MIT-licensed, dual-hosted on Gitea (primary, `roctinam/carbonyl-agent`) and GitHub (mirror, `jmagly/carbonyl-agent`), and has not yet shipped to PyPI. The maintainer (Joseph Magly, solo) has elected to adopt full SDLC rigor before public release to produce a durable foundation library with minimal downstream blast radius.

Both LOM and ABM gates passed. The architecture baseline documents the existing implementation rather than planning from scratch, which reduces architectural risk but surfaces technical debt (no CI, no SHA256 verification on runtime tarball download, smoke-tests-only coverage). Iteration 1 addresses these gaps directly.

## Artifact Index

| Artifact | Path | Status |
|----------|------|--------|
| Project Intake | `.aiwg/intake/project-intake.md` | Baselined |
| Solution Profile | `.aiwg/intake/solution-profile.md` | Baselined |
| Option Matrix | `.aiwg/intake/option-matrix.md` | Baselined (full-rigor) |
| LOM Gate Report | `.aiwg/reports/lom-gate-report.md` | PASS |
| Use Cases (7) | `.aiwg/requirements/use-cases.md` | Baselined v1.0 |
| User Stories (12) | `.aiwg/requirements/user-stories.md` | Baselined v1.0 |
| NFR Register (18) | `.aiwg/requirements/nfr-register.md` | Baselined v1.0 |
| Software Architecture Doc | `.aiwg/architecture/software-architecture-doc.md` | Baselined v1.0 |
| ADR-001 PTY + pyte terminal emulation | `.aiwg/architecture/adr-001-pty-pyte-terminal-emulation.md` | Accepted |
| ADR-002 Unix socket daemon | `.aiwg/architecture/adr-002-unix-socket-daemon.md` | Accepted |
| ADR-003 Runtime binary discovery order | `.aiwg/architecture/adr-003-runtime-binary-discovery-order.md` | Accepted |
| ADR-004 Gitea release runtime distribution | `.aiwg/architecture/adr-004-gitea-release-runtime-distribution.md` | Accepted |
| Test Strategy | `.aiwg/testing/test-strategy.md` | Baselined v1.0 |
| Threat Model (STRIDE) | `.aiwg/security/threat-model.md` | Baselined v1.0 |
| ABM Gate Report | `.aiwg/reports/abm-gate-report.md` | PASS |
| Iteration 001 Plan | `.aiwg/planning/iteration-001-plan.md` | Ready |
| Team Profile | `.aiwg/team/team-profile.md` | Ready |
| CI/CD Scaffold | `.aiwg/deployment/ci-cd-scaffold.md` | Ready |
| Release Runbook | `.aiwg/deployment/release-runbook.md` | Ready |

## Key Architecture Decisions

1. **ADR-001 — PTY + pyte terminal emulation**: Carbonyl exposes no native automation IPC, so the SDK drives it via pexpect PTY and parses rendered output with pyte. Zero runtime binary modifications required; tight coupling to terminal output format accepted.
2. **ADR-002 — Unix socket daemon**: Daemon mode uses Unix domain sockets with line-delimited JSON. Zero external deps, filesystem permissions as access control, local-only by design.
3. **ADR-003 — Runtime discovery order**: `CARBONYL_BIN` → `~/.local/share/carbonyl/bin/<triple>/` → `$PATH` → Docker fallback. Installer-precedence-over-PATH is intentional.
4. **ADR-004 — Gitea release distribution**: ~75 MB runtime tarballs hosted on Gitea releases (tagged `runtime-<hash>`), not bundled with the pip package. Enables independent runtime updates; surfaces the SHA256 verification gap as a tracked follow-up.

## Top Risks to Watch

| # | Risk | Severity | Mitigation | Owner |
|---|------|----------|------------|-------|
| T1 | Tampered runtime tarball — no SHA256 verification in `install.py` | **HIGH** | US-002: add SHA256 manifest + verification before extract | Maintainer |
| — | Bus factor 1 — single contributor | MEDIUM | CHANGELOG + ADR decision log + release runbook provide durable context for future contributors | Maintainer |
| T6 | Dependency supply-chain (pexpect, pyte) | MEDIUM | Pin versions, Dependabot alerts via GitHub mirror | Maintainer |
| T3 | Unix socket permission bypass in daemon | MEDIUM | Enforce 0600 socket perms + PID check on connect | Maintainer |
| — | Cross-platform binary matrix drift | MEDIUM | CI matrix (py3.11–3.13 × linux/macos) + runtime tarball manifest per triple | Maintainer |

## Iteration 1 Sprint Goal

> **"Establish CI baseline, supply-chain integrity, and expanded test coverage so v0.1.0 can ship with confidence."**

Ten stories (US-001..US-010) spanning: CI workflow (Gitea Actions + GitHub Actions), SHA256 verification in `install.py`, daemon integration tests, session integration tests, `screen_inspector` unit tests, mypy-strict type-check pass, CHANGELOG scaffolding, release runbook validation, PyPI trusted-publisher setup, README example verification.

**Definition of Done**: CI green on all matrix cells · ≥80% line / ≥70% branch coverage · mypy-strict clean · ADRs updated if design changed · CHANGELOG reflects all merged work · smoke test passes in clean venv after `pip install`.

## First Steps for Construction

1. **Review and sign off on SAD** (`.aiwg/architecture/software-architecture-doc.md`) — confirm it reflects the intended design, not just the current implementation.
2. **Scaffold CI pipelines** from `.aiwg/deployment/ci-cd-scaffold.md`:
   - Create `.gitea/workflows/ci.yml` (primary)
   - Create `.github/workflows/ci.yml` (mirror)
3. **Address T1 (HIGH)** — implement SHA256 verification in `src/carbonyl_agent/install.py` per US-002. This is the highest-priority security item before any public release.
4. **Begin Iteration 1 stories** in priority order — start with CI (unblocks everything downstream) then SHA256 verification, then test expansion.
5. **Set up PyPI trusted publisher** on the GitHub mirror for tag-triggered publishes per `release-runbook.md`.

## Pipeline Execution Summary

- **Entry mode**: description (option-matrix updated mid-flight to full rigor)
- **Phases completed**: Intake · LOM Gate (PASS) · Elaboration · ABM Gate (PASS) · Construction Prep · Construction Ready Brief
- **Artifacts generated**: 19 baselined documents
- **Gates**: LOM PASS, ABM PASS
- **Known follow-ups**: T1 supply-chain mitigation scheduled in Iteration 1; mypy strict pass scheduled in Iteration 1

---

**Status**: Construction may begin.
