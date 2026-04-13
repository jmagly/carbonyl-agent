# Threat Model — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09
- **Method**: Lightweight STRIDE
- **Owner**: Joseph Magly
- **Scope**: `carbonyl-agent` Python SDK — installer, daemon, session management, PTY bridge to Carbonyl runtime. Out of scope: Carbonyl/Chromium internals, upstream OS security.

## 1. System Overview

`carbonyl-agent` runs entirely in the user's security context. Trust boundaries:

1. **Network → local disk**: `install.py` downloads runtime tarballs from `git.integrolabs.net` over HTTPS and extracts them under `~/.local/share/carbonyl/bin/`.
2. **Local process ↔ daemon**: `DaemonClient` talks to a daemon process over a Unix domain socket in `~/.local/share/carbonyl/sessions/<name>.sock`.
3. **Browser ↔ SDK**: PTY byte stream parsed by `pyte`.
4. **Docker fallback**: `docker run fathyb/carbonyl` pulls an external image.

## 2. Assets

- User's local filesystem and shell account.
- Carbonyl runtime binary integrity.
- Session cookies / localStorage in `~/.local/share/carbonyl/sessions/<name>/`.
- Pages rendered by the browser (may contain arbitrary untrusted content).

## 3. Threats

| ID | Description | STRIDE | Likelihood | Impact | Current State | Mitigation Plan | Owner |
|----|-------------|--------|-----------|--------|---------------|-----------------|-------|
| **T1** | Tampered runtime tarball — `install.py` downloads `{triple}.tgz` over HTTPS and extracts without checksum verification. A compromised mirror, MITM against a user with weak TLS validation, or a malicious redirect could deliver a backdoored `carbonyl` binary that runs in the user's account. | Tampering / Elevation of Privilege | Medium | **High** (arbitrary code execution as user) | No verification. HTTPS only. | **HIGH PRIORITY**: Publish `SHA256SUMS` file per release tag on Gitea. `install.py` fetches it alongside the tarball and refuses extraction on mismatch. Pin expected public key for sigstore/minisign in a future iteration. Add `--checksum` override for dev use. (US-002) | Maintainer |
| **T2** | Compromised Gitea release infrastructure — an attacker with push access to `roctinam/carbonyl` replaces runtime assets. Checksum verification (T1) does not help if the checksum file is also replaced. | Tampering | Low | **High** | Unmitigated | Medium-term: detached signatures (minisign or cosign) with the signing key held outside Gitea. Document key fingerprint in README. Monitor release feed for unexpected publishes. | Maintainer |
| **T3** | Unix socket permission bypass — daemon socket at `~/.local/share/carbonyl/sessions/<name>.sock` inherits directory default mode. On a multi-user host with permissive umask or shared home, another local user could connect and drive the browser. | Spoofing / Information Disclosure | Low | Medium (session hijack) | Socket created by `ThreadingUnixStreamServer` defaults; no explicit chmod. | Explicitly `chmod 0700` the parent session directory at creation; `chmod 0600` the socket post-bind; add startup check that refuses to run if parent dir is group/world-writable. Integration test validates permissions. (US-003) | Maintainer |
| **T4** | Path traversal in session names — `SessionManager.create("../../etc")` could escape the session root and read/write outside the intended directory, or the daemon socket path could be placed arbitrarily. | Tampering | Low | Low–Medium | Unvalidated string handling | Validate session names against `^[A-Za-z0-9_.-]{1,64}$` at the public API boundary. Raise `ValueError` on mismatch. Regression test with traversal vectors. (US-004) | Maintainer |
| **T5** | PTY injection from malicious page — a page-controlled byte stream could emit terminal escape sequences that confuse `pyte` or downstream consumers of `page_text()` (e.g., a caller passing the text to a shell). `pyte` itself is robust, but consumers may not sanitize. | Tampering | Low | Low | Relies on `pyte` robustness | Document in README that `page_text()` output is untrusted; add a `safe_text()` helper that strips non-printable characters. Fuzz `pyte` feed path with `hypothesis` (part of test strategy). | Maintainer |
| **T6** | Dependency compromise — malicious update to `pexpect` or `pyte` via PyPI typosquat or account takeover. | Tampering / Elevation of Privilege | Low–Medium | High | `pyproject.toml` uses lower-bound ranges only | Pin exact versions in a `constraints.txt` used by CI; enable Dependabot/Renovate on GitHub mirror; add `pip-audit` step to CI. Consider moving to hash-pinned `uv lock`. (US-005) | Maintainer |
| **T7** | Docker fallback pulls untrusted image — `docker run fathyb/carbonyl` runs whatever tag `latest` currently points to on Docker Hub. If `fathyb/carbonyl` is compromised or the user typoed an env override, arbitrary containerized code runs. | Tampering / Elevation of Privilege | Low | High (container runs in user's Docker context) | Implicit trust in `fathyb/carbonyl:latest` | Pin image by digest (`fathyb/carbonyl@sha256:...`) in the fallback code; document the pin in README; surface a warning when falling back; require `CARBONYL_ALLOW_DOCKER=1` env var to opt in. (US-006) | Maintainer |

## 4. STRIDE Coverage Summary

| Category | Covered by |
|----------|-----------|
| Spoofing | T3 |
| Tampering | T1, T2, T3, T4, T5, T6, T7 |
| Repudiation | N/A (single-user local tool) |
| Information Disclosure | T3 |
| Denial of Service | Not modeled (local tool, user-controlled) |
| Elevation of Privilege | T1, T6, T7 |

## 5. Residual Risks

After the mitigation plan lands, residual risk is low for a developer SDK. The dominant remaining risk is T2 (release infra compromise) — accepted for v0.1.0, revisit for v1.0 with signing keys.

## 6. Review Cadence

Re-evaluate this model at each minor release or when a new trust boundary is added (e.g., remote daemon, HTTP API, multi-user mode).
