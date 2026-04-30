"""Tests for carbonyl_agent.runtime_pin (issue #39)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from carbonyl_agent import runtime_pin


class TestParser:
    def test_basic_keyvalue(self):
        out = runtime_pin._parse_pin_file("runtime-hash=abc123")
        assert out == {"runtime-hash": "abc123"}

    def test_comments_and_blanks_ignored(self):
        body = "\n".join([
            "# top comment",
            "",
            "runtime-hash=abc123",
            "  # indented comment",
            "  ",
        ])
        assert runtime_pin._parse_pin_file(body) == {"runtime-hash": "abc123"}

    def test_whitespace_around_keyvalue_trimmed(self):
        out = runtime_pin._parse_pin_file("  runtime-hash  =  abc123  ")
        assert out == {"runtime-hash": "abc123"}

    def test_lines_without_equals_skipped(self):
        out = runtime_pin._parse_pin_file("not a pin\nruntime-hash=abc")
        assert out == {"runtime-hash": "abc"}

    def test_value_can_contain_equals(self):
        out = runtime_pin._parse_pin_file("notes=k=v stuff")
        assert out == {"notes": "k=v stuff"}


class TestReadPinnedHash:
    def test_returns_hash_when_pin_file_present(self, tmp_path: Path):
        pin = tmp_path / runtime_pin.PIN_FILENAME
        pin.write_text("runtime-hash=deadbeef\n")
        with patch.dict(os.environ, {"CARBONYL_RUNTIME_PIN_FILE": str(pin)}, clear=False):
            assert runtime_pin.read_pinned_hash() == "deadbeef"

    def test_returns_none_when_no_pin_file(self, tmp_path: Path):
        # Point at a nonexistent path AND change cwd so the cwd-fallback
        # doesn't accidentally pick up the real repo's pin.
        with patch.dict(os.environ, {"CARBONYL_RUNTIME_PIN_FILE": str(tmp_path / "missing")}, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=[tmp_path / "missing"]):
                assert runtime_pin.read_pinned_hash() is None

    def test_returns_none_when_pin_file_empty_or_no_hash(self, tmp_path: Path):
        pin = tmp_path / runtime_pin.PIN_FILENAME
        pin.write_text("# only comments\n")
        with patch.dict(os.environ, {"CARBONYL_RUNTIME_PIN_FILE": str(pin)}, clear=False):
            assert runtime_pin.read_pinned_hash() is None

    def test_latest_sentinel_passed_through(self, tmp_path: Path):
        pin = tmp_path / runtime_pin.PIN_FILENAME
        pin.write_text("runtime-hash=runtime-latest\n")
        with patch.dict(os.environ, {"CARBONYL_RUNTIME_PIN_FILE": str(pin)}, clear=False):
            assert runtime_pin.read_pinned_hash() == "runtime-latest"


class TestResolveDefaultTag:
    def _isolated(self, tmp_path: Path, content: str | None) -> tuple[dict, list]:
        env = {}
        if content is not None:
            pin = tmp_path / runtime_pin.PIN_FILENAME
            pin.write_text(content)
            env["CARBONYL_RUNTIME_PIN_FILE"] = str(pin)
            paths = [pin]
        else:
            env["CARBONYL_RUNTIME_PIN_FILE"] = str(tmp_path / "missing")
            paths = [tmp_path / "missing"]
        # Strip any pre-existing env that would interfere
        env["CARBONYL_RUNTIME_TAG"] = ""
        return env, paths

    def test_pin_with_concrete_hash_wraps_with_runtime_prefix(self, tmp_path: Path):
        env, paths = self._isolated(tmp_path, "runtime-hash=deadbeef\n")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=paths):
                tag, source = runtime_pin.resolve_default_tag()
                assert tag == "runtime-deadbeef"
                assert source == "pin"

    def test_pin_with_runtime_prefix_already(self, tmp_path: Path):
        env, paths = self._isolated(tmp_path, "runtime-hash=runtime-abc123\n")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=paths):
                tag, source = runtime_pin.resolve_default_tag()
                assert tag == "runtime-abc123"
                assert source == "pin"

    def test_pin_latest_sentinel_returns_latest(self, tmp_path: Path):
        env, paths = self._isolated(tmp_path, "runtime-hash=runtime-latest\n")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=paths):
                tag, source = runtime_pin.resolve_default_tag()
                assert tag == runtime_pin.LATEST_SENTINEL
                assert source == "latest-sentinel"

    def test_no_pin_returns_unpinned_latest(self, tmp_path: Path):
        env, paths = self._isolated(tmp_path, None)
        with patch.dict(os.environ, env, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=paths):
                tag, source = runtime_pin.resolve_default_tag()
                assert tag == runtime_pin.LATEST_SENTINEL
                assert source == "unpinned"

    def test_env_var_overrides_pin(self, tmp_path: Path):
        env, paths = self._isolated(tmp_path, "runtime-hash=deadbeef\n")
        env["CARBONYL_RUNTIME_TAG"] = "runtime-overridden"
        with patch.dict(os.environ, env, clear=False):
            with patch.object(runtime_pin, "_candidate_pin_paths", return_value=paths):
                tag, source = runtime_pin.resolve_default_tag()
                assert tag == "runtime-overridden"
                assert source == "env"


class TestRepoPin:
    """Sanity check: the repo's own pin file is parseable."""

    def test_repo_pin_file_exists_and_parses(self):
        pin = runtime_pin.find_pin_file()
        assert pin is not None, ".carbonyl-runtime-version not found in repo"
        hash_value = runtime_pin.read_pinned_hash()
        assert hash_value, "repo pin file does not declare runtime-hash"
        # Hash should look like a hex-ish identifier or the sentinel
        assert hash_value == runtime_pin.LATEST_SENTINEL or all(
            c in "0123456789abcdef" for c in hash_value.lower()
        ), f"unexpected runtime-hash value: {hash_value!r}"
