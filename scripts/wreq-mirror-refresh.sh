#!/usr/bin/env bash
#
# Refreshes the cold release asset of the wreq source tarball at the SHA
# pinned in `.carbonyl-fingerprint-version`. Per ADR-005 §"Bus-factor
# mitigation plan" item 2.
#
# Behaviour:
#   1. Read pin from .carbonyl-fingerprint-version
#   2. Skip silently if pin is TBD-#44 (pre-#44 state)
#   3. Download tarball from upstream at the pinned SHA
#   4. Compute SHA-256 checksum
#   5. Upload as a GitHub release asset on jmagly/carbonyl-agent
#      tagged `wreq-mirror-<short-sha>`
#
# Requires:
#   - GitHub CLI authenticated with release-write access to jmagly/carbonyl-agent
#   - curl, gh, sha256sum
#
# Usage:
#   scripts/wreq-mirror-refresh.sh                # refresh from pin
#   scripts/wreq-mirror-refresh.sh --dry-run      # download + checksum only, no upload
#   scripts/wreq-mirror-refresh.sh --verify-only  # confirm existing mirror matches pin
#
# Refs:
#   ADR-005 § Bus-factor mitigation plan, item 2
#   roctinam/carbonyl-agent#60

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN_FILE="${REPO_ROOT}/.carbonyl-fingerprint-version"
MIRROR_REPO="${MIRROR_REPO:-jmagly/carbonyl-agent}"

DRY_RUN=0
VERIFY_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --verify-only) VERIFY_ONLY=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            echo "See: $0 --help" >&2
            exit 64
            ;;
    esac
done

if [[ ! -f "${PIN_FILE}" ]]; then
    echo "ERROR: ${PIN_FILE} missing" >&2
    exit 1
fi

PINNED_SHA="$(grep -E '^wreq-sha=' "${PIN_FILE}" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
PINNED_VERSION="$(grep -E '^wreq-version=' "${PIN_FILE}" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
PINNED_SOURCE="$(grep -E '^wreq-source=' "${PIN_FILE}" | head -1 | cut -d= -f2 | tr -d '[:space:]')"

if [[ "${PINNED_SHA}" == "TBD-#44" ]] || [[ -z "${PINNED_SHA}" ]]; then
    echo "Pin is still TBD-#44; nothing to mirror. Skipping (pre-#44 state)."
    exit 0
fi

# Phase 2.2 (#81) resolves wreq from the crates.io registry rather
# than a git pin. The mirror exists to anchor on a specific git SHA;
# registry-resolved deps use crates.io itself + Cargo.lock as the
# integrity record, so there's nothing to mirror here. A future bump
# to a git pin (e.g. to anchor on a security-relevant SHA outside a
# release) restarts the mirror path.
if [[ "${PINNED_SHA}" == "registry" ]]; then
    echo "wreq-sha=registry; wreq resolves via crates.io. Skipping cold mirror (Phase 2.2 #81)."
    exit 0
fi

# Validate the SHA looks like a git commit hash.
if [[ ! "${PINNED_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: wreq-sha in ${PIN_FILE} is not a 40-char hex SHA: ${PINNED_SHA}" >&2
    exit 1
fi

SHORT_SHA="${PINNED_SHA:0:12}"
TAG="wreq-mirror-${SHORT_SHA}"
TARBALL_NAME="wreq-${PINNED_VERSION}-${SHORT_SHA}.tar.gz"

# wreq-source format: https://github.com/<owner>/<repo>
# Build a tarball URL for the SHA. GitHub: archive/<sha>.tar.gz
case "${PINNED_SOURCE}" in
    https://github.com/*)
        TARBALL_URL="${PINNED_SOURCE%/}/archive/${PINNED_SHA}.tar.gz"
        ;;
    *)
        echo "ERROR: don't know how to build a tarball URL for ${PINNED_SOURCE}" >&2
        echo "       Update scripts/wreq-mirror-refresh.sh to handle this host." >&2
        exit 1
        ;;
esac

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "Pinned: ${PINNED_VERSION} @ ${PINNED_SHA}"
echo "Source: ${TARBALL_URL}"
echo "Target: github.com/${MIRROR_REPO} release ${TAG}"
echo

echo "Downloading tarball..."
curl -fsSL --output "${WORK_DIR}/${TARBALL_NAME}" "${TARBALL_URL}"

CHECKSUM="$(sha256sum "${WORK_DIR}/${TARBALL_NAME}" | awk '{print $1}')"
SIZE_BYTES="$(stat -c%s "${WORK_DIR}/${TARBALL_NAME}")"
echo "  size:     ${SIZE_BYTES} bytes"
echo "  sha256:   ${CHECKSUM}"

# Verify-only mode: compare against existing mirror release.
if [[ "${VERIFY_ONLY}" -eq 1 ]]; then
    echo
    echo "Verify-only: would compare against ${TAG} release on ${MIRROR_REPO}"
    echo "(Verification path lands when the first real upload happens — pre-#44 the mirror is empty.)"
    exit 0
fi

# Dry-run mode: skip the upload step.
if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo
    echo "Dry-run: tarball downloaded and checksummed; upload skipped."
    echo "Would upload: ${TARBALL_NAME} → ${TAG} on github.com/${MIRROR_REPO}"
    exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI is required for GitHub release uploads." >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: gh is not authenticated for GitHub release uploads." >&2
    exit 1
fi

if ! gh release view "${TAG}" --repo "${MIRROR_REPO}" >/dev/null 2>&1; then
    echo "Creating release ${TAG}..."
    gh release create "${TAG}" \
        --repo "${MIRROR_REPO}" \
        --title "wreq mirror @${SHORT_SHA}" \
        --notes "Cold mirror of wreq @${PINNED_SHA} (semver ${PINNED_VERSION}). Per ADR-005 bus-factor mitigation item 2."
else
    echo "Release ${TAG} already exists"
fi

echo "Uploading ${TARBALL_NAME}..."
echo "${CHECKSUM}  ${TARBALL_NAME}" > "${WORK_DIR}/${TARBALL_NAME}.sha256"
gh release upload "${TAG}" \
    "${WORK_DIR}/${TARBALL_NAME}" \
    "${WORK_DIR}/${TARBALL_NAME}.sha256" \
    --repo "${MIRROR_REPO}" \
    --clobber

echo
echo "Mirror refreshed: ${TAG} on https://github.com/${MIRROR_REPO}/releases/tag/${TAG}"
