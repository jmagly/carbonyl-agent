#!/usr/bin/env bash
# Build the API reference for carbonyl-agent (#17).
#
# Usage:
#   pip install -e ".[docs]"
#   ./scripts/build-docs.sh           # writes to docs/api/
#   ./scripts/build-docs.sh --serve   # local preview at http://localhost:8080
#
# CI uses --output-directory to publish into the gh-pages or release-asset
# bundle path; that lives in .gitea/workflows/ci.yml's docs job.
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${OUT:-docs/api}"

if [ "${1:-}" = "--serve" ]; then
    exec python -m pdoc src/carbonyl_agent --port 8080
fi

mkdir -p "$OUT"
python -m pdoc src/carbonyl_agent \
    --output-directory "$OUT" \
    --logo "" \
    --footer-text "carbonyl-agent — Python automation SDK for Carbonyl"

echo "Docs written to $OUT/"
echo "Open: $OUT/carbonyl_agent.html"
