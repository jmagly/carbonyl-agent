# Building against the wreq cold mirror

Per ADR-005 §"Bus-factor mitigation plan" item 2, the wreq source
tarball at the pinned SHA is mirrored to a Gitea release on
`roctinam/carbonyl-agent`. Air-gapped builds (or any environment that
cannot reach `crates.io` / GitHub) resolve from this mirror instead.

**Refs**: ADR-005; roctinam/carbonyl-agent#60.

## Mirror layout

| Field | Value |
|---|---|
| Host | `git.integrolabs.net` |
| Repo | `roctinam/carbonyl-agent` |
| Tag scheme | `wreq-mirror-<short-sha>` (12 hex chars) |
| Asset 1 | `wreq-<semver>-<short-sha>.tar.gz` (the source tarball) |
| Asset 2 | `wreq-<semver>-<short-sha>.tar.gz.sha256` (sidecar checksum) |

The tag's full SHA is recoverable from `.carbonyl-fingerprint-version`'s
`wreq-sha=` line.

## Building with the mirror

There are two paths:

### Path A — Cargo `[patch]` table (recommended)

Add a `[patch.crates-io]` block in `Cargo.toml` (or a workspace-level
`.cargo/config.toml`) that points wreq at a vendored source unpacked
from the mirror tarball:

```toml
# .cargo/config.toml
[patch.crates-io]
wreq = { path = "vendor/wreq" }
```

Bootstrap script (run once per checkout):

```bash
#!/usr/bin/env bash
set -euo pipefail

PIN_FILE=".carbonyl-fingerprint-version"
SHA="$(grep -E '^wreq-sha=' "${PIN_FILE}" | cut -d= -f2 | tr -d '[:space:]')"
VER="$(grep -E '^wreq-version=' "${PIN_FILE}" | cut -d= -f2 | tr -d '[:space:]')"
SHORT="${SHA:0:12}"
TAG="wreq-mirror-${SHORT}"
TARBALL="wreq-${VER}-${SHORT}.tar.gz"

mkdir -p vendor
curl -fsSL \
    "https://git.integrolabs.net/roctinam/carbonyl-agent/releases/download/${TAG}/${TARBALL}" \
    -o "vendor/${TARBALL}"
curl -fsSL \
    "https://git.integrolabs.net/roctinam/carbonyl-agent/releases/download/${TAG}/${TARBALL}.sha256" \
    -o "vendor/${TARBALL}.sha256"

# Verify checksum before extracting.
( cd vendor && sha256sum -c "${TARBALL}.sha256" )

tar -xzf "vendor/${TARBALL}" -C vendor
mv "vendor/wreq-${SHA}" "vendor/wreq"
```

Then `cargo build` resolves wreq from `vendor/wreq` instead of the
network.

### Path B — Cargo source replacement (whole-registry mirror)

For environments that need to mirror more than just wreq, configure
Cargo to replace the entire `crates-io` source with a local
`vendored-sources` registry built via `cargo vendor`. See the
[Cargo book §"Source Replacement"][cargo-source]. This is heavier
setup but covers all transitive deps in one shot.

[cargo-source]: https://doc.rust-lang.org/cargo/reference/source-replacement.html

## Verifying air-gap

To prove the build works without `crates.io`/GitHub access:

```bash
# Block all outbound traffic except git.integrolabs.net.
# (Example: iptables on Linux; adapt to your network sandbox.)
sudo iptables -A OUTPUT -d git.integrolabs.net -j ACCEPT
sudo iptables -A OUTPUT -d 127.0.0.1            -j ACCEPT
sudo iptables -P OUTPUT DROP

# Bootstrap the mirror (path A or B), then build.
./scripts/bootstrap-wreq-mirror.sh   # produced by path A above
cargo build --manifest-path crates/carbonyl-fingerprint/Cargo.toml --features python

# Restore network policy when done.
sudo iptables -P OUTPUT ACCEPT
```

End-to-end air-gap verification is **not** part of the v1 #60 commit —
it requires a real wreq SHA in the pin (currently `TBD-#44`). When
#44 lands the dep and the first real `wreq-mirror-*` release goes up,
this verification path is exercised.

## Refreshing the mirror

Manual: `scripts/wreq-mirror-refresh.sh` (see `--help`).

Automatic: `.gitea/workflows/wreq-mirror.yml` runs on every push that
touches `.carbonyl-fingerprint-version`. Bumping the pin triggers a
mirror refresh in the same CI cycle that runs `check.yml`'s pin/lock
alignment job.

## Tag retention

Mirrors are append-only: every pinned SHA produces exactly one tag,
which lives forever. This means rolling back to an older pin always
finds its mirror. Mirrors do NOT auto-prune; if the mirror release
list grows unwieldy (>50 tags), file a tracking issue to introduce a
retention policy aligned with the project's general support window.
