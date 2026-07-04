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
   - **Workflow name**: `release.yml`
   - **Environment name**: `release`
3. Click **Add**. PyPI creates a pending trusted publisher that activates on first successful OIDC publish.

### 0.3. Configure the GitHub environment

1. At [https://github.com/jmagly/carbonyl-agent/settings/environments](https://github.com/jmagly/carbonyl-agent/settings/environments):
   - Create environment named `release`
   - (Optional) Add a required reviewer for extra gating on tag pushes
   - (Optional) Restrict to `refs/tags/v*`
2. No PyPI API token needs to be stored — OIDC handles auth.

### 0.4. Verify via TestPyPI (recommended before first prod publish)

1. Configure a second pending publisher on [TestPyPI](https://test.pypi.org/manage/account/publishing/):
   - **PyPI Project Name**: `carbonyl-agent`
   - **Owner**: `jmagly`
   - **Repository name**: `carbonyl-agent`
   - **Workflow name**: `release-testpypi.yml`  ⚠ different from production
   - **Environment name**: `release-testpypi`  ⚠ different from production
2. Create the matching `release-testpypi` GitHub environment at [https://github.com/jmagly/carbonyl-agent/settings/environments](https://github.com/jmagly/carbonyl-agent/settings/environments). No required reviewers needed for dry-runs.
3. Trigger `.github/workflows/release-testpypi.yml` manually from the GitHub Actions UI (the workflow is `workflow_dispatch`-only). Supply the current `pyproject.toml` version (no leading `v`) as the `version` input — the workflow verifies it matches `pyproject.toml` before building.
4. Verify the package appears at [https://test.pypi.org/project/carbonyl-agent/](https://test.pypi.org/project/carbonyl-agent/).
5. In a clean venv: `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ carbonyl-agent` (the `--extra-index-url` lets pip resolve runtime dependencies — TestPyPI doesn't mirror the full dep graph).

### 0.5. Confirm readiness

- [ ] PyPI pending publisher created (workflow: `release.yml`, env: `release`)
- [ ] GitHub `release` environment exists
- [ ] `.github/workflows/release.yml` `publish` job uses `pypa/gh-action-pypi-publish@release/v1` with `id-token: write` permission
- [ ] (Optional, recommended for first-of-a-line releases) TestPyPI dry-run succeeded
  - [ ] TestPyPI pending publisher created (workflow: `release-testpypi.yml`, env: `release-testpypi`)
  - [ ] GitHub `release-testpypi` environment exists
  - [ ] `release-testpypi.yml` ran successfully via `workflow_dispatch` and the artefact is visible at https://test.pypi.org/project/carbonyl-agent/

## 1. Pre-Release Checklist

Complete **all** items before tagging. Check off as you go.

- [ ] `main` branch CI is green on Gitea **and** GitHub for the latest commit
- [ ] All iteration stories for this release are merged and closed
- [ ] `CHANGELOG.md` has a finalized section for the new version (move items out of `Unreleased`, add release date, preserve Keep-a-Changelog format)
- [ ] `pyproject.toml` `version` field bumped (CalVer `YYYY.M.PATCH`, no leading zeros — reset PATCH to 0 on a new `YYYY.M`, else increment PATCH; see docs/versioning.md)
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
- [ ] Internal QA smoke suite passes (`cd ../carbonyl-agent-qa && pytest tests/smoke/ -v`) — verifies bot-detection stack against real sites. Lives in private `roctinam/carbonyl-agent-qa` repo. Not in CI; gates release for any change touching `_HEADLESS_FLAGS` in `browser.py`

## 2. Tag Creation

Tag on **Gitea first** (primary remote), then confirm the mirror propagates to GitHub.

```bash
# Sync and confirm clean
git checkout main
git pull origin main
git status  # must be clean

# Tag  (project uses CalVer: YYYY.M.PATCH, no leading zeros — see docs/versioning.md)
VERSION="2026.7.0"
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

## 3. Automated Release Publishing

Two workflows fire on tag push, each handling its own platform:

### 3.0. Gitea release (primary)

On tag push to Gitea origin, `.gitea/workflows/release.yml` runs (#11):

1. **Resolve tag** — verify tag matches `pyproject.toml` version (fail-fast on mismatch).
2. **Build** — `hatch build` produces sdist + wheel; `twine check` validates.
3. **Stage** — copies artefacts plus `*.sha256` sidecars; optionally bundles the pdoc API reference as `carbonyl-agent-<version>-docs.tar.gz`.
4. **Resolve notes** — extract the `## [VERSION]` section from `CHANGELOG.md`; falls back to a generic body if missing.
5. **Create / reuse release** — idempotent: re-running against the same tag reuses the existing release.
6. **Upload assets** — replaces same-name assets so re-runs are safe.

Pre-release detection: tags matching `-(alpha|beta|rc)` are flagged as pre-release.

**Token**: prefers `secrets.RELEASE_TOKEN` (Gitea PAT with `repository:write`); falls back to the auto-injected `github.token` for the running workflow.

**Manual trigger**: `workflow_dispatch` accepts an existing tag name and a `include_docs=true|false` toggle for re-running without rebuilding docs.

### 3.1. PyPI publish (GitHub-side)

PyPI's trusted-publisher OIDC integration only supports GitHub Actions, GitLab CI, Google Cloud, and ActiveState (not Gitea Actions). So PyPI publishing lives on the GitHub mirror.

On tag push to GitHub (mirrored from Gitea), `.github/workflows/release.yml` runs:

1. **build** — Verify tag matches `pyproject.toml` version (fail-fast on mismatch); build `sdist` and `wheel` via `hatch build`; `twine check` validates the artifacts.
2. **publish** — uses OIDC Trusted Publisher to push to `https://pypi.org/project/carbonyl-agent/` — no API token stored.
3. **github-release** — mirrors the Gitea release to GitHub: extracts the matching `CHANGELOG.md` section, creates the GitHub release with the same notes, and uploads the sdist + wheel as release assets (#18).

**Pre-flight expectation**: Gitea CI must already be green for the commit being tagged. The release workflow does not re-run the test suite — it trusts Gitea CI.

### 3.2. Tag mirror (Gitea → GitHub)

The runbook §2 instructs you to push the tag to **Gitea origin first**, then to **GitHub**. There are two ways to keep these in sync:

- **Manual**: per §2, `git push origin v$VERSION` then `git push github v$VERSION`. Always works; no admin config required.
- **Automated**: configure Gitea push-mirror at `https://git.integrolabs.net/roctinam/carbonyl-agent/settings#push-mirror-settings`. Add `https://github.com/jmagly/carbonyl-agent.git` with a token that has `contents:write` on the GitHub repo. Once enabled, `git push origin` propagates tags to GitHub automatically (~60 s). Recommended once the GitHub PAT is provisioned.

The Gitea release fires on push to origin (§3.0); the PyPI publish fires once the tag reaches the GitHub mirror (§3.1). Each is independent — the Gitea release lands even if the PyPI step is blocked.

**Monitor** at:
- Gitea: `https://git.integrolabs.net/roctinam/carbonyl-agent/actions`
- GitHub: `https://github.com/jmagly/carbonyl-agent/actions`

Do not delete the tag while either workflow is in flight.

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

- **Gitea Release**: created automatically by `.gitea/workflows/release.yml` (§3.0) — release notes pulled from the matching `## [VERSION]` section of `CHANGELOG.md`, with sdist + wheel + `*.sha256` sidecars (and optionally a docs tarball) attached. Verify it appears at `https://git.integrolabs.net/roctinam/carbonyl-agent/releases`. Re-run the workflow with `workflow_dispatch` if assets need to be regenerated; uploads are idempotent.
- **GitHub Release**: created automatically by `.github/workflows/release.yml`'s `github-release` job (§3.1, #18). Verify at `https://github.com/jmagly/carbonyl-agent/releases`.
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
