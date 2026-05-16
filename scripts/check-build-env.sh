#!/usr/bin/env bash
# Preflight check for carbonyl-wreq Rust build dependencies.
#
# Refs: roctinam/carbonyl-agent#108
#
# carbonyl-wreq depends on wreq -> boring-sys2 (BoringSSL fork). The
# boring-sys2 build script invokes bindgen against the BoringSSL C
# headers, which requires libclang AND a C sysroot that exposes stddef.h.
# Without those, the build fails with:
#
#   boringssl/src/include/openssl/base.h:59:10:
#     fatal error: 'stddef.h' file not found
#
# This script verifies the required tools are present BEFORE cargo runs,
# so the missing-dependency message is actionable rather than buried in a
# bindgen panic.

set -eu

missing=()
warnings=()

require() {
  local cmd=$1
  local hint=$2
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$cmd  ($hint)")
  fi
}

require cc       "Debian/Ubuntu: build-essential | RHEL/Fedora: gcc"
require cmake    "Debian/Ubuntu: cmake          | RHEL/Fedora: cmake"
require clang    "Debian/Ubuntu: clang          | RHEL/Fedora: clang"
require pkg-config "Debian/Ubuntu: pkg-config   | RHEL/Fedora: pkgconf-pkg-config"

# libclang-dev provides the libclang.so that rust-bindgen dlopens. Detect
# by looking for its pkg-config entry OR a libclang shared object in a
# standard library directory.
if ! pkg-config --exists libclang 2>/dev/null \
  && ! ls /usr/lib/x86_64-linux-gnu/libclang*.so* >/dev/null 2>&1 \
  && ! ls /usr/lib64/libclang*.so* >/dev/null 2>&1; then
  missing+=("libclang.so  (Debian/Ubuntu: libclang-dev | RHEL/Fedora: clang-devel)")
fi

# bindgen needs a C sysroot exposing <stddef.h>. clang ships its own
# resource-dir headers; gcc's are at $(gcc -print-file-name=include). On
# minimal containers neither may be installed.
if command -v clang >/dev/null 2>&1; then
  if ! echo '#include <stddef.h>' | clang -E -x c - >/dev/null 2>&1; then
    warnings+=("clang cannot find <stddef.h> — try installing libclang-rt-dev or set BINDGEN_EXTRA_CLANG_ARGS=\"-I\$(gcc -print-file-name=include)\"")
  fi
fi

if [ ${#missing[@]} -gt 0 ]; then
  echo "✗ Missing build dependencies for carbonyl-wreq:"
  for entry in "${missing[@]}"; do
    echo "    - $entry"
  done
  echo
  echo "  Debian/Ubuntu one-shot install:"
  echo "    sudo apt-get install -y clang cmake libclang-dev libssl-dev pkg-config python3-dev"
  echo
  echo "  See CONTRIBUTING.md → 'Rust workspace prerequisites' for details."
  exit 1
fi

if [ ${#warnings[@]} -gt 0 ]; then
  echo "⚠ Build environment warnings:"
  for entry in "${warnings[@]}"; do
    echo "    - $entry"
  done
  echo
fi

echo "✓ Build environment OK (clang $(clang --version | head -1 | awk '{print $3}'), cmake $(cmake --version | head -1 | awk '{print $3}'))"
