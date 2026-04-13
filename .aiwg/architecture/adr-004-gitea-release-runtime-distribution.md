# ADR-004: Runtime Distribution via Gitea Releases

**Status**: Accepted
**Date**: 2026-04-09
**Version**: 1.0 (Baselined)
**Deciders**: Joseph Magly (sole maintainer)

---

## Context

The Carbonyl runtime is a statically-linked Chromium fork plus `libcarbonyl.so` Rust FFI. A single platform triple produces a tarball of roughly **75 MB**. `carbonyl-agent` needs to distribute these runtimes to end users so that `pip install carbonyl-agent && carbonyl-agent install` "just works."

Distribution options considered:

1. **Bundle the binary in the Python wheel** — ship one wheel per platform triple.
2. **Host tarballs on Gitea releases under `roctinam/carbonyl`** — the SDK downloads at install time.
3. **Host tarballs on GitHub releases (`jmagly/carbonyl-agent` mirror)** — public distribution channel.
4. **Host tarballs on a third-party CDN or S3 bucket** — external infrastructure.
5. **Ask users to build from source** — point at the upstream Carbonyl build docs.

Constraints:

- **PyPI wheel size limit** is effectively 100 MB per file; multi-triple bundling would require one wheel per `manylinux` + `macosx` combination and still exceed reasonable package sizes.
- The Carbonyl binary is owned by `roctinam/carbonyl`, which already publishes runtime tarballs on its Gitea releases tagged `runtime-<hash>` (the hash encodes Chromium version + patch set). Reusing these releases avoids duplicating storage.
- The project is early-stage (v0.1.0, single maintainer) and cannot justify paid CDN / S3 infrastructure.
- Runtime cadence is independent of SDK cadence: a bug fix in `carbonyl_agent.daemon` should not force a runtime re-release, and a Chromium security update should not require a SDK version bump.

## Decision

Runtime tarballs will be **hosted on Gitea releases at `roctinam/carbonyl`, tagged `runtime-<hash>`**, and downloaded at install time by `carbonyl-agent install`.

The installer (`src/carbonyl_agent/install.py`) constructs the download URL from three pieces:

```python
GITEA_BASE = os.environ.get("GITEA_BASE", "https://git.integrolabs.net")  # line 24
GITEA_REPO = "roctinam/carbonyl"                                          # line 25
url = f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/{triple}.tgz"  # line 65
```

Tag resolution supports a `runtime-latest` alias resolved via the Gitea API (`/api/v1/repos/<repo>/releases/latest`, install.py lines 44–57). The tarball is downloaded with progress output, extracted into `~/.local/share/carbonyl/bin/<triple>/` with the leading triple directory stripped (install.py lines 93–103), and the binary is chmod'd executable.

## Consequences

### Positive

- **Wheel stays tiny and pure-Python**: The `carbonyl-agent` wheel is a few kilobytes of Python source. One wheel fits all platforms. No manylinux headaches, no macOS universal2 shenanigans.
- **Independent runtime cadence**: The SDK can ship bug fixes without touching the runtime. A new Chromium build rolls as a new `runtime-<hash>` tag and users opt in with `carbonyl-agent install --force --tag runtime-<hash>` or `--tag runtime-latest`.
- **Single source of truth for the binary**: The upstream `roctinam/carbonyl` repo owns both the build process and the release artefacts. No forking of binaries between repos, no question about which build corresponds to which Chromium commit.
- **`GITEA_BASE` is overridable**: CI and air-gapped environments can point the installer at a private Gitea mirror without code changes (install.py line 24).
- **Transparent to `pip`**: PyPI sees a normal pure-Python package. The "big download" is clearly separated from dependency resolution and happens only when the user explicitly asks for it via `carbonyl-agent install`.

### Negative

- **No SHA256 verification (known gap)**: `cmd_install` currently trusts HTTPS + the Gitea server. An attacker who compromised the Gitea instance or performed a TLS MITM could substitute a malicious binary, and the installer would extract it silently. This is documented in SAD §11 and the intake form. Mitigation plan: publish a signed `SHA256SUMS` manifest per release and verify before extraction.
- **Gitea uptime is a hard dependency**: If `git.integrolabs.net` is down, `carbonyl-agent install` fails. Users can set `GITEA_BASE` to a mirror, but no mirror exists by default. A GitHub-releases fallback is a reasonable follow-up.
- **Public discoverability is limited**: Gitea releases are not indexed in the same way as PyPI or GitHub. First-time users must read the README to discover the install step.
- **"Latest" resolution is dynamic**: `_resolve_tag` queries the Gitea API at install time, so reproducible installs require pinning a specific `runtime-<hash>` tag in CI. `LATEST_TAG = "runtime-latest"` (install.py line 31) is a sentinel, not a pinned version.
- **Docker fallback becomes the safety net**: Users who can't reach Gitea can still smoke-test via the Docker image (see ADR-003), which somewhat softens the availability concern but does not replace a proper install.

### Neutral

- The tarball naming scheme (`<triple>.tgz` with a top-level `<triple>/` directory) is mirrored by the install.py extraction logic (lines 96–103). Changing either side without the other would break installs.
- Runtime tarballs are stored once in Gitea's LFS-equivalent storage and served directly; no additional CDN layer.

## Alternatives Considered

- **Bundle in wheel** (Option 1): rejected — wheel size blows past PyPI comfort, forces a wheel-per-triple, and couples SDK and runtime release cadence.
- **GitHub releases** (Option 3): deferred, not rejected. `jmagly/carbonyl-agent` is a GitHub mirror; a future enhancement could publish runtime tarballs there as a secondary source if Gitea availability becomes a pain point.
- **Third-party CDN / S3** (Option 4): rejected for v0.x — adds operational cost and access-management complexity for a single-maintainer project.
- **Build from source** (Option 5): rejected as the default user experience. The Carbonyl build requires a full Chromium toolchain (hours of build time, >100 GB disk). It remains available as an opt-in path via `CARBONYL_BIN` (ADR-003).

## References

- `src/carbonyl_agent/install.py` lines 24–115 (download + extract logic)
- `src/carbonyl_agent/install.py` lines 44–57 (`_resolve_tag` via Gitea API)
- `CLAUDE.md` "Runtime Distribution" section
- `.aiwg/intake/project-intake.md` "Security posture" — documents the SHA256 verification gap
- SAD §11 (Technical debt and future work)
- ADR-003 (runtime binary discovery order)
