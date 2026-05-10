# Runbook: replacing `wreq` as the TLS-fingerprint HTTP backend

**Status**: Active SOP. Per ADR-005 §"Bus-factor mitigation plan" item 5.
**Refs**: roctinam/carbonyl-agent#63, ADR-005 (Accepted 2026-05-08).
**Owner**: Whoever is on-call for `carbonyl-fingerprint` at trigger time.

## When to invoke this runbook

Invoke when **any** of these conditions holds. The first three are
hard triggers — start migration immediately. The last two require
judgment.

| Trigger | Signal | Source to monitor |
|---|---|---|
| Upstream archived | `wreq` repo flips to "Archived" on its host, or no commits for >180 days | `https://github.com/0x676e67/wreq` (last upstream URL of record); GitHub atom feed `releases.atom` |
| CVE without patch | A `wreq` CVE is published with no upstream fix in `--severity high` after 14 days | `cargo audit` in CI; `https://rustsec.org/advisories/` |
| License change | `Cargo.toml` `license` field of upstream changes from `MIT/Apache-2.0` to anything more restrictive | `cargo metadata --format-version 1 \| jq -r '.packages[] \| select(.name=="wreq") .license'` on every pin bump |
| Maintainer responsiveness | Open PR addressing a security issue stagnates >30 days | Manual: weekly check on `wreq/pulls?q=is%3Aopen+label%3Asecurity` |
| Trait surface drift | Conformance suite (#62) fails after a `wreq` minor bump and upstream declines the fix | CI job `rust-crates::conformance` red on a `wreq` bump |

## Migration plan

### 0. Stop the bleed

If trigger 1 or 2 fired:

1. Pin `wreq` to the last known-good SHA in `.carbonyl-fingerprint-version`
   (which #59 introduces). Do not let the dependabot/renovate equivalent
   bump it.
2. If trigger 2 (unpatched CVE), evaluate impact: does the CVE affect
   the surface used by `carbonyl-fingerprint::http::WreqClient`? If
   yes, surface to W3B consumers (#44) and warn that builds are using
   the vulnerable pin until migration completes.
3. Open a tracking issue: `[trigger=…] migrate off wreq`. Reference this
   runbook.

Time budget for this stage: same day.

### 1. Pick a replacement

Ranked candidates per ADR-005 §"Library selection rationale":

#### Tier 1 — `rquest`

- **Why**: functionally similar to `wreq`; per-field JA3/JA4/H2 control;
  MIT/Apache-2.0 dual licensed; small but overlapping maintainership.
- **Cost**: ~3 days for the trait impl, ~2 days for conformance.
- **Risk**: same-class bus-factor exposure as `wreq`; if both projects
  are downstream of a single contributor, the escape hatch is illusory.
  Verify the maintainer set is disjoint before choosing.
- **Already in the conformance suite (#62)** as the secondary backend,
  so the trait impl ought to be small.

Source: `https://github.com/penumbra-x/rquest` (URL of record at ADR
authoring; verify before pin).

#### Tier 2 — `reqwest-impersonate` (with caveats)

- **Why**: actively maintained by a credible team; broad downstream use.
- **Why not as default**: preset-only fingerprint API cannot express
  per-persona TLS variation. To use this, we'd downgrade Persona's
  fingerprint fields from per-field control to "match preset X" — that
  is a Persona schema breaking change.
- **Acceptable migration path** only if the schema is also revised. Do
  not pick this without an ADR amendment.

Source: `https://github.com/4JX/reqwest-impersonate` (verify before pin).

#### Tier 3 — Hand-rolled rustls

- **Why**: maximum control; no third-party bus factor.
- **Cost**: ~6 weeks of focused work plus indefinite ongoing maintenance.
  ADR-005 explicitly rejected this for v0.x.
- **When it makes sense**: only if Tiers 1 and 2 fail simultaneously
  AND we have headcount.

#### Tier 4 — `curl-impersonate` (out-of-process)

- **Why**: broad fingerprint coverage; large maintainer pool via curl.
- **Why not in hot path**: per-request `fork+exec` cost is ~10 ms plus
  IPC marshalling. Acceptable for low-volume CLI use, unacceptable for
  SDK consumers issuing dozens of requests per session.
- **Useful inside the conformance suite** as an external "ground truth"
  probe even if not the production backend.

### 2. Verify the replacement against the trait surface

1. Confirm the candidate exposes — or can wrap — every method on
   `carbonyl_fingerprint::http::FingerprintHttpClient`. Block on this.
   The trait surface is normative per ADR-005.
2. Run the conformance test suite (#62) against the candidate. The
   suite emits known-good Persona requests against a controlled
   responder and asserts the wire format matches the persona spec.
3. The candidate is acceptable iff:
   - Every trait method has a backing impl (no `unimplemented!()`)
   - Conformance suite passes for **all** Chrome majors in
     `corpus/chrome/chrome-{major}.toml`
   - Output diff vs `wreq` on the same Persona is byte-equal at the
     TLS hello and HTTP/2 SETTINGS layers (or differs only in fields
     not part of the Persona contract — document any such field)

### 3. Implement the swap

1. Add the candidate as `carbonyl-fingerprint` dep behind a feature
   flag mirroring the wreq pattern: `[features] rquest = ["dep:rquest"]`.
2. Add a sibling impl module: `crates/carbonyl-fingerprint/src/http/rquest_impl.rs`,
   `pub struct RquestClient: FingerprintHttpClient`.
3. Run `cargo test --features rquest` — every existing trait test must
   pass against the new backend.
4. Update `crates/carbonyl-fingerprint/src/http/mod.rs` default impl
   selection to point at the candidate (gate via feature so we can
   roll back).
5. Run the cross-backend conformance suite (#62). It must be green.

### 4. Bump the pin

1. `.carbonyl-fingerprint-version` (introduced by #59):
   - Replace `wreq` line with the candidate name + crate version + git SHA.
2. Refresh the cold mirror (#60):
   - Tag `<candidate>-mirror-<sha>` on the appropriate Gitea release.
   - Verify air-gapped build with the documented `--use-mirror` path.
3. Update `crates/carbonyl-fingerprint/README.md` (or main crate doc
   comments) to name the new backend.
4. Update this runbook with the new candidate ranking — promote what
   used to be Tier 2 or 3 into the new Tier 1.

### 5. Cut a release

1. Tag a major version bump on `roctinam/carbonyl-agent` if the public
   `FingerprintHttpClient` surface changed. Otherwise tag a minor.
2. Publish to PyPI (gated on #12 OIDC trusted publisher landing).
3. Notify W3B consumers (#44) — they recompile but should not need
   code changes; the trait surface is the abstraction.

## Cost estimate

Per ADR-005 §"Negative" the migration costs **1–2 weeks of focused work**
provided this runbook has been kept current and a Tier 1 candidate is
already in the conformance suite. Specific budget:

| Stage | Worst case | Typical |
|---|---|---|
| 0. Stop the bleed | 1 day | 1 day |
| 1. Pick replacement | 1 day | 0.5 day |
| 2. Verify trait surface | 3 days | 1 day |
| 3. Implement swap | 5 days | 2 days |
| 4. Bump pin + mirror | 1 day | 0.5 day |
| 5. Cut release | 1 day | 0.5 day |
| **Total** | **12 days** | **5.5 days** |

## Things that are easy to forget

- **Persona schema is stable across the migration.** The whole point of
  the trait + persona separation is that downstream consumers never
  see this churn. If a candidate forces a Persona schema change, that
  is an ADR-005 amendment, not a routine migration.
- **Audit log format must continue to work.** ADR-005 §"Audit / observability"
  defines the on-disk format; the new backend must emit the same
  per-request log structure. Verify with a parser test before release.
- **The conformance suite IS the acceptance gate.** Don't merge a
  backend swap with a yellow conformance run.
- **Re-confirm the maintainer set isn't shared.** Tier 1 (`rquest`) is
  the canonical escape hatch but it shares contributor overlap with
  `wreq`. Recheck before committing — a shared bus factor is no
  escape hatch at all.

## Post-migration

After the new backend has been live for one stable release cycle:

1. Re-author this runbook with the new backend as the assumed default
   and the old backend as the historical context (don't delete it; it
   may need to come back).
2. Re-run the conformance suite weekly to detect drift in the new
   backend's wire format vs corpus reference data.
3. Bump ADR-005's library selection table to reflect the swap.
