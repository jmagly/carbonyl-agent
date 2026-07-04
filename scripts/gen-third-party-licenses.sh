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
#   - Full workspace: carbonyl-fingerprint + carbonyl-wreq (and their
#     transitive deps). The project is AGPL-3.0-only, so GPL-3.0 wreq-util
#     is license-compatible (GPLv3 §13); about.toml accepts it and requires
#     attribution rather than treating it as a conflict (#99).
#   - When carbonyl-wreq ships in the wheel (#88), this file already covers
#     its notices. #100 tracks replacing wreq-util with an in-house registry.
#
# Refs: roctinam/carbonyl-agent#94, #99, #88, #100

set -euo pipefail

cd "$(dirname "$0")/.."

OUTPUT="THIRD_PARTY_LICENSES.txt"
TEMPLATE="about.hbs"
MANIFEST="Cargo.toml"  # workspace root — covers all members (#99)

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
  # Ignore blank-line and whitespace-only differences (-B -b): cargo-about's
  # rendering of some crates' license files is not byte-identical across
  # environments (freshly-fetched crate sources vs a warm registry cache can
  # differ by a trailing blank line). The gate's job is to catch a new/changed
  # crate license — never a whitespace-only change — so this stays strict on
  # content while tolerating environmental noise.
  if ! diff -q -B -b "$OUTPUT" "$TMP" >/dev/null 2>&1; then
    echo "ERROR: $OUTPUT is stale (a dependency license changed or a crate is" >&2
    echo "not covered). Regenerate with:" >&2
    echo "  scripts/gen-third-party-licenses.sh" >&2
    diff -B -b "$OUTPUT" "$TMP" | head -40 >&2
    exit 1
  fi
  echo "OK: $OUTPUT covers the resolved dependency tree."
else
  cargo about generate --manifest-path "$MANIFEST" "$TEMPLATE" -o "$OUTPUT"
  echo "Wrote $OUTPUT ($(wc -l < "$OUTPUT") lines)"
fi
