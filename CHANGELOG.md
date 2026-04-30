# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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


## [0.1.0] - Unreleased

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
