"""Tests for SHA-256 checksum verification in carbonyl_agent.install."""
from __future__ import annotations

import hashlib
import io
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from carbonyl_agent.install import (
    _fetch_sha256sums,
    _platform_triple,
    _sha256_file,
    _verify_checksum,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_TARBALL_CONTENT = b"fake tarball bytes for testing"
FAKE_SHA256 = hashlib.sha256(FAKE_TARBALL_CONTENT).hexdigest()
WRONG_SHA256 = "0" * 64
TRIPLE = "x86_64-unknown-linux-gnu"


def _write_tarball(path: Path) -> None:
    path.write_bytes(FAKE_TARBALL_CONTENT)


def _sha256sums_body(triple: str = TRIPLE, digest: str = FAKE_SHA256) -> bytes:
    return f"{digest}  {triple}.tgz\n".encode()


class _FakeResponse(io.BytesIO):
    """Minimal urllib response stand-in."""

    def __init__(self, data: bytes, headers: dict | None = None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ---------------------------------------------------------------------------
# _sha256_file
# ---------------------------------------------------------------------------

def test_sha256_file(tmp_path: Path):
    p = tmp_path / "blob"
    _write_tarball(p)
    assert _sha256_file(p) == FAKE_SHA256


# ---------------------------------------------------------------------------
# _verify_checksum — matching digest
# ---------------------------------------------------------------------------

def test_sha256_verify_match(tmp_path: Path, capsys):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    with patch("carbonyl_agent.install._fetch_sha256sums", return_value=FAKE_SHA256):
        _verify_checksum(tarball, "runtime-abc", TRIPLE)

    out = capsys.readouterr().out
    assert "Checksum OK" in out


# ---------------------------------------------------------------------------
# _verify_checksum — mismatching digest
# ---------------------------------------------------------------------------

def test_sha256_verify_mismatch(tmp_path: Path):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    with patch("carbonyl_agent.install._fetch_sha256sums", return_value=WRONG_SHA256):
        with pytest.raises(SystemExit):
            _verify_checksum(tarball, "runtime-abc", TRIPLE)


# ---------------------------------------------------------------------------
# _verify_checksum — SHA256SUMS file missing (404)
# ---------------------------------------------------------------------------

def test_sha256_sums_missing(tmp_path: Path, capsys):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    with patch("carbonyl_agent.install._fetch_sha256sums", return_value=None):
        # Should NOT raise — just warn
        _verify_checksum(tarball, "runtime-abc", TRIPLE)

    err = capsys.readouterr().err
    assert "SHA256SUMS not found" in err


# ---------------------------------------------------------------------------
# --checksum flag override
# ---------------------------------------------------------------------------

def test_checksum_flag_override(tmp_path: Path, capsys):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    # Provide pinned checksum — should NOT call _fetch_sha256sums at all
    with patch("carbonyl_agent.install._fetch_sha256sums") as mock_fetch:
        _verify_checksum(
            tarball, "runtime-abc", TRIPLE, pinned_checksum=FAKE_SHA256,
        )
        mock_fetch.assert_not_called()

    out = capsys.readouterr().out
    assert "Checksum OK" in out
    assert "--checksum flag" in out


def test_checksum_flag_override_mismatch(tmp_path: Path):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    with pytest.raises(SystemExit):
        _verify_checksum(
            tarball, "runtime-abc", TRIPLE, pinned_checksum=WRONG_SHA256,
        )


# ---------------------------------------------------------------------------
# --no-verify flag
# ---------------------------------------------------------------------------

def test_no_verify_skips(tmp_path: Path, capsys):
    tarball = tmp_path / "archive.tgz"
    _write_tarball(tarball)

    with patch("carbonyl_agent.install._fetch_sha256sums") as mock_fetch:
        _verify_checksum(
            tarball, "runtime-abc", TRIPLE, skip_verify=True,
        )
        mock_fetch.assert_not_called()

    err = capsys.readouterr().err
    assert "skipped" in err


# ---------------------------------------------------------------------------
# _fetch_sha256sums — parsing
# ---------------------------------------------------------------------------

def test_fetch_sha256sums_parse():
    body = _sha256sums_body()

    resp = _FakeResponse(body)
    with patch("carbonyl_agent.install.urllib.request.urlopen", return_value=resp):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result == FAKE_SHA256


def test_fetch_sha256sums_404():
    exc = urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch("carbonyl_agent.install.urllib.request.urlopen", side_effect=exc):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result is None


def test_fetch_sha256sums_no_match():
    body = b"abcdef1234567890  other-file.tgz\n"
    resp = _FakeResponse(body)
    with patch("carbonyl_agent.install.urllib.request.urlopen", return_value=resp):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result is None


# ---------------------------------------------------------------------------
# _platform_triple — format validation
# ---------------------------------------------------------------------------

def test_platform_triple_format():
    triple = _platform_triple()
    parts = triple.split("-")
    # Expected format: {arch}-{vendor}-{os} e.g. "x86_64-unknown-linux-gnu"
    # That contains exactly two hyphens (three parts) or more for os like "linux-gnu"
    assert triple.count("-") >= 2, f"Expected at least two hyphens in triple, got: {triple}"
    assert len(parts) >= 3, f"Expected at least 3 parts in triple, got: {parts}"
    # First part should be a machine architecture
    assert len(parts[0]) > 0, "Architecture part must not be empty"


# ---------------------------------------------------------------------------
# CLI wiring (US-026) — daemon subcommand is dispatched correctly
# ---------------------------------------------------------------------------

def test_cli_daemon_subcommand_registered():
    """carbonyl-agent daemon <cmd> is recognized by the top-level parser."""
    import sys

    from carbonyl_agent import install

    # Capture parser by calling main() with --help and intercepting SystemExit
    old_argv = sys.argv
    try:
        sys.argv = ["carbonyl-agent", "daemon", "--help"]
        with pytest.raises(SystemExit) as exc:
            install.main()
        # argparse --help exits 0
        assert exc.value.code == 0
    finally:
        sys.argv = old_argv


def test_cli_daemon_unknown_subcommand_errors():
    """carbonyl-agent daemon <unknown> exits non-zero."""
    import sys

    from carbonyl_agent import install

    old_argv = sys.argv
    try:
        sys.argv = ["carbonyl-agent", "daemon", "nonexistent"]
        with pytest.raises(SystemExit) as exc:
            install.main()
        assert exc.value.code != 0
    finally:
        sys.argv = old_argv
