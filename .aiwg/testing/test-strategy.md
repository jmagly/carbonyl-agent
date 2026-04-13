# Test Strategy — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09
- **Owner**: Joseph Magly (maintainer)
- **Applies to**: `carbonyl-agent` v0.1.0 and subsequent minor releases
- **Status**: Approved for Construction

## 1. Scope

This strategy covers automated and manual testing of the `carbonyl-agent` Python SDK — a PTY + pyte-based automation layer over the Carbonyl headless browser. Testing covers the public API (`CarbonylBrowser`, `SessionManager`, `ScreenInspector`, `DaemonClient`), the CLI (`carbonyl-agent install|status`), and the runtime install pipeline.

**In scope**: package import surface, browser spawn/PTY behavior, pyte screen parsing, session directory lifecycle, daemon Unix-socket RPC, install tarball download/extract, platform triple detection, CLI exit codes.

**Out of scope**: the Carbonyl binary itself (owned by `roctinam/carbonyl`), Chromium rendering correctness, upstream pexpect/pyte behavior, network/DNS infrastructure.

## 2. Test Levels

| Level | Purpose | Requires binary? | Location |
|-------|---------|------------------|----------|
| **Unit** | Exercise pure-Python logic in isolation (screen_inspector, session paths, install helpers, daemon protocol codec) | No | `tests/unit/` |
| **Integration** | Exercise subsystems with mocked or containerized collaborators (daemon over real Unix socket with stub browser, install with local HTTP server) | No (mocked) | `tests/integration/` |
| **End-to-end (E2E)** | Drive a real Carbonyl binary against real pages (local static fixture server + `https://example.com`) | Yes | `tests/e2e/` |
| **Smoke** | Fast import + basic instantiation; first job in CI | No | `tests/test_smoke.py` |

Each level has its own pytest marker (`@pytest.mark.unit`, `integration`, `e2e`, `smoke`) registered in `pyproject.toml`.

## 3. Test Types

- **Functional**: command/response correctness for every public method on `CarbonylBrowser` and `DaemonClient`.
- **Regression**: every fixed defect gains a pinned test referencing the issue ID.
- **Compatibility**: matrix across Python 3.11, 3.12, 3.13 on Linux x86_64, Linux aarch64, and macOS (arm64). Docker fallback path validated on Linux x86_64 only.
- **Property-based**: `hypothesis` strategies for terminal byte sequences fed into pyte, asserting the `ScreenInspector` view invariants (row/col bounds, line-text round-trip).
- **Contract**: daemon JSON wire protocol validated against a frozen schema (request shape, response shape, error envelope).
- **Install verification**: SHA256 checksum enforcement (post US-002), tarball path-traversal rejection, idempotent reinstall.

## 4. Tooling

| Purpose | Tool |
|---------|------|
| Runner | `pytest>=8.0` |
| Coverage | `pytest-cov` (line + branch) |
| Matrix orchestration | `nox` (preferred over tox for Python-based config) |
| Property tests | `hypothesis>=6` |
| Lint | `ruff` |
| Type check | `mypy --strict` on `src/carbonyl_agent/` |
| HTTP mocking | `pytest-httpserver` for `install.py` integration |
| Integration runtime | `docker run fathyb/carbonyl` for CI E2E on Linux |

All dev tooling lives in `pyproject.toml` under `[project.optional-dependencies] dev`.

## 5. Coverage Targets

| Metric | Target | Gate |
|--------|--------|------|
| Line coverage (package-wide) | ≥ 80% | Fail CI below |
| Branch coverage (package-wide) | ≥ 70% | Fail CI below |
| Line coverage (`install.py`, `daemon.py`) | ≥ 90% | Fail CI below |
| Public API method coverage | 100% (every method exercised at least once) | Fail CI below |

Coverage is computed on the unit + integration tiers; E2E runs are additive.

## 6. Test Data Strategy

- **Unit**: no external resources. Stub `CarbonylBrowser` with a fake PTY that replays recorded byte streams fixtured under `tests/fixtures/pty/`.
- **Integration**: local `pytest-httpserver` serves canned Gitea releases JSON and tarballs (generated on the fly). Daemon tests bind to a `tmp_path` socket and use a stub browser object implementing the `CarbonylBrowser` duck-type.
- **E2E**: `docker run --rm fathyb/carbonyl` invoked through the install/Docker fallback paths. Pages under test: `https://example.com` (stable public) and a local `http.server` serving `tests/fixtures/pages/*.html`.
- **Fixtures**: all binary fixtures ≤ 10 KB each; larger recordings compressed as `.xz`.

## 7. Environments

| Environment | Triggered by | Purpose |
|-------------|--------------|---------|
| Developer laptop | `nox -s tests` | Rapid feedback |
| Gitea Actions CI | push / PR to `main` | Unit + integration + smoke across matrix |
| GitHub Actions CI (mirror) | push to mirror | Same as Gitea, mirrored for public visibility |
| E2E runner | nightly scheduled + release tag | Docker-based E2E |

## 8. Entry / Exit Criteria

**Unit tests**
- Entry: code compiles, imports succeed.
- Exit: ≥ 80% line / 70% branch, zero failing, zero xfail-without-issue.

**Integration tests**
- Entry: unit tier green.
- Exit: daemon protocol contract tests pass; install pipeline passes checksum + tarball safety tests.

**E2E tests**
- Entry: integration tier green, `fathyb/carbonyl` image pullable.
- Exit: every public API example in README executes without error.

**Release gate**
- All tiers green on all matrix cells for the Python × OS combinations declared supported in `pyproject.toml`.

## 9. Defect Management

- Defects filed as Gitea issues on `roctinam/carbonyl-agent` with label `bug`.
- Every defect fix ships with a regression test referencing the issue number in the test docstring.
- Severity: Blocker (release-blocking), Major (user-visible incorrect behavior), Minor (UX/docs), Trivial.

## 10. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Flaky PTY timing in E2E | Fixed `drain()` budgets with retries; quarantine marker `@pytest.mark.flaky` feeding `flaky-detect` skill |
| Carbonyl binary drift breaks E2E | Pin runtime tag in CI; bump deliberately with its own PR |
| Matrix cost balloons | Skip aarch64 on PR, run on `main` + release tags only |
| Docker unavailable in CI | `pytest.importorskip` + marker `requires_docker`; still run rest of suite |
| Solo maintainer bandwidth | Automate ruthlessly; invest in CI over manual QA |

## 11. Traceability

Test IDs follow `TEST-<module>-<nnn>`. Each test links to a requirement or issue in its docstring. Coverage + traceability reports generated by `pytest --junitxml` and the `check-traceability` skill.
