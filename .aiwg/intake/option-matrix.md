# Option Matrix (Project Context & Intent)

**Generated**: 2026-04-09 (from codebase analysis, non-interactive)

## Step 1: Project Reality

### What IS This Project?

Python automation SDK (~6 modules, ~smoke-test coverage) that wraps the Carbonyl headless browser via PTY + pyte terminal emulation. MIT-licensed, hosted on Gitea (mirrored to GitHub), v0.1.0, freshly extracted from a parent repo, solo developer, zero current users, intended for distribution via PyPI to Python developers building headless-browser automation.

### Audience & Scale

**Who uses this?**
- [x] Just me (solo developer, current state)
- [ ] Eventual: external developers (Python community using Carbonyl) — pre-launch

**Audience characteristics**:
- Technical sophistication: Technical (Python developers)
- Risk tolerance: Experimental OK (early-stage SDK)
- Support: Self-service (README + issues)

**Usage scale**:
- Active users: 0 (pre-release)
- Request volume: N/A (library)
- Data volume: N/A (stateless except local session dirs)

### Deployment & Infrastructure

**Deployment model**:
- [x] Client-only — installed as pip package, runs in user's Python process
- Distribution: PyPI (planned) + Gitea releases for Carbonyl runtime tarballs

**Where it runs**: User's local machine (Linux, macOS)

**Infrastructure complexity**:
- Deployment type: pip package
- Persistence: filesystem only (~/.local/share/carbonyl/)
- External dependencies: 2 (pexpect, pyte) + Carbonyl binary

### Technical Complexity

- Size: <1k LoC
- Languages: Python only
- Architecture: Simple library + optional daemon
- Familiarity: Brownfield extraction (code is mature, packaging is new)

**Risk factors**:
- [x] Integration-heavy with subprocess/PTY (Carbonyl binary lifecycle, terminal emulation edge cases)

---

## Step 2: Constraints & Context

**Team**: 1 developer, senior, part-time on this project
**Budget**: Personal/volunteer
**Timeline**: No fixed deadline

**Data sensitivity**: Public only (no PII, no payments)
**Regulatory**: None
**Contractual**: None

**Current state**:
- Stage: Prototype → preparing for first release
- Test coverage: Smoke tests only
- Docs: README + CLAUDE.md
- Deployment automation: None

**Technical debt**: Minor (no CI, no checksum verification, sparse tests)

---

## Step 3: Priorities & Trade-offs

**Ranking** (confirmed by maintainer):
1. Quality & correctness — foundation library, bugs propagate to all consumers
2. Reliability — must work consistently across supported platforms
3. Delivery speed — release matters but not at the expense of quality
4. Cost efficiency — solo developer, but willing to invest in process rigor

**Priority weights**:

| Criterion | Weight | Rationale |
|-----------|--------|-----------|
| Quality/security | 0.45 | Foundation SDK; downstream blast radius; supply-chain integrity (tarball download) |
| Reliability/scale | 0.25 | Must work consistently across Linux triples + macOS; daemon mode reliability critical |
| Delivery speed | 0.20 | Release matters but rigor is non-negotiable |
| Cost efficiency | 0.10 | Solo developer accepts process overhead in exchange for durable foundation |

**Optimizing for**: A durable, well-documented, well-tested foundation library that external contributors and downstream consumers can rely on.

**Non-negotiable**: MIT license, open source, dual-remote (Gitea primary), full SDLC rigor (requirements, architecture baseline, test strategy, gates).

---

## Step 4: Intent & Decision Context

**Trigger for intake**:
- [x] Documenting existing project (just extracted from parent repo)
- [x] Preparing for release (need baseline before public PyPI launch)

**Decisions to make**:
1. Invest in CI/release automation now, or ship v0.1.0 manually first?
2. Add integration tests for daemon/session before release, or after first user feedback?
3. SHA256 verification for runtime download — block release on this or fast-follow?

**Uncertain**: Whether anyone will use this. Investment level should match validation.

---

## Step 5: Framework Application

**Templates** (full set):
- [x] Intake (done)
- [x] Requirements (use cases, user stories, NFRs)
- [x] Architecture (SAD, ADRs)
- [x] Test strategy
- [x] Security (lightweight threat model — supply chain focus)
- [x] Deployment / release (CI/CD scaffold, release runbook)
- [x] Governance (decision log via ADRs)

**Commands**:
- [x] Intake commands
- [x] SDLC accelerate / flow commands
- [x] Quality gates (LOM, ABM)

**Agents**: Full SDLC agent orchestration (requirements-analyst, architecture-designer, security-architect, test-architect, devops-engineer, etc.)

**Process rigor**: **Full** — comprehensive artifact set, gate checks, multi-agent review where applicable.

### Rationale

This is a foundation library that will be consumed by other projects. Bugs and design mistakes have downstream blast radius. The maintainer is willing to invest in full SDLC rigor up front to produce a durable, well-documented baseline before public release. Process overhead is accepted in exchange for confidence at the v1.0 milestone.

---

## Step 6: Evolution & Adaptation

**Expected changes** (next 6–12 months):
- [x] First PyPI release (immediate)
- [x] User feedback may drive API changes (semver matters once published)
- [ ] Team expansion: unlikely
- [ ] Compliance: unlikely

**Adaptation triggers**:
- Add CHANGELOG + semver discipline → at first PyPI publish
- Add CONTRIBUTING.md + CI status badges → at first external PR
- Add architecture doc → if 2nd contributor joins
- Add integration test matrix → if multiple Carbonyl runtime versions need support

**Planned framework evolution**:
- Now: intake only
- 3 months: + CI workflow + CHANGELOG
- 6 months: + integration tests if user reports surface bugs
- 12 months: revisit if adoption justifies more rigor
