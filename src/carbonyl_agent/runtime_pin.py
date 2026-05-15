"""Read the repo-pinned Carbonyl runtime version.

The pin lives at ``.carbonyl-runtime-version`` at the repository root and is
shared across the SDK installer, the qa-runner Docker build, and CI workflows
so that every consumer references the same runtime hash. See issue #39 for
the rationale.

Pin file format::

    # comments allowed
    runtime-hash=<hex>
    # optional — when present, takes precedence over runtime-hash
    runtime-tag=v2026.5.0

The pin is intentionally a plain ``key=value`` file rather than YAML/JSON so
shell scripts (`docker/qa-runner/build.sh`, CI YAML) can parse it with `grep`
without pulling in additional tooling.

Resolution precedence (#98):
  1. ``CARBONYL_RUNTIME_TAG`` env var
  2. Pin file ``runtime-tag=<tag>`` (semantic anchor, preferred for releases)
  3. Pin file ``runtime-hash=<hex>`` (immutable anchor, exact bytes)
  4. Pin file ``runtime-hash=runtime-latest`` (explicit drift opt-in)
  5. No pin file (legacy unpinned default → ``runtime-latest``)
"""
from __future__ import annotations

import os
from pathlib import Path

# Filename used at the repository root.
PIN_FILENAME = ".carbonyl-runtime-version"

# Sentinel that means "use the upstream `runtime-latest` resolver" — i.e.
# the unpinned, drift-prone path. Allowed in the pin file as an explicit
# opt-out, and as the value of ``CARBONYL_RUNTIME_TAG`` env override.
LATEST_SENTINEL = "runtime-latest"


def _candidate_pin_paths() -> list[Path]:
    """Locations to try, in priority order, when looking for the pin file.

    1. ``CARBONYL_RUNTIME_PIN_FILE`` env var (explicit override)
    2. Walk up from the package source directory until we find one
    3. Current working directory
    """
    paths: list[Path] = []

    explicit = os.environ.get("CARBONYL_RUNTIME_PIN_FILE")
    if explicit:
        paths.append(Path(explicit))

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        paths.append(parent / PIN_FILENAME)

    paths.append(Path.cwd() / PIN_FILENAME)
    return paths


def _parse_pin_file(content: str) -> dict[str, str]:
    """Parse the ``key=value`` pin file body. Comments (``#``) and blank lines
    ignored. Whitespace around keys/values trimmed.
    """
    out: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def find_pin_file() -> Path | None:
    """Return the first existing pin-file path, or *None* if none found."""
    for candidate in _candidate_pin_paths():
        if candidate.is_file():
            return candidate
    return None


def read_pinned_hash() -> str | None:
    """Return the pinned ``runtime-hash`` value, or *None* if no pin file
    is found or the file does not contain a usable hash.

    A value of ``runtime-latest`` is treated as a deliberate opt-out and
    returned as-is so the caller can decide whether to honour it.
    """
    path = find_pin_file()
    if path is None:
        return None
    pins = _parse_pin_file(path.read_text())
    value = pins.get("runtime-hash")
    if not value:
        return None
    return value


def read_pinned_tag() -> str | None:
    """Return the pinned ``runtime-tag`` value, or *None* if no pin file
    is found or the file does not contain a tag entry.

    A tag is a semantic anchor (e.g. ``v2026.5.0``) that the carbonyl
    runtime publishes alongside the hash-anchored ``runtime-<hash>``
    releases. When present in the pin file, it takes precedence over
    ``runtime-hash`` — operators can pin to a semantic release line
    while still seeing exact-hash diagnostics in the install output.
    See #98.
    """
    path = find_pin_file()
    if path is None:
        return None
    pins = _parse_pin_file(path.read_text())
    value = pins.get("runtime-tag")
    if not value:
        return None
    return value


def resolve_default_tag() -> tuple[str, str]:
    """Return ``(tag, source)`` for the default ``carbonyl-agent install``
    tag when the user has not passed ``--tag``.

    ``source`` is one of ``"env"``, ``"tag-pin"``, ``"pin"``,
    ``"latest-sentinel"``, ``"unpinned"`` — used by the installer to
    print which path was taken so operators can spot drift.

    Resolution order:

    1. ``CARBONYL_RUNTIME_TAG`` env var (explicit override; not from the pin)
    2. Pin file ``runtime-tag=<tag>``  →  ``<tag>`` (semantic anchor, #98)
    3. Pin file ``runtime-hash=<hex>``  →  ``runtime-<hex>``
    4. Pin file ``runtime-hash=runtime-latest``  →  ``runtime-latest``
    5. No pin file  →  ``runtime-latest`` (legacy unpinned default)
    """
    env_tag = os.environ.get("CARBONYL_RUNTIME_TAG")
    if env_tag:
        return env_tag, "env"

    tag_pinned = read_pinned_tag()
    if tag_pinned:
        return tag_pinned, "tag-pin"

    pinned = read_pinned_hash()
    if pinned is None:
        return LATEST_SENTINEL, "unpinned"
    if pinned == LATEST_SENTINEL:
        return LATEST_SENTINEL, "latest-sentinel"
    # Bare hash → wrap with the runtime- prefix
    if pinned.startswith("runtime-"):
        return pinned, "pin"
    return f"runtime-{pinned}", "pin"
