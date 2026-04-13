# Contributing to carbonyl-agent

## Development Setup

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
