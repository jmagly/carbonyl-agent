# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
