"""Centralized logging setup for carbonyl-agent (#14).

All modules should obtain a logger via :func:`get_logger` rather than
calling ``print()``. The root namespace is ``carbonyl_agent``; sub-
loggers (``carbonyl_agent.browser``, ``carbonyl_agent.daemon``, etc.)
inherit configuration unless overridden.

Configuration precedence (first wins):

1. ``CARBONYL_LOG_LEVEL`` env var — explicit level (DEBUG / INFO / WARNING / ERROR)
2. ``CARBONYL_DEBUG=1`` env var — shorthand for DEBUG
3. Default: INFO

All output goes to ``stderr`` so that library consumers can capture
``stdout`` without log noise. Format includes the logger name so it is
easy to filter (e.g. ``CARBONYL_LOG_LEVEL=DEBUG ... 2>&1 | grep daemon``).

CLI entry points should call :func:`enable_debug_logging` when ``--debug``
is passed; that overrides the env var precedence for the duration of
the process.
"""
from __future__ import annotations

import logging
import os
import sys

_ROOT_LOGGER_NAME = "carbonyl_agent"
_CONFIGURED = False


def _resolve_level() -> int:
    explicit = os.environ.get("CARBONYL_LOG_LEVEL")
    if explicit:
        try:
            return int(getattr(logging, explicit.upper()))
        except (AttributeError, TypeError):
            pass
    if os.environ.get("CARBONYL_DEBUG", "").lower() in ("1", "true", "yes"):
        return logging.DEBUG
    return logging.INFO


def _ensure_configured() -> None:
    """Idempotently configure the root carbonyl_agent logger.

    Library code should NOT call ``logging.basicConfig`` — that mutates
    the application's root logger. Instead we attach our own handler to
    the ``carbonyl_agent`` namespace and propagate=False so applications
    that have configured their own root handler aren't affected.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "[%(name)s] %(levelname)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(_resolve_level())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a sub-logger of ``carbonyl_agent``.

    ``name`` is typically ``__name__`` from the calling module, which
    already includes the ``carbonyl_agent.`` prefix — that's fine; the
    stdlib ``logging`` module deduplicates the namespace correctly.
    """
    _ensure_configured()
    if name == _ROOT_LOGGER_NAME or name.startswith(_ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def enable_debug_logging() -> None:
    """Force DEBUG level — typically wired to a ``--debug`` CLI flag."""
    _ensure_configured()
    logging.getLogger(_ROOT_LOGGER_NAME).setLevel(logging.DEBUG)
