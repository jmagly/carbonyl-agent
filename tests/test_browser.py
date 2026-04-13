"""Tests for carbonyl_agent.browser module."""
import os
from unittest.mock import patch

import pytest

from carbonyl_agent.browser import (
    _DOCKER_FALLBACK_ENV,
    CarbonylBrowser,
    _is_text_char,
    _local_binary,
)


class TestDockerFallbackGate:
    """US-008: Docker fallback requires CARBONYL_ALLOW_DOCKER=1."""

    @patch("carbonyl_agent.browser._local_binary", return_value=None)
    def test_raises_without_env(self, mock_binary):
        """Docker fallback blocked when env var not set."""
        env = {k: v for k, v in os.environ.items() if k != _DOCKER_FALLBACK_ENV}
        with patch.dict(os.environ, env, clear=True):
            b = CarbonylBrowser()
            with pytest.raises(RuntimeError, match="CARBONYL_ALLOW_DOCKER"):
                b.open("https://example.com")

    @patch("carbonyl_agent.browser._local_binary", return_value=None)
    def test_raises_when_env_not_1(self, mock_binary):
        """Docker fallback blocked when env var is set but not '1'."""
        with patch.dict(os.environ, {_DOCKER_FALLBACK_ENV: "0"}):
            b = CarbonylBrowser()
            with pytest.raises(RuntimeError, match="CARBONYL_ALLOW_DOCKER"):
                b.open("https://example.com")


class TestTextFiltering:
    """Basic tests for text extraction utilities."""

    def test_is_text_char_letter(self):
        assert _is_text_char("A") is True

    def test_is_text_char_block(self):
        assert _is_text_char("\u2580") is False  # block element

    def test_is_text_char_space(self):
        assert _is_text_char(" ") is True


class TestLocalBinary:
    """Test binary discovery returns None or a valid path."""

    def test_returns_none_or_path(self):
        result = _local_binary()
        if result is not None:
            assert result.is_file()
