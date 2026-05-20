# Release artifacts (in-tree mirror)

This directory holds a copy of every tagged release's published artifacts —
the same `*.whl` and `*.tar.gz` files attached to the GitHub release page,
mirrored into the source tree so they can be installed without external
network access.

## Why in-tree?

Operators in airgapped or otherwise network-restricted environments need a
way to install a known-version `carbonyl-agent` without reaching out to
github.com. Cloning the repo (or fetching a single tagged commit) gives them
the wheel directly:

```bash
git clone --depth 1 --branch v2026.5.3 \
  https://github.com/jmagly/carbonyl-agent.git
pip install ./carbonyl-agent/docs/releases/v2026.5.3/carbonyl_agent-2026.5.3-py3-none-any.whl
```

This complements the runtime-tarball airgap install path (`carbonyl-agent
install --from-file …` for the Carbonyl binary, documented in
`docs/install.md`) — together they cover both the Python SDK and the
underlying Chromium runtime.

## Layout

```
docs/releases/
├── README.md                                # this file
└── vYYYY.M.PATCH/
    ├── carbonyl_agent-YYYY.M.PATCH-py3-none-any.whl
    ├── carbonyl_agent-YYYY.M.PATCH.tar.gz
    └── SHA256SUMS
```

`SHA256SUMS` is the output of `sha256sum *.whl *.tar.gz` and matches the
`digest` field returned by `GET /repos/jmagly/carbonyl-agent/releases/tags/
v<version>` — verify with:

```bash
cd docs/releases/v2026.5.3
sha256sum -c SHA256SUMS
```

## Release process

When `.github/workflows/release.yml` ships a tag, the maintainer mirrors the
two assets into `docs/releases/v<version>/` and generates `SHA256SUMS`. The
release tag is the source of truth — these are byte-identical copies, not
re-builds.

For the current release process see [`docs/versioning.md`](../versioning.md).
