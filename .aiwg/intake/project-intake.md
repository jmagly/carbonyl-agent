# Project Intake Form (Existing System)

**Document Type**: Brownfield System Documentation
**Generated**: 2026-04-09 (from codebase analysis)
**Source**: `/srv/vmshare/dev-inbox/carbonyl/carbonyl-agent`

## Metadata

- **Project name**: carbonyl-agent
- **Repository**: roctinam/carbonyl-agent (Gitea, primary) / jmagly/carbonyl-agent (GitHub mirror)
- **Current Version**: 0.1.0
- **License**: MIT
- **Last Updated**: Recent (2 commits in history — fresh extraction)
- **Stakeholders**: Joseph Magly (sole contributor)

## System Overview

**Purpose**: Python automation SDK for the Carbonyl headless browser. Provides a high-level Python API (PTY + pyte terminal emulation) to drive Carbonyl programmatically — open pages, read rendered text, manage sessions, and run a daemon for persistent browser instances.

**Current Status**: Pre-release / early extraction (v0.1.0). Recently extracted from `roctinam/carbonyl`'s `automation/` directory into a standalone package.

**Users**: Unknown (no released distribution yet; intended for developers needing programmatic Carbonyl control).

**Tech Stack**:
- Language: Python ≥3.11 (100%)
- Runtime deps: `pexpect>=4.9`, `pyte>=0.8`
- Build: hatchling
- External runtime: Carbonyl binary (downloaded from Gitea releases or via Docker)
- Tests: pytest

## Problem and Outcomes

**Problem Statement**: Carbonyl is a Chromium-based headless browser that renders to a terminal. Developers need a Python automation layer to script it (browser control, session persistence, daemon mode) without re-implementing PTY/terminal-emulation glue per project.

**Target Personas**: Python developers building scrapers, automation, or headless browsing pipelines who want Carbonyl's lightweight footprint without Selenium/Playwright overhead.

**Success Metrics**: Not formally defined. Implicit: package installable via pip, smoke tests pass, binary auto-install works on Linux/macOS.

## Current Scope and Features

**Public API** (from `src/carbonyl_agent/__init__.py`):
- `CarbonylBrowser` — PTY-driven browser wrapper with pyte terminal emulation
- `SessionManager` — named user-data-dir session management
- `ScreenInspector` — coordinate visualization and region summaries
- `DaemonClient` + daemon server — Unix-socket-based persistent browser instance

**CLI** (`carbonyl-agent`):
- `install` — download Carbonyl runtime tarball from Gitea releases
- `status` — report installed runtime state

**Binary discovery order**: `CARBONYL_BIN` env → `~/.local/share/carbonyl/bin/<triple>/` → `$PATH` → Docker fallback.

## Architecture (Current State)

**Style**: Single-process Python library + thin CLI. Optional daemon process (Unix socket IPC).

**Components** (`src/carbonyl_agent/`):
- `browser.py` — `CarbonylBrowser`: PTY spawn + pyte emulation
- `session.py` — named session directories
- `screen_inspector.py` — coordinate/region utilities
- `daemon.py` — DaemonClient + Unix-socket server
- `install.py` — runtime tarball download/extract
- `__main__.py` — CLI entry point

**Data persistence**: File system only. Session state in `~/.local/share/carbonyl/sessions/<name>/`. Runtime binary in `~/.local/share/carbonyl/bin/`.

**Integration points**:
- Carbonyl native binary (subprocess via PTY)
- Gitea releases API (runtime tarball downloads)
- Docker (fallback runtime)

## Scale and Performance

**Scale model**: Single-developer, per-process library. Not a service. Scale = number of Carbonyl processes the host can run.

**Performance characteristics**: Bound by Carbonyl binary + PTY throughput. No caching, queueing, or concurrency primitives in the SDK itself beyond the daemon's socket loop.

## Security and Compliance

**Security posture**: Minimal — appropriate for a developer SDK.
- No authentication (local library)
- Daemon uses Unix domain sockets (filesystem permission boundary)
- Runtime download: HTTPS from Gitea; **no checksum verification detected** (potential gap)
- No PII, payments, or regulated data handled by the SDK itself

**Compliance**: None applicable (MIT-licensed open-source developer tool).

## Team and Operations

**Team size**: 1 (Joseph Magly, sole contributor)
**Velocity**: 2 commits in history (just extracted)
**Process maturity**: Lightweight
- Git: dual remote (Gitea primary, GitHub mirror)
- Tests: smoke tests in `tests/test_smoke.py`
- CI/CD: not detected
- Docs: README + CLAUDE.md

**Operational support**: N/A (library, not a service)

## Dependencies and Infrastructure

**Runtime**: pexpect, pyte
**Dev**: pytest
**External**: Carbonyl binary (~75 MB tarballs hosted at `roctinam/carbonyl` releases, tagged `runtime-<hash>`)

## Known Issues and Technical Debt

- No CI/CD pipeline configured
- No checksum verification on runtime tarball download
- Smoke tests only — no integration coverage of daemon mode, session persistence, or screen inspector
- No published PyPI release (v0.1.0 unreleased)
- Single-platform install logic needs validation across Linux triples + macOS

## Why This Intake Now?

Project was just extracted from a parent repo and needs baseline SDLC documentation before adding features, releasing publicly, or onboarding contributors. Establishing intake to inform whether to invest in process infrastructure (CI, tests, release automation) now or later.

## Attachments

- Solution profile: `solution-profile.md`
- Option matrix: `option-matrix.md`
- Source: `src/carbonyl_agent/`
- Parent repo: `roctinam/carbonyl` (owns Chromium build + Rust FFI)

## Next Steps

1. Review intake for accuracy
2. Decide on rigor level (see option-matrix.md)
3. If proceeding with SDLC: `/flow-concept-to-inception`
