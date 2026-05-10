# Bumping the wreq pin (`.carbonyl-fingerprint-version`)

The `.carbonyl-fingerprint-version` file pins the exact `wreq` source
the carbonyl-fingerprint TLS layer is built against. CI rejects drift
between the pinned SHA and the SHA Cargo resolved into `Cargo.lock`.
This document is the procedure for changing the pin.

**Refs**: ADR-005 § Bus-factor mitigation plan #1; roctinam/carbonyl-agent#59.

## When to bump

| Trigger | Action |
|---|---|
| New stable wreq release with desirable fix | Bump |
| Security advisory against the pinned SHA | Bump (or invoke `wreq-replacement` runbook if no fix exists) |
| Pinning a fork instead of upstream (e.g., the cold mirror) | Bump |
| Routine maintenance on a quiet week | Don't — wait for a real reason |

Bumping the pin invalidates the cold mirror — see step 4.

## Procedure

### 1. Pick the new SHA

```bash
# Latest release of upstream:
curl -fsSL https://api.github.com/repos/0x676e67/wreq/releases/latest \
  | jq -r '.tag_name'

# Resolve tag → SHA:
git ls-remote https://github.com/0x676e67/wreq refs/tags/<tag> \
  | awk '{print $1}'
```

Pick the **commit SHA**, not the abbreviated form. CI does an exact
40-char compare.

### 2. Update Cargo.toml

```toml
# crates/carbonyl-fingerprint/Cargo.toml (after #44 lands the dep)
wreq = "<new-semver>"
```

Run `cargo update -p wreq` to refresh `Cargo.lock`. The resolved SHA
must match step 1; if it doesn't, the registry version is stale and
the bump cannot proceed without a force-source override (use git
dependency in Cargo.toml as a temporary measure, document the reason).

### 3. Update the pin file

Edit `.carbonyl-fingerprint-version`:

```text
wreq-version=<new-semver>
wreq-sha=<new-40-char-sha>
wreq-source=https://github.com/0x676e67/wreq
```

(Or change `wreq-source` if you've migrated to a different upstream
fork — that decision should also bump ADR-005's library selection
table.)

### 4. Refresh the cold mirror (#60)

```bash
# Once #60 lands its tooling:
scripts/wreq-mirror-refresh.sh --sha <new-sha>
```

The mirror tag scheme is `wreq-mirror-<sha>` on the appropriate Gitea
release. Air-gapped builds resolve from this mirror; if you bump the
pin without refreshing the mirror, those builds break.

### 5. Run the conformance suite (#62)

The wire-format assertions in the conformance suite catch silent
regressions when wreq's TLS hello layout changes between releases.

```bash
cargo test --manifest-path crates/carbonyl-fingerprint/Cargo.toml \
  --features python --test conformance
```

If conformance fails, do **not** merge the bump. Instead:
- File an upstream regression issue, OR
- Roll back to the previous SHA, OR
- If wreq is genuinely broken with no fix, invoke the
  `wreq-replacement` runbook (#63 → `.aiwg/architecture/runbooks/wreq-replacement.md`)

### 6. Commit

Conventional commit shape:

```
chore(wreq): bump pin to vX.Y.Z (<short-sha>)

Reason: <one-line rationale>

Refs: roctinam/carbonyl-agent#59
```

Push and let CI verify the pin/lock alignment.

## What CI checks

`.gitea/workflows/check.yml` runs (after wreq is a real dep):

1. Read pinned SHA from `.carbonyl-fingerprint-version`.
2. Read resolved SHA from `Cargo.lock` (`[[package]] name = "wreq"` →
   `source = "git+...#<sha>"` for git deps, or registry hash check
   for crates.io deps).
3. Fail if they differ.
4. Skip silently if the wreq dep is absent (covers pre-#44 state).

## Failure modes and recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| CI says "pin/lock mismatch" | `cargo update` or dependabot bumped wreq | Either revert the bump or follow this procedure to legitimize it |
| Cold mirror returns 404 for `wreq-mirror-<sha>` | Forgot step 4 | Refresh mirror; the air-gap build was always going to fail until you do |
| Conformance suite fails post-bump | wreq changed wire format | Roll back to previous SHA; file upstream issue; consider escape-hatch runbook |
| Pin file present but values are `TBD-#44` | Pre-#44 placeholder state | Replace with real values as #44's first task |
