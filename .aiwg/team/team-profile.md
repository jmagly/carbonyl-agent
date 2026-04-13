# Team Profile — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09

## Members

| Name | Role(s) | Availability | Contact |
|------|---------|--------------|---------|
| Joseph Magly | Maintainer, Lead Developer, Release Manager, Security Owner, QA | Part-time, asynchronous | Gitea `@jmagly` (git.integrolabs.net), GitHub `@jmagly` |

`carbonyl-agent` is a solo-maintained project. Joseph holds every role simultaneously. All responsibilities below collapse onto one person — the RACI matrix is recorded for clarity, not for delegation.

## RACI — Current Scope

| Activity | R | A | C | I |
|----------|---|---|---|---|
| Feature development | Joseph | Joseph | — | (future contributors) |
| Code review / self-review | Joseph | Joseph | — | — |
| Test authoring and maintenance | Joseph | Joseph | — | — |
| CI/CD configuration | Joseph | Joseph | — | — |
| Security review and threat model updates | Joseph | Joseph | — | — |
| Release cutting (tag → PyPI) | Joseph | Joseph | — | — |
| Issue triage | Joseph | Joseph | — | — |
| Dependency updates | Joseph | Joseph | — | — |
| ADR authorship | Joseph | Joseph | — | — |
| Documentation (README, CLAUDE.md, AIWG artifacts) | Joseph | Joseph | — | — |

## Communication Channels

| Channel | Use |
|---------|-----|
| Gitea issues (`roctinam/carbonyl-agent/issues`) | Primary issue tracker |
| GitHub issues (`jmagly/carbonyl-agent/issues`) | Mirror — reference only, do not duplicate triage |
| Gitea PRs | Primary code review surface (self-review) |
| `.aiwg/` artifacts in-repo | Durable decisions, plans, test strategy, threat model |
| Commit messages | Record rationale inline; reference issue IDs |

Async-only. No standups, no synchronous meetings.

## Working Hours / Availability

- **Mode**: part-time, asynchronous.
- **Expect response latency**: days, not hours.
- **No on-call**: `carbonyl-agent` is a library, not a service. There is no production incident channel. Security reports route through Gitea issues with label `security` (private issue if sensitive).

## Decision-Making Process

- **Solo decisions** are the norm. When a decision materially affects architecture, API surface, security posture, or external contract, it is logged as an ADR in `.aiwg/architecture/adrs/` before or alongside the implementing commit.
- **Disagreements**: N/A (solo).
- **Escalation**: N/A.
- **Reversibility bias**: prefer reversible changes; irreversible choices (public API, release versions, published checksums) get an ADR.

## Onboarding Notes for Future Contributors

If a contributor arrives, start here:

1. **Read the top-level docs** in order:
   - `README.md` — install and quick start
   - `CLAUDE.md` — project conventions, binary search order, layout
   - `AIWG.md` — SDLC framework context
2. **Read the intake**:
   - `.aiwg/intake/project-intake.md`
   - `.aiwg/intake/solution-profile.md`
3. **Read the plans and threat model**:
   - `.aiwg/planning/iteration-001-plan.md`
   - `.aiwg/security/threat-model.md`
   - `.aiwg/testing/test-strategy.md`
4. **Dev setup**:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/carbonyl-agent install
   .venv/bin/pytest tests/
   ```
5. **Remotes**: push to `origin` (Gitea) first, then `github` (mirror). This is a project-wide convention documented in `/srv/vmshare/dev-inbox/CLAUDE.md`.
6. **Issue workflow**: file issues on Gitea. Label with `bug`, `enhancement`, `security`, or `docs`. Reference issue IDs in commits and test docstrings.
7. **Decisions**: if you need to make a design choice, draft an ADR in `.aiwg/architecture/adrs/` and link it from the PR description.

## Succession / Bus Factor

Bus factor = 1. Mitigations:

- All context is in-repo (`.aiwg/`, `CLAUDE.md`, `README.md`) — no tacit knowledge.
- Dual remote (Gitea + GitHub) ensures code survives single-host failure.
- Release artifacts published to PyPI ensure end-users can pin a known-good version.
- Project is extracted cleanly from `roctinam/carbonyl`; the parent project can reabsorb it if abandoned.
