"""Tests for SHA-256 checksum verification in carbonyl_agent.install."""
from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from carbonyl_agent.install import (
    _asset_name_for_tag,
    _download_candidates,
    _fetch_checksum_url,
    _fetch_sha256sums,
    _platform_triple,
    _sha256_file,
    _validate_binary_version,
    _verify_checksum,
    cmd_install,
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


def _sidecar_body(filename: str, digest: str = FAKE_SHA256) -> bytes:
    return f"{digest}  {filename}\n".encode()


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
    with patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"), \
            patch("carbonyl_agent.install.urllib.request.urlopen", return_value=resp):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result == FAKE_SHA256


def test_fetch_sha256sums_404():
    exc = urllib.error.HTTPError(
        url="", code=404, msg="Not Found", hdrs=None, fp=None,  # type: ignore[arg-type]
    )
    with patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"), \
            patch("carbonyl_agent.install.urllib.request.urlopen", side_effect=exc):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result is None


def test_fetch_sha256sums_no_match():
    body = b"abcdef1234567890  other-file.tgz\n"
    resp = _FakeResponse(body)
    with patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"), \
            patch("carbonyl_agent.install.urllib.request.urlopen", return_value=resp):
        result = _fetch_sha256sums("runtime-abc", TRIPLE)

    assert result is None


def test_fetch_checksum_url_parse_sidecar_filename():
    filename = "carbonyl-0.2.0-alpha.15-x86_64-unknown-linux-gnu.tgz"
    resp = _FakeResponse(_sidecar_body(filename))
    with patch("carbonyl_agent.install.urllib.request.urlopen", return_value=resp):
        result = _fetch_checksum_url("https://example.invalid/asset.tgz.sha256", filename)

    assert result == FAKE_SHA256


def test_download_candidates_semantic_tag_prefers_github():
    candidates = _download_candidates("v0.2.0-alpha.15", TRIPLE)

    assert len(candidates) == 1
    assert candidates[0].label == "GitHub public release"
    assert "github.com/jmagly/carbonyl/releases/download/v0.2.0-alpha.15" in candidates[0].url
    assert candidates[0].asset_name == "carbonyl-0.2.0-alpha.15-x86_64-unknown-linux-gnu.tgz"
    assert candidates[0].checksum_url and candidates[0].checksum_url.endswith(".tgz.sha256")
    assert candidates[0].require_checksum is True
    assert candidates[0].expected_version == "0.2.0-alpha.15"


def test_download_candidates_runtime_hash_requires_internal_release_base():
    candidates = _download_candidates("runtime-deadbeef", TRIPLE)

    assert candidates == []


def test_download_candidates_runtime_hash_keeps_internal_asset_shape():
    with patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"):
        candidates = _download_candidates("runtime-deadbeef", TRIPLE)

    assert len(candidates) == 1
    assert candidates[0].label == "Internal runtime release"
    assert candidates[0].asset_name == f"{TRIPLE}.tgz"
    assert candidates[0].checksum_url and candidates[0].checksum_url.endswith("/SHA256SUMS")
    assert candidates[0].require_checksum is False
    assert candidates[0].expected_version is None


# ---------------------------------------------------------------------------
# Multi-arch resolution coverage (#98)
#
# Full multi-arch *execution* test jobs (macos-arm64, linux-aarch64) are gated
# on runner availability. These tests exercise the arch-independent resolution
# layer — asset-name + download-URL construction per consumer triple — so the
# install path is proven for every target arch on the x86_64 CI runner.
# ---------------------------------------------------------------------------

# The consumer triples #98 targets. macos-arm64 runtime ships upstream since
# carbonyl v0.2.0-alpha.7; linux-aarch64 runtime is pending upstream
# (roctinam/carbonyl#67/#116) but the agent-side resolution must be correct now.
CONSUMER_TRIPLES = [
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "aarch64-apple-darwin",
]


@pytest.mark.parametrize("triple", CONSUMER_TRIPLES)
def test_semantic_tag_resolution_per_triple(triple: str):
    """A semantic tag resolves to the right GitHub-first asset name + URL for
    every consumer arch, not just x86_64."""
    tag = "v0.2.0-alpha.17"
    assert _asset_name_for_tag(tag, triple) == f"carbonyl-0.2.0-alpha.17-{triple}.tgz"

    candidates = _download_candidates(tag, triple)
    assert [c.label for c in candidates] == ["GitHub public release"]
    for c in candidates:
        assert c.asset_name == f"carbonyl-0.2.0-alpha.17-{triple}.tgz"
        assert f"/download/{tag}/carbonyl-0.2.0-alpha.17-{triple}.tgz" in c.url
        assert c.checksum_url and c.checksum_url.endswith(f"{triple}.tgz.sha256")
        assert c.require_checksum is True
        assert c.expected_version == "0.2.0-alpha.17"


@pytest.mark.parametrize("triple", CONSUMER_TRIPLES)
def test_runtime_hash_resolution_per_triple(triple: str):
    """A runtime-hash tag resolves only when an internal mirror is configured."""
    assert _download_candidates("runtime-099874f855c74a61", triple) == []

    with patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"):
        candidates = _download_candidates("runtime-099874f855c74a61", triple)

    assert len(candidates) == 1
    assert candidates[0].asset_name == f"{triple}.tgz"
    assert candidates[0].expected_version is None


def test_platform_triple_maps_macos_to_apple_darwin():
    """darwin/arm64 host resolves to the macos-arm64 consumer triple (#98)."""

    def _fake_run(cmd, *args, **kwargs):
        arg = cmd[1]  # uname -m / uname -s
        out = "arm64" if arg == "-m" else "Darwin"
        return SimpleNamespace(stdout=out + "\n")

    with patch("carbonyl_agent.install.subprocess.run", side_effect=_fake_run):
        assert _platform_triple() == "arm64-apple-darwin"


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


def test_validate_binary_version_rejects_mismatch(tmp_path: Path):
    binary = tmp_path / "carbonyl"
    binary.write_text("#!/usr/bin/env bash\necho 'Carbonyl 0.2.0-alpha.11'\n")
    binary.chmod(0o755)

    with pytest.raises(SystemExit):
        _validate_binary_version(binary, "0.2.0-alpha.15")


def test_cmd_install_dry_run_prints_github_first_for_semantic_tag(tmp_path: Path, capsys):
    args = SimpleNamespace(
        dest=str(tmp_path),
        from_file=None,
        dry_run=True,
        tag="v0.2.0-alpha.15",
        force=False,
        checksum=None,
        no_verify=False,
    )

    with patch("carbonyl_agent.install._platform_triple", return_value=TRIPLE):
        assert cmd_install(args) == 0

    out = capsys.readouterr().out
    assert "GitHub public release" in out
    assert "Internal release mirror" not in out
    assert "carbonyl-0.2.0-alpha.15-x86_64-unknown-linux-gnu.tgz.sha256" in out
    assert "carbonyl --version == Carbonyl 0.2.0-alpha.15" in out


def test_cmd_install_falls_back_to_internal_mirror_when_configured(tmp_path: Path):
    tarball = tmp_path / "runtime.tgz"
    payload = tmp_path / "payload" / TRIPLE
    payload.mkdir(parents=True)
    binary = payload / "carbonyl"
    binary.write_text("#!/usr/bin/env bash\necho 'Carbonyl 0.2.0-alpha.15'\n")
    binary.chmod(0o755)
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(payload, arcname=TRIPLE)
    digest = _sha256_file(tarball)
    body = tarball.read_bytes()

    class DownloadResponse(_FakeResponse):
        def __init__(self, data: bytes):
            super().__init__(data, headers={"Content-Length": str(len(data))})

    calls: list[str] = []

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        calls.append(url)
        if "github.com" in url:
            raise urllib.error.URLError("offline")
        if url.endswith(".sha256"):
            filename = "carbonyl-0.2.0-alpha.15-x86_64-unknown-linux-gnu.tgz"
            return _FakeResponse(_sidecar_body(filename, digest))
        return DownloadResponse(body)

    args = SimpleNamespace(
        dest=str(tmp_path / "dest"),
        from_file=None,
        dry_run=False,
        tag="v0.2.0-alpha.15",
        force=True,
        checksum=None,
        no_verify=False,
    )

    with patch("carbonyl_agent.install._platform_triple", return_value=TRIPLE), \
            patch("carbonyl_agent.install.INTERNAL_RELEASE_BASE", "https://internal.example"), \
            patch("carbonyl_agent.install.urllib.request.urlopen", side_effect=fake_urlopen):
        assert cmd_install(args) == 0

    assert any("github.com" in url for url in calls)
    assert any("internal.example" in url and url.endswith(".tgz") for url in calls)
    installed = tmp_path / "dest" / TRIPLE / "carbonyl"
    result = subprocess.run([str(installed), "--version"], capture_output=True, text=True)
    assert result.stdout.strip() == "Carbonyl 0.2.0-alpha.15"


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
