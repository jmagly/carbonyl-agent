# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [CalVer](https://calver.org/) (`YYYY.M.PATCH`, PEP 440-compliant, no leading zeros) — matching the convention used in `aiwg`, `fortemi`, and related Roctinam repos. See [`docs/versioning.md`](docs/versioning.md).

## [Unreleased]

## [2026.5.3] - 2026-05-20

Cookie/token import from host browsers, plus three-mode QA-runner documentation.

### Added

- `carbonyl-agent cookies {import,list,revoke}` — import authenticated session cookies from a host browser (Chrome / Chromium / Brave / Edge / Firefox) into a carbonyl session's user-data-dir. Per-domain authorization prompt on every import, sensitive-domain denylist (banks, SSO providers, payment processors, primary email) that requires `--allow-sensitive` AND typing the domain to confirm, libsecret-backed Chromium decrypt, copy-then-read of the source SQLite (tolerates an open source browser), mode `0600` on every file written, audit log at `~/.local/share/carbonyl-agent/cookie-imports.log` that records cookie *names* but never *values*. Optional `[cookies]` extra ships `cryptography>=42` + `secretstorage>=3.3`; Firefox import works on stdlib alone. (#122)
- `docker/qa-runner/README.md` refactor — documents three first-class runtime modes (bare metal / Docker / VM) with a capability matrix per mode and a chooser pointing trusted-input workloads at the `agentic-sandbox` `browser-qa` loadout. (#120, #121)

### Changed

- `.gitea/workflows/ci.yml` typecheck and test jobs now install the `[cookies]` extra so `cryptography` + `secretstorage` stubs are resolvable and the Chromium-decrypt test path runs under all three Python versions.

### Closed

- #121 — systemd-udevd Xorg binding inside Docker documented as a known Docker-mode constraint; the VM-mode path covers the trusted-input workload.
- #122 — cookie/token import feature with per-domain authorization gate.

## [2026.5.2] - 2026-05-18

Runtime pin bump for the M148 release cut on `roctinam/carbonyl`.

### Changed

- `.carbonyl-runtime-version` pinned to `8f070d2720157bd0`, the M148 (148.0.7778.167) baseline shipped in carbonyl runtime v0.2.0-alpha.5. `carbonyl-agent install` (default `--tag`) and the qa-runner Docker image now consume the M148 runtime tarball. Triggers automatic qa-runner image rebuild via `.gitea/workflows/build-qa-runner.yml`. (roctinam/carbonyl#70)

## [2026.5.1] - 2026-05-17

Maintenance release. Real-browser fingerprint refresh (Chrome 147 → 148, Firefox 150 desktop preset wired from a captured ClientHello), airgap install support, audit-log rotation, and the GitHub-first release pipeline that ships this tag.

### Added

- Airgap install via `carbonyl-agent install --from-file <tarball>` and `--dry-run` for offline / sandboxed hosts that can't reach Gitea release assets directly. (#95, #117)
- Size-based rotation for the egress audit log at `~/.local/state/carbonyl-agent/egress-audit.log` — caps file size and rotates to numbered backups so long-running daemons don't accumulate unbounded audit history. (#93, #116)
- TestPyPI dry-run workflow (`.github/workflows/release-testpypi.yml`) — manual `workflow_dispatch` publish to TestPyPI for first-of-a-line release verification, mirroring the production release build pipeline. (#12 follow-up, #112)
- `scripts/check-build-env.sh` preflight that surfaces missing Rust prerequisites (clang, cmake, libclang-dev, libssl-dev, pkg-config, python3-dev) before they manifest as opaque BoringSSL/boring-sys2 build failures. Documented in README. (#109)
- PyPI classifiers (Programming Language, Topic, Operating System, etc.) and a runtime compatibility matrix in the install docs covering supported Linux distributions and arch triples. (#90, #92)
- Firefox 150.0.3 desktop preset (`FIREFOX_150_DESKTOP`) in `carbonyl-wreq` populated from a real-browser ClientHello capture (1874 bytes, SHA-256 `3416dc48ef…`). Wired through `preset_for` and exercised by the legacy and `via_registry` Layer-2 conformance paths. The h2 SETTINGS for Firefox are sourced from the persona spec backstop until a Firefox-trusted cert path lands. (#102)
- `carbonyl-wreq` Chromium-family chained issue (#118) and HITL-deferred capture tracker (#119) filed for the next iteration of the wreq-util replacement track.

### Changed

- Persona corpus refreshed from Chrome 147 to Chrome 148 across `carbonyl-fingerprint` template registry and conformance fixtures. Existing personas validated against the 148 baseline. (#114 + corpus refresh)
- Real Chrome 148 h2 SETTINGS recaptured against the current Chrome network stack. The new capture no longer advertises `MAX_CONCURRENT_STREAMS` (setting 3) — `CHROME_148_DESKTOP` drops it to match observed wire bytes, eliminating a Layer-2 mismatch. (#110, #114)
- pdoc documentation bundle cleaned (no warnings) and CI now fails on pdoc warnings to keep the bundle clean going forward. (#96, #115)
- `.github/workflows/release.yml` is now Phase-1-ready: GitHub release ships from `build` directly; PyPI publish is gated behind the `ENABLE_PYPI_PUBLISH` repo variable. Flip the variable to `true` after #12's PyPI/TestPyPI trusted-publisher setup is complete to enable Phase 2 (strict PyPI gate before GitHub release).

### Fixed

- Removed stale "TODO; see entry point" comments referring to Rule H (HKDF-Expand deterministic noise-seed derivation) — Rule H is implemented in `seed::derive_canvas_noise` / `seed::derive_audio_noise`. Added Rule H to the enforced-rules list and dropped it from the "deliberately not implemented" set. (#91)
- Rust-crates CI (`check.yml`) checkout step on `pull_request` events: Gitea sets `GITHUB_REF_NAME` to the PR number (e.g. `109`), not a branch name. Replaced `git clone --branch ${GITHUB_REF_NAME}` with explicit fetch-by-SHA so the workflow works on both push and pull_request events. (#107, #108, #111)
- Documented Rust toolchain prerequisites in the contributor-onboarding section of the README; the build-env preflight script (above) shifts these failures left from "opaque link error after 4 minutes of compilation" to "explicit missing-package report in 2 seconds." (#109)

### Known issues

- `wreq-util` (transitive dep of `carbonyl-wreq`) remains licensed GPL-3.0; the wreq feature is dev-only and the GPL crate is not redistributed in the wheel. Replacement track continues via in-house preset registry (#99, #100). Iteration B closed with Chrome 148 + Firefox 150 captured (#102); mobile-chrome / safari-macos / safari-ios captures deferred as HITL-blocked on physical devices (#119); Chromium-family variants (Brave, Chromium, Edge, Opera, Vivaldi, Arc) scoped for the next iteration (#118).
- PyPI publish remains skipped (Phase 1 of the release pipeline) — releases ship as GitHub releases at https://github.com/jmagly/carbonyl-agent/releases until #12's PyPI / TestPyPI trusted-publisher setup is completed on the web UI. The release pipeline is wired and one repo-variable flip away from full PyPI publishing.

## [2026.5.0] - 2026-05-15

### Added

- `examples/` directory with six runnable scripts (quickstart, session, daemon, persona, egress, wreq) plus a validated Chrome-147-Linux persona fixture under `examples/personas/`. Resolves the dangling README link. (#89)
- `LICENSE` (MIT) at the repository root — was implied by `pyproject.toml` but the file itself was missing.
- `THIRD_PARTY_LICENSES.txt` aggregating the carbonyl-fingerprint Rust dep tree (48 crates: 46 MIT, 1 BSD-3-Clause, 1 Unicode-3.0). Generated by `scripts/gen-third-party-licenses.sh` (cargo-about). Configured via `about.toml` + `about.hbs`. Wheel and sdist both ship the file. (#94)
- `runtime-tag=<tag>` pin file entry — semantic anchor that takes precedence over `runtime-hash` when both are present. Enables pinning to a stable runtime release line (e.g. `v2026.5.0`) once the upstream Carbonyl repo publishes them. New resolution source `tag-pin` reported by `carbonyl-agent install`. (#98)

### Changed

- **Versioning scheme**: Migrated from SemVer (`0.2.0a1`) to CalVer (`2026.5.0`) to align with `aiwg`, `fortemi`, and other Roctinam repos. No prerelease suffixes — each release is the current state at that calendar tag. Tag format is `v${version}` — `v2026.5.0`. See [`docs/versioning.md`](docs/versioning.md). (#87)

### Known issues

- `wreq-util` (transitive dep of `carbonyl-wreq`) is licensed GPL-3.0. The wreq feature is dev-only today (#88), so the GPL crate is not redistributed in the wheel, but the eventual wreq pip-install integration is blocked until wreq-util is replaced with permissively-licensed emulation presets or feature-gated with an explicit GPL acknowledgement. Tracked separately. (#94, #88)

## [0.2.0a1] - 2026-05-12

Persona-bound browser + egress. The headline change: a single `Persona` object now drives both the browser session and the out-of-band HTTP egress path, so API calls a script makes around its browser session carry a matching TLS fingerprint instead of a Python-stdlib SSL signature.

### Added — Persona system (W3A)

- New Rust crate `carbonyl-fingerprint` — typed `Persona` schema (v2, with `BrowserFamily` enum and `browser_version` field), consistency validator (rules A–H covering hardware bounds, platform↔UA-CH alignment, Linux font/GPU exclusions, Chrome major↔H2 Akamai pairing, timezone↔language allowlist, deterministic noise seeds via HKDF-Expand), bounded-drift refresher, and a sampler with five family templates: Desktop Chrome 147 Linux, Desktop Firefox 150 Linux, Desktop Safari 26 macOS, Mobile Chrome 147 Android, Mobile Safari 26 iOS. PyO3 bindings expose `validate_toml()` and `is_valid_toml()`. (#43, #66, #68–#74)
- `CarbonylBrowser(persona=...)` constructor accepts a typed `Persona` object — durable per-persona Chromium profile under `CARBONYL_AGENT_PROFILES_DIR` (default `~/.config/carbonyl-agent/profiles/`). File-locked, isolated from the runtime session store, portable across `input_backend="pty"` and `"uinput"`. Companion `purge_profile()`, `export_profile(path)`, `import_profile(path)` methods; `from carbonyl_agent import ProfileManager, list_personas`. `persona=` and `session=` are mutually exclusive. (#41, #45)
- Corpus-driven Chrome reference loader for the validator. (#68)

### Added — Persona-bound egress, Phase 1 (httpx + audit)

- `carbonyl_agent.egress.EgressClient(persona)` — persona-aware HTTP client over httpx. Per-(persona, host) connection pool, automatic header injection (UA, Accept-Language, sec-ch-ua-* for Chrome family), JSON Lines audit log at `~/.local/state/carbonyl-agent/egress-audit.log` (or `$XDG_STATE_HOME/carbonyl-agent/...`), drift detector, STRICT/WARN/OFF modes via `CARBONYL_FP_AUDIT`. Audit row fields: `request_id`, `timestamp`, `persona_id`, `method`, `url`, `ja4_expected`, `ja4_actual`, `status_code`, `latency_ms`, `drift`, `audit_mode`, `transport`. (#44)
- `CarbonylBrowser.egress() -> EgressClient` shorthand — returns a client bound to the browser's persona, with the cookie jar co-located inside the active profile so browser-set and API-set cookies converge. Raises if the browser was constructed with the string-form `persona="name"` (profile keying only). (#44)
- New `[egress]` extra pulling `httpx>=0.27`.

### Added — Persona-bound egress, Phase 2 (wreq + BoringSSL)

- New Rust crate `carbonyl-wreq` — implements the `carbonyl_fingerprint::http::HttpClient` trait against the `wreq` 5.x browser-emulating HTTPS client. Maps the persona's family + version to the closest `wreq_util::Emulation` preset (Chrome 147 → `Chrome137`, Firefox 150 → `Firefox139`, Safari 26 → `Safari18_3_1`, iOS Safari → `SafariIos17_4_1`). H2 SETTINGS overrides applied via `Http2Builder` on top of the preset. (#75, #80, #81)
- Layer 2 wire conformance test crate — drives a real wreq client through a localhost rustls responder, captures ClientHello + h2 preface bytes, composes a `WireSnapshot`, asserts `ConformanceFixture::assert_wire_state` against the fixture. Per-fixture baseline gap lists make residual divergence (e.g. wire.ja4 for Chrome 147 against wreq-util's Chrome 137 preset) load-bearing — any change to the gap set surfaces as a test failure. (#82)
- `carbonyl_agent.wreq_transport.WreqTransport` — an `httpx.BaseTransport` wrapper that delegates to the optional `carbonyl_wreq` native module. `EgressClient` auto-detects at construction; falls back to httpx silently when the native module isn't built. New audit-row `transport` field disambiguates `wreq` vs `httpx-fallback`. (#83)
- PyO3 binding in `carbonyl-wreq` — single `send_request` function with an embedded multi-threaded tokio runtime (`OnceLock<Runtime>`, shared across threads, GIL-released during IO). Build with `maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml --features python`. (#85)
- New `[wreq]` extra (declares intent; the native module is a developer build until pip-install integration lands). (#83)
- `.gitea/workflows/conformance.yml` — gates PRs touching fingerprint, wreq, or egress paths on `cargo {clippy,test} --workspace --all-targets --features carbonyl-wreq/python`. (#84)
- Rust workspace conversion: top-level `Cargo.toml` over both crates; single root `Cargo.lock`. (#80)

### Added — Operational

- `--carbonyl-cookie-flush-interval-ms=1000` flag wired into `CarbonylBrowser._spawn` for persona/session mode — cookies persist within the 5 s graceful_timeout instead of requiring a 30 s drain. Requires Carbonyl runtime `9b3ba53adcd8d330` or newer; older runtimes silently ignore the flag. (#51)

### Changed

- Carbonyl runtime pin bumped: `runtime-hash=dd69bef0ea4b2512` → `runtime-hash=9b3ba53adcd8d330` (Carbonyl v0.2.0-alpha.4). E2E compatibility matrix rotated so `dd69bef0ea4b2512` now sits in the prior-runtime slot. (#51)
- ADR-005 marked Accepted. New Phase 1 → Phase 2 transition notes covering the sentinel preservation in the fallback path, the `transport`-tag field, the Layer 1 vs Layer 2 conformance separation, and cross-references to the new artifacts. (#64, #75)
- `.gitea/workflows/check.yml` runs at workspace root and installs `clang cmake libclang-dev libssl-dev pkg-config python3-dev` before invoking cargo, for the boring-sys2 + PyO3 build path. (#80, #81, #85)
- `pyproject.toml` declares `[project.urls]` with GitHub canonical Homepage / Repository / Issues / Changelog for PyPI metadata. Project version bumped to `0.2.0a1`.

### Fixed

- `wreq-mirror` workflow skips early when `wreq-sha=registry` — the cold mirror anchors on git SHAs and registry-resolved deps use crates.io + `Cargo.lock` as the integrity record. (#81)
- CI typecheck + test jobs install `[dev,egress]` extras (not just `[dev]`) so httpx is available for mypy. (#44 follow-up)
- `wire_h2.rs` / `wire_ja4.rs` allow `clippy::collapsible_match` / `collapsible_if` (rust 1.95 added these lints; the nested ifs in these parsers carry distinct error-return paths). (#82)

## [0.1.0a1] - 2026-05-05

First test release of carbonyl-agent. The full feature set documented under
[Unreleased] above is included verbatim — this prerelease tag exists to
exercise the Gitea release pipeline (sdist + wheel + sha256 + pdoc bundle)
end-to-end. The 0.1.0 GA cut, including PyPI publish via OIDC trusted
publisher (#12), follows once the publisher is configured.

### Added
- Daemon transport contract pinned and documented (#47). The daemon is a **Unix domain socket** server (not TCP/HTTP) at `<session_dir>/<session>.sock`, with `session_dir` defaulting to `~/.local/share/carbonyl/sessions` and overridable via `CARBONYL_SESSION_DIR` or the `session_dir=` kwarg. New public re-exports `from carbonyl_agent import sock_path, DEFAULT_SOCKET_DIR` so consumers (CI fixtures, debug tooling) stop poking at private constants. New `DaemonClient.ping() -> bool` performs a semantic `hello` round-trip (returns `False` on any error, never raises) for readiness probes that need more than `is_daemon_live`'s TCP-style socket check. Container deployments documented in README — daemon + clients must share a filesystem path for the socket.
- `CarbonylBrowser.wait_for_render_settle(timeout=5.0, idle_ms=200, poll_ms=50)` — deterministic readiness probe for visual-capture tests. Pumps the PTY and hashes `page_text()` until the buffer has been stable for `idle_ms`, returning `True` on settle or `False` on timeout. Replaces wall-clock `drain()` heuristics; works in both direct and daemon-connected modes. (#48)
- `CarbonylBrowser(persona=...)` — durable per-persona browser profile management. Profiles live under `CARBONYL_AGENT_PROFILES_DIR` (default `~/.config/carbonyl-agent/profiles/`), isolated from the runtime session store. Companion `purge_profile()`, `export_profile(path)`, `import_profile(path)` methods on the browser; `from carbonyl_agent import ProfileManager, list_personas` for direct use. File lock with PID-in-error prevents accidental dual-open of the same persona; profiles are portable across `input_backend="pty"` and `input_backend="uinput"`. `persona=` and `session=` are mutually exclusive — `session=` remains the legacy SessionManager API. (#41)
- Daemon protocol gains a `hello` capability handshake. `DaemonClient.connect()` now records the daemon's `input_backend` and `protocol_version` for inspection. New constructor kwarg `DaemonClient(..., require_backend="uinput")` raises `BackendMismatchError` (also re-exported at the package root) when the daemon's actual backend doesn't match — useful for catching "client expects trusted input but daemon is in PTY mode" silent-fail scenarios. Old daemons (no `hello` cmd) are detected via the `Unknown command` reply and reported as `protocol_version=0, backend="pty"` (#40)
- `start_daemon(backend="uinput")` opt-in. The parent process pre-flights `/dev/uinput` access before forking so failures are visible to the caller. Default remains `"pty"` (#40)
- `carbonyl-agent daemon start <session> [url] --backend pty|uinput` CLI flag. Both the top-level `carbonyl-agent` CLI and the standalone `python -m carbonyl_agent.daemon` accept it (#40)
- `carbonyl-agent daemon status` gains a BACKEND column (#40)
- `daemon_status()` includes `input_backend` per session, sourced from the live handshake when the daemon is up or from session metadata when it's stopped (#40)
- New `carbonyl_agent.UinputEmitter` — emits keyboard and mouse events via `/dev/uinput` so the browser receives them as `event.isTrusted = true`. Required for scripted login on modern SPAs (X, LinkedIn, etc.) where React-controlled inputs reject synthetic events. Companion exceptions: `UinputUnavailableError`, `UnsupportedKeyError` (#36)
- `CarbonylBrowser(input_backend="uinput")` — opt-in routing of `send()`, `send_key()`, `click()`, `mouse_move()`, `mouse_path()` through the uinput emitter. Default remains `"pty"` for backward compatibility. Uinput backend requires the `carbonyl-agent-qa-runner` container (or equivalent Xorg + uinput environment); see ADR-002 rev 2 for the rationale (#36)
- CI workflow `.gitea/workflows/build-qa-runner.yml` that builds and publishes `carbonyl-agent-qa-runner` to the Gitea container registry on every push to `main` that touches `docker/qa-runner/**` or `.carbonyl-runtime-version`. Image tags: `runtime-<hash>`, `sha-<short-git-sha>`, plus `latest` on main. Includes a post-build smoke test inside the published image (uinput import + Carbonyl `--version`) (#38)
- `.carbonyl-runtime-version` pin file at the repo root — single source of truth for the Carbonyl runtime hash consumed by the SDK installer, the qa-runner Docker image, and CI workflows. New helper module `carbonyl_agent.runtime_pin` exposes `read_pinned_hash()` and `resolve_default_tag()` (#39)
- `docker/qa-runner/build.sh` — wrapper around `docker build` that reads the pin file and constructs the right `CARBONYL_RUNTIME_URL` build-arg automatically. `CARBONYL_RUNTIME_HASH` / `CARBONYL_RUNTIME_URL` env overrides supported (#39)
- `CARBONYL_RUNTIME_TAG` env var: explicit override that takes precedence over the pin file when running `carbonyl-agent install` (#39)

### Changed
- `carbonyl-agent install` now reads `.carbonyl-runtime-version` for the default `--tag` value when one is not specified on the CLI. `runtime-latest` becomes a deliberate opt-out rather than the silent default. The installer prints which path was taken (pin / env / latest-sentinel / unpinned) so operators can spot drift (#39)


## Foundational Features (pre-v0.1.0a1)

Initial feature inventory referenced verbatim by the v0.1.0a1 release entry above. Kept for historical record; not an active "Unreleased" section.

### Added
- `CarbonylBrowser` — PTY-driven browser automation with pyte terminal emulation
- `SessionManager` — named Chromium user-data-dir session management (create, fork, snapshot, restore)
- `ScreenInspector` — terminal coordinate visualization and region analysis
- `DaemonClient` and daemon server — persistent browser instances over Unix domain sockets
- `carbonyl-agent install` CLI — download Carbonyl runtime binary from Gitea releases
- `carbonyl-agent status` CLI — report installed runtime location and version
- `carbonyl-agent daemon {start,stop,status,attach}` CLI — manage persistent browser daemons
- `DaemonClient`, `start_daemon`, `stop_daemon` re-exported from top-level `carbonyl_agent` package for direct import
- Composable Chromium flag groups — agents can pick and choose per scenario: `DEFAULT_HEADLESS_FLAGS` (baseline), `BASE_CHROMIUM_FLAGS` (first-run / keychain suppression), `ANTI_BOT_FLAGS` (UA spoof, webdriver suppression, HTTP/1.1 fallback), `ANTI_FEDCM_FLAGS` / `ANTI_ONETAP_FLAGS` (disable Google One Tap overlays that interfere with scripted login on X/LinkedIn/publishers)
- `CarbonylBrowser(extra_flags=...)` and `base_flags=...` keyword args for composing flag sets
- Binary discovery: `CARBONYL_BIN` env → installed path → `$PATH` → Docker fallback
- SHA256 checksum verification for runtime tarball downloads
- Docker fallback opt-in gate (`CARBONYL_ALLOW_DOCKER=1`) with pinned image digest
- Session name validation (path traversal prevention, length limits)
- Comprehensive test suite: unit, integration, property tests (hypothesis)
- CI pipelines for Gitea Actions and GitHub Actions (py3.11/3.12/3.13 matrix)
- Type annotations with `mypy --strict` compliance
- Dependency pinning with `pip-audit` in CI

### Security
- SHA256 verification on runtime tarball downloads (mitigates supply-chain tampering)
- Unix socket permission hardening for daemon mode (0600 socket, 0700 parent dir)
- Docker fallback requires explicit opt-in via environment variable
- Session name validation prevents path traversal attacks
- Dependency audit via `pip-audit` in CI pipeline
