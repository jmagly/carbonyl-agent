<!-- aiwg:managed v2026.5.2 bundled -->
# Ops-Complete Rules Index

Rules owned by the ops-complete framework. Each entry provides a summary sufficient to determine relevance — load the full rule via @-link only when needed.

**How to use**: Scan summaries below. When a rule is relevant to your current task, load the full rule file for detailed enforcement instructions. Rules are grouped by enforcement level (CRITICAL > HIGH > MEDIUM).

---

## CRITICAL (1 rule)

#### ops-safety
**Summary**: Interactive command detection and destructive operation gates. Agents must flag commands requiring interactive input (sudo prompts, LUKS, password entry) for human execution. Gate destructive operations (rm -rf, fdisk, mkfs, iptables -F, systemctl disable) behind human confirmation. Classify every operation by blast radius (critical/high/medium/low). Require dry-run first for high/critical operations. Never apply one host's config to another without explicit confirmation. Token security via heredoc pattern.
**When to apply**: Executing operational commands, infrastructure changes, disk/network/firewall operations, cross-host config application, any destructive operation
**Full rule**: @$AIWG_ROOT/agentic/code/frameworks/ops-complete/rules/ops-safety.md

---

## HIGH (2 rules)

#### ops-documentation
**Summary**: Executable, idempotent, verified procedure format. Every operational document must follow the standard 8-section structure: Purpose, System Topology, Procedure, Verification, Troubleshooting, House Rules for Agents, What NOT to Fix, Audit Trail. Commands must be copy-paste ready with expected output shown. Procedures must end with verification steps. Idempotency required when possible, declared when not.
**When to apply**: Writing runbooks, host standup guides, maintenance procedures, migration documents, any operational documentation consumed by agents or operators
**Full rule**: @$AIWG_ROOT/agentic/code/frameworks/ops-complete/rules/ops-documentation.md

#### ops-cross-repo
**Summary**: Scope validation and cross-repo reference format. Enforces repo boundaries: sysops = per-host hardware/OS, itops = services/assets/CMDB/DR, devops = CI/CD/build/fleet-wide tooling. Flags work landing in the wrong repo before commit. Requires fully qualified cross-repo references (roctinam/sysops#15, not bare #15). Tracks dependencies with Blocks:/Blocked-by: markers. Requires host-to-service mapping awareness.
**When to apply**: Committing to ops repos, referencing issues across repos, creating cross-repo dependencies, host-level changes affecting services, service changes affecting hosts
**Full rule**: @$AIWG_ROOT/agentic/code/frameworks/ops-complete/rules/ops-cross-repo.md

---

## MEDIUM (1 rule)

#### ops-issue-tracking
**Summary**: Label conventions, dependency tracking, and phased work patterns. Standard labels: `host: <hostname>`, `priority: {critical,high,medium,low}`, `area: <domain>`, `status: <state>`, `type: <kind>`. Multi-step operations use phased issue breakdown with explicit dependencies. Commits reference issues via conventional commit messages. Progress tracked through issue comments at each checkpoint.
**When to apply**: Creating ops issues, labeling, multi-step migrations, commit message writing, progress reporting
**Full rule**: @$AIWG_ROOT/agentic/code/frameworks/ops-complete/rules/ops-issue-tracking.md

---

## Quick Reference by Context

| Task Type | Relevant Rules |
|-----------|---------------|
| **Executing commands on hosts** | ops-safety, ops-documentation |
| **Writing runbooks/procedures** | ops-documentation, ops-safety |
| **Destructive operations** | ops-safety |
| **Cross-host config changes** | ops-safety, ops-cross-repo |
| **Creating/triaging issues** | ops-issue-tracking, ops-cross-repo |
| **Committing to ops repos** | ops-cross-repo, ops-issue-tracking |
| **Multi-step migrations** | ops-issue-tracking, ops-documentation, ops-safety |
| **Cross-repo references** | ops-cross-repo, ops-issue-tracking |
| **Host standup/decommission** | ops-documentation, ops-safety, ops-issue-tracking |
| **Service impact assessment** | ops-cross-repo, ops-safety |

---

*4 rules across 3 enforcement levels*
*Full rule files: @$AIWG_ROOT/agentic/code/frameworks/ops-complete/rules/*
