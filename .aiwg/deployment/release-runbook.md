# Release Runbook — carbonyl-agent

- **Version**: 1.0 (BASELINED)
- **Date**: 2026-04-09
- **Audience**: maintainer cutting a release
- **Scope**: every `carbonyl-agent` version tag, from `v0.1.0` onward

This runbook is the canonical procedure for releasing `carbonyl-agent` to PyPI. It assumes the CI pipeline in `.aiwg/deployment/ci-cd-scaffold.md` is in place and PyPI Trusted Publisher is configured for the GitHub mirror.

## 0. One-Time PyPI Trusted Publisher Setup

Only performed once, before the first release. Complete before attempting v0.1.0.

### 0.1. Register the PyPI project name

1. Log in at [https://pypi.org/account/login/](https://pypi.org/account/login/)
2. If no prior `carbonyl-agent` release exists, the name is available on first publish. Skip to 0.2.
3. Verify the name is not squatted: [https://pypi.org/project/carbonyl-agent/](https://pypi.org/project/carbonyl-agent/)

### 0.2. Configure the trusted publisher

1. Navigate to [https://pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/)
2. Under **Add a new pending publisher**, enter:
   - **PyPI Project Name**: `carbonyl-agent`
   - **Owner**: `jmagly`
   - **Repository name**: `carbonyl-agent`
   - **Workflow name**: `ci.yml`
   - **Environment name**: `release`
3. Click **Add**. PyPI creates a pending trusted publisher that activates on first successful OIDC publish.

### 0.3. Configure the GitHub environment

1. At [https://github.com/jmagly/carbonyl-agent/settings/environments](https://github.com/jmagly/carbonyl-agent/settings/environments):
   - Create environment named `release`
   - (Optional) Add a required reviewer for extra gating on tag pushes
   - (Optional) Restrict to `refs/tags/v*`
2. No PyPI API token needs to be stored — OIDC handles auth.

### 0.4. Verify via TestPyPI (recommended before first prod publish)

1. Configure a second pending publisher on [TestPyPI](https://test.pypi.org/manage/account/publishing/) with the same settings
2. Add a `workflow_dispatch` trigger to `.github/workflows/ci.yml` that publishes to TestPyPI instead of PyPI
3. Run the workflow manually, verify package appears at [https://test.pypi.org/project/carbonyl-agent/](https://test.pypi.org/project/carbonyl-agent/)
4. In a clean venv: `pip install --index-url https://test.pypi.org/simple/ carbonyl-agent`

### 0.5. Confirm readiness

- [ ] PyPI pending publisher created
- [ ] GitHub `release` environment exists
- [ ] `.github/workflows/ci.yml` `publish` job uses `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` permission
- [ ] (Optional) TestPyPI dry-run succeeded

## 1. Pre-Release Checklist

Complete **all** items before tagging. Check off as you go.

- [ ] `main` branch CI is green on Gitea **and** GitHub for the latest commit
- [ ] All iteration stories for this release are merged and closed
- [ ] `CHANGELOG.md` has a finalized section for the new version (move items out of `Unreleased`, add release date, preserve Keep-a-Changelog format)
- [ ] `pyproject.toml` `version` field bumped (semver: PATCH for bugfix, MINOR for additive, MAJOR for breaking)
- [ ] `README.md` install and quick-start examples verified in a fresh venv
- [ ] All ADRs reflect the shipped design; no pending ADRs in `drafts/`
- [ ] `.aiwg/security/threat-model.md` reviewed; no HIGH unmitigated items unless explicitly accepted
- [ ] No `TODO`/`FIXME` without a linked issue
- [ ] `mypy --strict` and `ruff check` pass locally
- [ ] Coverage gate satisfied locally (`pytest --cov` ≥ 50% line for v0.1.0; raise to 80% after E2E tests land in #15)
- [ ] `pip-audit` clean
- [ ] Dry-run build succeeds: `python -m build` produces sdist + wheel in `dist/`
- [ ] `twine check dist/*` passes
- [ ] (Optional) Smoke-tested against TestPyPI for MAJOR or MINOR releases

## 2. Tag Creation

Tag on **Gitea first** (primary remote), then confirm the mirror propagates to GitHub.

```bash
# Sync and confirm clean
git checkout main
git pull origin main
git status  # must be clean

# Tag
VERSION="0.1.0"
git tag -a "v${VERSION}" -m "carbonyl-agent v${VERSION}"

# Push to Gitea (origin) first
git push origin "v${VERSION}"

# Verify mirror picked up the tag on GitHub (may take ~60s)
git fetch github --tags
git show-ref "refs/tags/v${VERSION}"

# If the mirror is lagging, push explicitly
git push github "v${VERSION}"
```

Semver rules:

| Change type | Bump |
|-------------|------|
| Bugfix, doc fix, internal refactor | PATCH (`0.1.0 → 0.1.1`) |
| New feature, new public API, backwards-compatible | MINOR (`0.1.0 → 0.2.0`) |
| Removed or changed public API, changed wire protocol | MAJOR (`0.1.0 → 1.0.0`) |

Pre-1.0 note: breaking changes in 0.x bump MINOR rather than MAJOR, per semver §4.

## 3. Automated PyPI Publish

On tag push to GitHub, `.github/workflows/ci.yml` runs:

1. Full test matrix (unit + integration + smoke).
2. Build `sdist` and `wheel` via `hatchling`.
3. Upload artifacts.
4. `publish` job uses OIDC Trusted Publisher to push to `https://pypi.org/project/carbonyl-agent/`.

**Monitor** the workflow at `https://github.com/jmagly/carbonyl-agent/actions`. Do not delete the tag while the publish is in flight.

## 4. Post-Release Verification

Run these checks from a clean environment within 30 minutes of publish.

```bash
# 1. Clean venv install
python3 -m venv /tmp/carbonyl-verify
source /tmp/carbonyl-verify/bin/activate
pip install "carbonyl-agent==${VERSION}"

# 2. Import smoke test
python -c "from carbonyl_agent import CarbonylBrowser, SessionManager, ScreenInspector; print('ok')"

# 3. CLI smoke test
carbonyl-agent --help
carbonyl-agent status

# 4. Runtime install works
carbonyl-agent install

# 5. End-to-end smoke
python -c "
from carbonyl_agent import CarbonylBrowser
b = CarbonylBrowser()
b.open('https://example.com')
b.drain(8.0)
assert 'example' in b.page_text().lower()
b.close()
print('e2e ok')
"

deactivate
rm -rf /tmp/carbonyl-verify
```

If any step fails, treat it as a **production incident** and proceed to §6 Rollback.

## 5. Announcements

- **Gitea Release**: create a release from the tag at `https://git.integrolabs.net/roctinam/carbonyl-agent/releases`. Paste the `CHANGELOG.md` section for this version as the release body.
- **GitHub Release**: mirror the same release at `https://github.com/jmagly/carbonyl-agent/releases`. Link back to the Gitea release as the upstream source.
- Update the `README.md` badge / install snippet if this is the first release.

## 6. Rollback Procedure

PyPI does not support hard deletes of versions. The rollback path is **yank + superseding release**.

1. **Yank** the bad version from PyPI:
   ```bash
   # Via web UI at https://pypi.org/manage/project/carbonyl-agent/releases/
   # Or via twine (requires token):
   # yanking is a manage-UI operation; there is no twine CLI for it
   ```
2. **Document** the yank in `CHANGELOG.md`:
   ```markdown
   ## [0.1.0] - 2026-04-09 [YANKED]
   Yanked due to <reason>. Users should install 0.1.1 or later.
   ```
3. **Cut a superseding release** (PATCH bump) that fixes the issue. Repeat this runbook from §1.
4. **File a post-incident ADR** in `.aiwg/architecture/adrs/` describing root cause and process improvement.
5. **Notify** in the Gitea and GitHub release notes of the superseding version that the prior version was yanked.

## 7. Release History Log

Maintained in `CHANGELOG.md`. Every release tag corresponds to a `CHANGELOG.md` entry with the same version number and release date.
