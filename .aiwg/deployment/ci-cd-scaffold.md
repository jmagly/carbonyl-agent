# CI/CD Scaffold — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09
- **Status**: Approved for implementation (US-001)

## Strategy

`carbonyl-agent` uses **Gitea Actions as the primary CI** (matching the primary remote `roctinam/carbonyl-agent`) and **GitHub Actions as the mirrored secondary CI** (for public visibility on `jmagly/carbonyl-agent`). Both pipelines run the same stages; the Gitea pipeline is the authoritative gate for merges and releases.

## Pipeline Stages

| Stage | Tool | Gate | Runtime |
|-------|------|------|---------|
| 1. Checkout | `actions/checkout@v4` | — | ~5s |
| 2. Setup Python | `actions/setup-python@v5` | — | ~10s |
| 3. Install | `pip install -e .[dev]` + `pip-audit` | fail on vuln | ~30s |
| 4. Lint | `ruff check src tests` | fail on error | ~5s |
| 5. Type check | `mypy --strict src/carbonyl_agent` | fail on error | ~15s |
| 6. Unit + smoke tests | `pytest -m "unit or smoke" --cov` | coverage gates | ~30s |
| 7. Integration tests | `pytest -m integration --cov --cov-append` | coverage gates | ~1–2 min |
| 8. Coverage report | `coverage report --fail-under=80` | < 80% fails | ~2s |
| 9. Build | `python -m build` (hatchling) | — | ~10s |
| 10. Release (tag only) | PyPI Trusted Publisher via OIDC | tag push only | ~20s |

Matrix: `python-version: [3.11, 3.12, 3.13]` × `os: [ubuntu-latest, macos-latest]`. To control cost, macos is restricted to `python-version: 3.12` on PR runs and expanded to the full set on `main` / release tags.

## Gitea Actions — `.gitea/workflows/ci.yml`

```yaml
name: ci

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest]
        python-version: ['3.11', '3.12', '3.13']
        include:
          - os: macos-latest
            python-version: '3.12'
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
          pip install pip-audit ruff mypy pytest-cov nox hypothesis pytest-httpserver
      - name: Audit dependencies
        run: pip-audit --strict
      - name: Lint
        run: ruff check src tests
      - name: Type check
        run: mypy --strict src/carbonyl_agent
      - name: Unit + smoke tests
        run: pytest -m "unit or smoke" --cov=carbonyl_agent --cov-branch
      - name: Integration tests
        run: pytest -m integration --cov=carbonyl_agent --cov-branch --cov-append
      - name: Coverage gate
        run: |
          coverage report --fail-under=80
      - name: Build distributions
        if: matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'
        run: |
          pip install build
          python -m build
      - name: Upload dist artifacts
        if: matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: test
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Publish to PyPI (OIDC Trusted Publisher)
        uses: pypa/gh-action-pypi-publish@release/v1
```

## GitHub Actions — `.github/workflows/ci.yml`

Identical pipeline, mirrored. The GitHub workflow is the one that actually executes the Trusted Publisher OIDC flow (PyPI Trusted Publishers are wired to GitHub Actions, not Gitea). Gitea CI remains the authoritative test gate; GitHub CI is authoritative for publishing.

```yaml
name: ci

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest]
        python-version: ['3.11', '3.12', '3.13']
        include:
          - os: macos-latest
            python-version: '3.12'
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
          pip install pip-audit ruff mypy pytest-cov nox hypothesis pytest-httpserver
      - run: pip-audit --strict
      - run: ruff check src tests
      - run: mypy --strict src/carbonyl_agent
      - run: pytest -m "unit or smoke" --cov=carbonyl_agent --cov-branch
      - run: pytest -m integration --cov=carbonyl_agent --cov-branch --cov-append
      - run: coverage report --fail-under=80
      - if: matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'
        run: |
          pip install build
          python -m build
      - if: matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'
        uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: test
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1
```

## Secrets Management

- **PyPI publishing**: use a **PyPI Trusted Publisher** (OIDC) bound to `jmagly/carbonyl-agent` on GitHub. No API token stored in Gitea or GitHub secrets. Configure at https://pypi.org/manage/project/carbonyl-agent/settings/publishing/.
- **Gitea token for mirror**: the Gitea→GitHub mirror push is configured in Gitea repo settings (Settings → Mirrors), using a GitHub PAT scoped to `public_repo` only. Rotate annually.
- **No other secrets** required. The project has no cloud resources, no API clients, no service accounts.

## Release Process

1. Developer cuts and tags a release on `main` at Gitea (`git tag v0.1.0 && git push origin v0.1.0`).
2. Gitea mirror job pushes the tag to GitHub within ~60s.
3. GitHub Actions CI runs the full matrix, builds `sdist` + `wheel`, and on success the `publish` job uses OIDC Trusted Publisher to push to PyPI.
4. A Gitea Release is created from the tag with release notes pulled from `CHANGELOG.md`.
5. A GitHub Release is created by the mirror reflecting the Gitea release.

See `.aiwg/deployment/release-runbook.md` for the full runbook.

## Release Runbook (summary)

Full procedure lives in `release-runbook.md`. Pre-flight: CI green on `main`, `CHANGELOG.md` updated, version bumped in `pyproject.toml`, README examples smoke-tested locally, ADRs current. Cut tag on Gitea first, verify mirror, wait for GitHub CI, verify PyPI artifact, smoke-test `pip install` in a clean venv.

## Future Enhancements

- SLSA provenance via `pypa/gh-action-pypi-publish` attestations.
- Sigstore signing of wheels.
- Nightly E2E job against pinned Carbonyl runtime tags.
- Publish coverage to Codecov / Coveralls for historical tracking.
