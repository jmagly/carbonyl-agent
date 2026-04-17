"""Tests for carbonyl_agent.browser module."""
import os
from unittest.mock import patch

import pytest

from carbonyl_agent.browser import (
    _DOCKER_FALLBACK_ENV,
    ANTI_BOT_FLAGS,
    ANTI_FEDCM_FLAGS,
    ANTI_ONETAP_FLAGS,
    BASE_CHROMIUM_FLAGS,
    DEFAULT_HEADLESS_FLAGS,
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


class TestFlagGroups:
    """Flag groups are composable and can be selected by agents per scenario."""

    def test_default_is_base_plus_anti_bot(self):
        assert DEFAULT_HEADLESS_FLAGS == BASE_CHROMIUM_FLAGS + ANTI_BOT_FLAGS

    def test_onetap_alias_equals_fedcm(self):
        assert ANTI_ONETAP_FLAGS == ANTI_FEDCM_FLAGS

    def test_fedcm_flag_content(self):
        assert any("FedCm" in f for f in ANTI_FEDCM_FLAGS)

    def test_default_flags_used_when_none_specified(self):
        b = CarbonylBrowser()
        assert b._flags == list(DEFAULT_HEADLESS_FLAGS)

    def test_extra_flags_appended(self):
        extra = ["--disable-features=FedCm"]
        b = CarbonylBrowser(extra_flags=extra)
        assert b._flags == list(DEFAULT_HEADLESS_FLAGS) + extra

    def test_extra_flags_anti_fedcm_group(self):
        b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS)
        assert b._flags[-len(ANTI_FEDCM_FLAGS):] == ANTI_FEDCM_FLAGS

    def test_base_flags_override_replaces_default(self):
        custom = ["--foo", "--bar"]
        b = CarbonylBrowser(base_flags=custom)
        assert b._flags == custom

    def test_base_and_extra_compose(self):
        base = ["--foo"]
        extra = ["--bar"]
        b = CarbonylBrowser(base_flags=base, extra_flags=extra)
        assert b._flags == ["--foo", "--bar"]


class TestViewport:
    """Consumer-controlled CSS viewport (carbonyl issue #37 fix)."""

    def test_default_is_none(self):
        b = CarbonylBrowser()
        assert b._viewport is None

    def test_accepts_tuple(self):
        b = CarbonylBrowser(viewport=(1280, 800))
        assert b._viewport == (1280, 800)
