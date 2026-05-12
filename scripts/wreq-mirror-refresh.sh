#!/usr/bin/env bash
#
# Refreshes the cold mirror of the wreq source tarball at the SHA
# pinned in `.carbonyl-fingerprint-version`. Per ADR-005 §"Bus-factor
# mitigation plan" item 2.
#
# Behaviour:
#   1. Read pin from .carbonyl-fingerprint-version
#   2. Skip silently if pin is TBD-#44 (pre-#44 state)
#   3. Download tarball from upstream at the pinned SHA
#   4. Compute SHA-256 checksum
#   5. Upload as a Gitea release asset on roctinam/carbonyl-agent
#      tagged `wreq-mirror-<short-sha>`
#
# Requires:
#   - A Gitea token at one of the standard locations:
#     ~/.config/gitea/token, $GITEA_TOKEN env var, or
#     `gh auth token`-style fallback documented in the project's
#     token-security rule. The token needs release-write scope on
#     roctinam/carbonyl-agent.
#   - curl, jq, sha256sum, tar
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
GITEA_HOST="${GITEA_HOST:-git.integrolabs.net}"
MIRROR_REPO="${MIRROR_REPO:-roctinam/carbonyl-agent}"

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
echo "Target: ${GITEA_HOST}/${MIRROR_REPO} release ${TAG}"
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
    echo "Would upload: ${TARBALL_NAME} → ${TAG} on ${GITEA_HOST}/${MIRROR_REPO}"
    exit 0
fi

# Resolve Gitea token. Follow the project's token-security rule:
# heredoc-scoped, never echoed.
TOKEN_FILE="${HOME}/.config/gitea/token"
if [[ -n "${GITEA_TOKEN:-}" ]]; then
    : # use env var
elif [[ -f "${TOKEN_FILE}" ]]; then
    : # will read inside the upload heredoc
else
    echo "ERROR: no Gitea token available." >&2
    echo "       Set GITEA_TOKEN env var, or place token at ${TOKEN_FILE} (mode 600)." >&2
    exit 1
fi

# Upload — wrapped in heredoc so the token has the smallest possible
# scratch surface.
bash <<EOF
set -euo pipefail
TOKEN="\${GITEA_TOKEN:-}"
if [[ -z "\${TOKEN}" ]]; then
    TOKEN="\$(cat "${TOKEN_FILE}")"
fi

# 1. Ensure the release exists. Create if not present.
RELEASE_BODY="\$(jq -nc --arg tag "${TAG}" --arg name "wreq mirror @${SHORT_SHA}" --arg body "Cold mirror of wreq @${PINNED_SHA} (semver ${PINNED_VERSION}). Per ADR-005 § bus-factor mitigation #2." '{ tag_name: \$tag, name: \$name, body: \$body, prerelease: false, draft: false }')"

RELEASE_ID="\$(curl -fsSL -H "Authorization: token \${TOKEN}" \
    "https://${GITEA_HOST}/api/v1/repos/${MIRROR_REPO}/releases/tags/${TAG}" \
    2>/dev/null \
    | jq -r '.id // empty')"

if [[ -z "\${RELEASE_ID}" ]]; then
    echo "Creating release ${TAG}..."
    RELEASE_ID="\$(curl -fsSL -X POST \
        -H "Authorization: token \${TOKEN}" \
        -H "Content-Type: application/json" \
        --data "\${RELEASE_BODY}" \
        "https://${GITEA_HOST}/api/v1/repos/${MIRROR_REPO}/releases" \
        | jq -r '.id')"
    echo "  release id=\${RELEASE_ID}"
else
    echo "Release ${TAG} already exists (id=\${RELEASE_ID})"
fi

# 2. Upload the tarball as an asset.
echo "Uploading ${TARBALL_NAME}..."
curl -fsSL -X POST \
    -H "Authorization: token \${TOKEN}" \
    -F "attachment=@${WORK_DIR}/${TARBALL_NAME}" \
    "https://${GITEA_HOST}/api/v1/repos/${MIRROR_REPO}/releases/\${RELEASE_ID}/assets?name=${TARBALL_NAME}" \
    | jq -r '"  asset id=" + (.id | tostring) + " size=" + (.size | tostring)'

# 3. Upload the checksum as a sidecar.
echo "${CHECKSUM}  ${TARBALL_NAME}" > "${WORK_DIR}/${TARBALL_NAME}.sha256"
curl -fsSL -X POST \
    -H "Authorization: token \${TOKEN}" \
    -F "attachment=@${WORK_DIR}/${TARBALL_NAME}.sha256" \
    "https://${GITEA_HOST}/api/v1/repos/${MIRROR_REPO}/releases/\${RELEASE_ID}/assets?name=${TARBALL_NAME}.sha256" \
    | jq -r '"  checksum asset id=" + (.id | tostring)'
EOF

echo
echo "Mirror refreshed: ${TAG} on https://${GITEA_HOST}/${MIRROR_REPO}/releases/tag/${TAG}"
