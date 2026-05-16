"""Tests for EgressAuditLog size-based rotation (#93).

Lives in a separate file from `test_egress.py` because that file
`importorskip`s `carbonyl_fingerprint` (PyO3 native ext) at module
scope. The rotation logic depends only on the pure-Python
EgressAuditEntry + EgressAuditLog classes — no Persona, no native
extension, no httpx. Splitting these into their own file lets them
run on every CI worker regardless of whether the carbonyl_wreq
extension is built.

Refs: roctinam/carbonyl-agent#93
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from carbonyl_agent.egress import EgressAuditEntry, EgressAuditLog


def _make_entry(request_id: str = "abc") -> EgressAuditEntry:
    """Minimal entry whose serialized line is ~250 bytes — picked so a
    small max_bytes threshold trips after a known number of appends."""
    return EgressAuditEntry(
        request_id=request_id,
        timestamp="2026-05-16T00:00:00+00:00",
        persona_id="rotation-test-persona",
        method="GET",
        url="https://example.com/some/path?with=querystring",
        ja4_expected="t13d1516h2_8daaf6152771_b186095e22b6",
        ja4_actual="t13d1516h2_8daaf6152771_b186095e22b6",
        status_code=200,
        latency_ms=12.5,
        drift=False,
        audit_mode="warn",
    )


def test_rotates_when_threshold_exceeded(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    # Compute line_len from the exact entries the loop will write — the
    # request_id length contributes to the serialized size, and using a
    # default `_make_entry()` here would understate the line size.
    line_len = len(_make_entry("req-0").to_json() + "\n")
    # Three lines fit under the threshold; the fourth must rotate.
    log = EgressAuditLog(log_path, max_bytes=line_len * 3, backup_count=2)

    for i in range(4):
        log.append(_make_entry(f"req-{i}"))

    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "expected .1 backup after threshold crossed"
    assert log_path.exists(), "active log must be re-created with the 4th entry"
    active_lines = log_path.read_text().splitlines()
    assert len(active_lines) == 1, (
        "active log should contain only the post-rotation entry; got: "
        f"{active_lines!r}"
    )
    rotated_lines = rotated.read_text().splitlines()
    assert len(rotated_lines) == 3
    assert json.loads(rotated_lines[0])["request_id"] == "req-0"
    assert json.loads(active_lines[0])["request_id"] == "req-3"


def test_max_bytes_zero_disables_rotation(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    log = EgressAuditLog(log_path, max_bytes=0, backup_count=5)
    for i in range(10):
        log.append(_make_entry(f"req-{i}"))
    assert not log_path.with_suffix(log_path.suffix + ".1").exists()
    assert len(log_path.read_text().splitlines()) == 10


def test_backup_count_zero_truncates_without_history(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    line_len = len(_make_entry("req-0").to_json() + "\n")
    log = EgressAuditLog(log_path, max_bytes=line_len * 2, backup_count=0)
    for i in range(5):
        log.append(_make_entry(f"req-{i}"))
    assert not log_path.with_suffix(log_path.suffix + ".1").exists()
    # The active log only carries entries since the last rotation.
    assert log_path.exists()


def test_drops_oldest_backup_past_count(tmp_path: Path) -> None:
    """After repeated rotations with backup_count=2, .3 must not exist."""
    log_path = tmp_path / "audit.log"
    line_len = len(_make_entry("req-0").to_json() + "\n")
    log = EgressAuditLog(log_path, max_bytes=line_len, backup_count=2)
    for i in range(6):
        log.append(_make_entry(f"req-{i}"))
    assert log_path.exists()
    assert log_path.with_suffix(log_path.suffix + ".1").exists()
    assert log_path.with_suffix(log_path.suffix + ".2").exists()
    assert not log_path.with_suffix(log_path.suffix + ".3").exists()


def test_env_var_max_bytes_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT_MAX_BYTES", "1024")
    log = EgressAuditLog(tmp_path / "audit.log")
    assert log.max_bytes == 1024


def test_env_var_backup_count_overrides_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT_BACKUP_COUNT", "3")
    log = EgressAuditLog(tmp_path / "audit.log")
    assert log.backup_count == 3


def test_env_var_invalid_max_bytes_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT_MAX_BYTES", "not-a-number")
    with pytest.raises(ValueError, match="CARBONYL_FP_AUDIT_MAX_BYTES"):
        EgressAuditLog(tmp_path / "audit.log")


def test_env_var_negative_max_bytes_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT_MAX_BYTES", "-1")
    with pytest.raises(ValueError, match=">= 0"):
        EgressAuditLog(tmp_path / "audit.log")


def test_constructor_args_override_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CARBONYL_FP_AUDIT_MAX_BYTES", "1024")
    monkeypatch.setenv("CARBONYL_FP_AUDIT_BACKUP_COUNT", "3")
    log = EgressAuditLog(tmp_path / "audit.log", max_bytes=2048, backup_count=7)
    assert log.max_bytes == 2048
    assert log.backup_count == 7


def test_default_max_bytes_is_10_mib(tmp_path: Path) -> None:
    """Verify the documented default. If this changes, the docstring +
    module docstring + #93 acceptance criteria must update in lockstep."""
    log = EgressAuditLog(tmp_path / "audit.log")
    assert log.max_bytes == 10 * 1024 * 1024
    assert log.backup_count == 5
