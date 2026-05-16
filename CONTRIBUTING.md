# Contributing to carbonyl-agent

## Development Setup

### Python SDK

```bash
# Clone the repository
git clone https://git.integrolabs.net/roctinam/carbonyl-agent.git
cd carbonyl-agent

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]" hypothesis mypy ruff pytest-cov

# Install the Carbonyl runtime binary (optional — needed for integration/E2E tests)
carbonyl-agent install
```

### Rust workspace prerequisites

The `crates/carbonyl-wreq` crate depends on `wreq` → `boring-sys2` (BoringSSL
fork). The boring-sys2 build script runs `bindgen` over the BoringSSL C headers
and needs both a C toolchain AND a working libclang with access to `<stddef.h>`.
Without these, `cargo build -p carbonyl-wreq` panics with a confusing
`'stddef.h' file not found` error from inside the boring-sys2 build script
(`Refs: roctinam/carbonyl-agent#108`).

Install once per dev host:

```bash
# Debian / Ubuntu
sudo apt-get install -y clang cmake libclang-dev libssl-dev pkg-config python3-dev

# Fedora / RHEL
sudo dnf install -y clang clang-devel cmake openssl-devel pkgconf-pkg-config python3-devel
```

Then preflight-check the environment before the first cargo build:

```bash
./scripts/check-build-env.sh
# ✓ Build environment OK (clang 18.1.3, cmake 3.28.3)

cargo test -p carbonyl-wreq --no-run
```

If you see `'stddef.h' file not found` despite having `libclang-dev` installed
(common on minimal containers without `libclang-rt-*-dev`), point bindgen at
gcc's sysroot in `.cargo/config.toml` or your shell env:

```bash
export BINDGEN_EXTRA_CLANG_ARGS="-I$(gcc -print-file-name=include)"
```

The CI workflow (`.gitea/workflows/check.yml`) installs the same set on the
`rust:latest` container before every clippy / test job, so green CI is the
authoritative reference for a known-good environment.

## Running Tests

```bash
# All tests (unit + integration, skips E2E if binary absent)
pytest tests/ -x -q

# With coverage report
pytest tests/ --cov=carbonyl_agent --cov-report=term-missing

# Specific test file
pytest tests/test_session.py -v
```

## Code Quality

```bash
# Linting
ruff check src/ tests/

# Auto-fix lint issues
ruff check src/ tests/ --fix

# Type checking (strict mode)
mypy --strict src/carbonyl_agent/

# Dependency audit
pip-audit

# End-to-end tests (#15) — require a real Carbonyl binary + network
carbonyl-agent install                    # one-time
pytest tests/e2e/ -v                      # ~2 min wall-clock

# Performance benchmarks (#22)
pip install -e ".[bench]"
pytest tests/benchmarks/ --benchmark-only

# API reference docs (#17)
pip install -e ".[docs]"
./scripts/build-docs.sh           # writes docs/api/*.html
./scripts/build-docs.sh --serve   # preview at http://localhost:8080
```

## Code Style

- **Linter**: ruff (E, F, W, I rules; line length 100)
- **Type hints**: mypy strict — all public APIs must be fully annotated
- **Python**: >=3.11, use modern syntax (match, `X | Y` unions, etc.)
- **Commits**: conventional format preferred (`feat:`, `fix:`, `docs:`, `test:`, `chore:`)

## Project Structure

```
src/carbonyl_agent/
    __init__.py          # Public API exports
    __main__.py          # CLI entry point
    browser.py           # CarbonylBrowser: PTY + pyte terminal emulation
    daemon.py            # DaemonClient + Unix socket daemon server
    install.py           # Runtime binary download with SHA256 verification
    screen_inspector.py  # Coordinate visualization and region analysis
    session.py           # Named session management
tests/
    test_smoke.py              # Import + basic smoke tests
    test_browser.py            # Browser module tests
    test_install.py            # Install module tests
    test_session.py            # Session manager tests
    test_screen_inspector.py   # ScreenInspector tests (with hypothesis)
    test_daemon_integration.py # Daemon protocol integration tests
```

## Architecture Decisions

Key design decisions are documented as ADRs in `.aiwg/architecture/`:
- ADR-001: PTY + pyte terminal emulation
- ADR-002: Unix socket daemon
- ADR-003: Runtime binary discovery order
- ADR-004: Gitea release runtime distribution

## Release Process

See the [release runbook](.aiwg/deployment/release-runbook.md) for the full release checklist.

Quick summary:
1. Ensure CI is green, CHANGELOG updated, version bumped
2. Tag on Gitea (origin): `git tag v0.X.Y && git push origin v0.X.Y`
3. Push tag to GitHub mirror: `git push github v0.X.Y`
4. GitHub Actions publishes to PyPI via trusted publisher
5. Verify: `pip install carbonyl-agent==0.X.Y` in clean venv

## Links

- [CHANGELOG.md](CHANGELOG.md)
- [README.md](README.md)
- [CLAUDE.md](CLAUDE.md) — AI assistant context
- [.aiwg/reports/construction-ready-brief.md](.aiwg/reports/construction-ready-brief.md) — SDLC overview
