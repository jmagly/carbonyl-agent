#!/bin/bash
# build.sh — build carbonyl-agent-qa-runner against the pinned runtime.
#
# Reads .carbonyl-runtime-version at the repo root for the runtime hash,
# constructs the corresponding runtime-x11-<hash> tarball URL, and runs
# `docker build` with that as the CARBONYL_RUNTIME_URL build arg.
#
# Override the resolved hash by exporting CARBONYL_RUNTIME_HASH=<hex>.
# Override the entire URL by exporting CARBONYL_RUNTIME_URL=<url>.
#
# Usage (from repo root):
#   docker/qa-runner/build.sh                     # local image: carbonyl-agent-qa-runner:local
#   docker/qa-runner/build.sh -t my-tag:dev       # custom tag

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PIN_FILE="$REPO_ROOT/.carbonyl-runtime-version"
GITEA_BASE="${GITEA_BASE:-https://git.integrolabs.net}"
GITEA_REPO="${GITEA_REPO:-roctinam/carbonyl}"

# --- Resolve the runtime hash ----------------------------------------------

if [[ -n "${CARBONYL_RUNTIME_URL:-}" ]]; then
    URL="$CARBONYL_RUNTIME_URL"
    echo "Using CARBONYL_RUNTIME_URL: $URL" >&2
elif [[ -n "${CARBONYL_RUNTIME_HASH:-}" ]]; then
    HASH="$CARBONYL_RUNTIME_HASH"
    URL="$GITEA_BASE/$GITEA_REPO/releases/download/runtime-x11-$HASH/x86_64-unknown-linux-gnu.tgz"
    echo "Using CARBONYL_RUNTIME_HASH=$HASH" >&2
else
    if [[ ! -f "$PIN_FILE" ]]; then
        echo "ERROR: pin file not found at $PIN_FILE" >&2
        echo "Either create one with 'runtime-hash=<hex>' or pass" >&2
        echo "CARBONYL_RUNTIME_HASH=<hex> / CARBONYL_RUNTIME_URL=<url>." >&2
        exit 2
    fi
    HASH="$(grep -E '^runtime-hash=' "$PIN_FILE" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
    if [[ -z "$HASH" ]] || [[ "$HASH" == "runtime-latest" ]]; then
        echo "ERROR: pin file '$PIN_FILE' does not contain a concrete runtime hash" >&2
        echo "       (got '$HASH'). Override with CARBONYL_RUNTIME_HASH or" >&2
        echo "       CARBONYL_RUNTIME_URL." >&2
        exit 2
    fi
    URL="$GITEA_BASE/$GITEA_REPO/releases/download/runtime-x11-$HASH/x86_64-unknown-linux-gnu.tgz"
    echo "Using pinned runtime hash from $PIN_FILE: $HASH" >&2
fi

# --- Build -----------------------------------------------------------------

TAG="${1:-carbonyl-agent-qa-runner:local}"
shift || true

echo "Building $TAG with CARBONYL_RUNTIME_URL=$URL" >&2

docker build \
    --build-arg "CARBONYL_RUNTIME_URL=$URL" \
    -t "$TAG" \
    "$@" \
    "$REPO_ROOT/docker/qa-runner/"
