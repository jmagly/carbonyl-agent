"""Tests for carbonyl_agent._logging (#14)."""
from __future__ import annotations

import logging

import pytest

from carbonyl_agent._logging import (
    enable_debug_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def reset_logging_state(monkeypatch):
    """Each test gets a fresh logger configuration.

    The module is module-level cached, so we have to reset the singleton
    flag and clear handlers between tests to avoid bleed.
    """
    from carbonyl_agent import _logging
    monkeypatch.setattr(_logging, "_CONFIGURED", False)
    root = logging.getLogger("carbonyl_agent")
    original_handlers = list(root.handlers)
    original_level = root.level
    root.handlers.clear()
    yield
    root.handlers.clear()
    for h in original_handlers:
        root.addHandler(h)
    root.setLevel(original_level)


class TestGetLogger:
    def test_returns_namespaced_logger(self):
        log = get_logger("foo")
        assert log.name == "carbonyl_agent.foo"

    def test_module_dunder_name_is_normalized(self):
        log = get_logger("carbonyl_agent.daemon")
        assert log.name == "carbonyl_agent.daemon"  # not double-prefixed

    def test_root_namespace_passes_through(self):
        log = get_logger("carbonyl_agent")
        assert log.name == "carbonyl_agent"


class TestLevelResolution:
    def test_default_is_info(self, monkeypatch):
        monkeypatch.delenv("CARBONYL_LOG_LEVEL", raising=False)
        monkeypatch.delenv("CARBONYL_DEBUG", raising=False)
        get_logger("test")  # triggers configuration
        assert logging.getLogger("carbonyl_agent").level == logging.INFO

    def test_carbonyl_debug_env_enables_debug(self, monkeypatch):
        monkeypatch.delenv("CARBONYL_LOG_LEVEL", raising=False)
        monkeypatch.setenv("CARBONYL_DEBUG", "1")
        get_logger("test")
        assert logging.getLogger("carbonyl_agent").level == logging.DEBUG

    def test_explicit_log_level_wins(self, monkeypatch):
        monkeypatch.setenv("CARBONYL_LOG_LEVEL", "WARNING")
        monkeypatch.setenv("CARBONYL_DEBUG", "1")
        get_logger("test")
        # Explicit level wins over CARBONYL_DEBUG shorthand
        assert logging.getLogger("carbonyl_agent").level == logging.WARNING

    def test_invalid_level_falls_back(self, monkeypatch):
        monkeypatch.setenv("CARBONYL_LOG_LEVEL", "NOT_A_REAL_LEVEL")
        monkeypatch.delenv("CARBONYL_DEBUG", raising=False)
        get_logger("test")
        assert logging.getLogger("carbonyl_agent").level == logging.INFO


class TestEnableDebug:
    def test_overrides_runtime_level(self, monkeypatch):
        monkeypatch.delenv("CARBONYL_LOG_LEVEL", raising=False)
        monkeypatch.delenv("CARBONYL_DEBUG", raising=False)
        get_logger("test")
        assert logging.getLogger("carbonyl_agent").level == logging.INFO
        enable_debug_logging()
        assert logging.getLogger("carbonyl_agent").level == logging.DEBUG


class TestHandlerSetup:
    def test_handler_attached_only_once(self):
        get_logger("a")
        get_logger("b")
        get_logger("c")
        # Single StreamHandler regardless of how many sub-loggers we ask for
        root = logging.getLogger("carbonyl_agent")
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) == 1

    def test_propagation_disabled(self):
        get_logger("test")
        # Library code must not bubble into the application's root logger
        assert logging.getLogger("carbonyl_agent").propagate is False
