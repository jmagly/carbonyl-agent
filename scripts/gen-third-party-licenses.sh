#!/usr/bin/env bash
# Regenerate THIRD_PARTY_LICENSES.txt — aggregates Rust dep licenses for
# the carbonyl-fingerprint and carbonyl-wreq native extensions shipped
# inside the carbonyl-agent wheel.
#
# Usage:
#   scripts/gen-third-party-licenses.sh           # regenerate in place
#   scripts/gen-third-party-licenses.sh --check   # fail if file would change
#
# Prerequisites:
#   cargo install cargo-about --features cli
#
# Scope notes:
#   - Current scope: carbonyl-fingerprint only (clean license tree).
#   - carbonyl-wreq is excluded pending resolution of the wreq-util
#     GPL-3.0 finding documented in #94. The full workspace generation
#     will succeed once wreq-util is replaced or feature-gated.
#
# Refs: roctinam/carbonyl-agent#94

set -euo pipefail

cd "$(dirname "$0")/.."

OUTPUT="THIRD_PARTY_LICENSES.txt"
TEMPLATE="about.hbs"
MANIFEST="crates/carbonyl-fingerprint/Cargo.toml"

if ! command -v cargo-about >/dev/null 2>&1; then
  echo "ERROR: cargo-about not installed. Run:" >&2
  echo "  cargo install cargo-about --features cli" >&2
  exit 2
fi

CHECK=false
if [ "${1:-}" = "--check" ]; then
  CHECK=true
fi

if [ "$CHECK" = true ]; then
  TMP="$(mktemp -t third-party-XXXXXX.txt)"
  trap 'rm -f "$TMP"' EXIT
  cargo about generate --manifest-path "$MANIFEST" "$TEMPLATE" -o "$TMP"
  if ! diff -q "$OUTPUT" "$TMP" >/dev/null 2>&1; then
    echo "ERROR: $OUTPUT is stale. Regenerate with:" >&2
    echo "  scripts/gen-third-party-licenses.sh" >&2
    diff "$OUTPUT" "$TMP" | head -40 >&2
    exit 1
  fi
  echo "OK: $OUTPUT matches generated output."
else
  cargo about generate --manifest-path "$MANIFEST" "$TEMPLATE" -o "$OUTPUT"
  echo "Wrote $OUTPUT ($(wc -l < "$OUTPUT") lines)"
fi
