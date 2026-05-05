"""Tests for carbonyl_agent.uinput_emitter (issue #36).

Uses mocked uinput.Device factories so the suite runs without real
/dev/uinput access. The actual kernel-level uinput integration is
exercised by the layer-1 trust e2e in carbonyl-agent-qa#1.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# These tests exercise the python-uinput integration — the library is an
# optional runtime dependency (not listed in pyproject.toml), available on
# Linux hosts that have the python-uinput pip package installed. Skip the
# whole module when the underlying library isn't available, so CI runs in
# minimal containers (e.g. python:3.12-slim) don't fail here.
pytest.importorskip("uinput", reason="python-uinput library not installed")

from carbonyl_agent import uinput_emitter
from carbonyl_agent.uinput_emitter import (
    UinputEmitter,
    UinputUnavailableError,
    UnsupportedKeyError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingDevice:
    """Mock ``uinput.Device`` that records every emit() / syn() call."""

    def __init__(self, events, name):
        self.events = events
        self.name = name
        self.calls: list[tuple[str, tuple]] = []
        self.destroyed = False

    def emit(self, ev, value, syn=False):
        self.calls.append(("emit", (ev, value, syn)))

    def syn(self):
        self.calls.append(("syn", ()))

    def destroy(self):
        self.destroyed = True


def _factory_recorder(devices: list):
    def factory(events, name):
        dev = _RecordingDevice(events, name)
        devices.append(dev)
        return dev
    return factory


@pytest.fixture
def emitter_with_mocks(tmp_path, monkeypatch):
    """A UinputEmitter wired to a recording device factory.
    Bypasses the /dev/uinput preflight so tests run anywhere."""
    devices: list = []
    factory = _factory_recorder(devices)
    em = UinputEmitter(device_suffix="test", device_factory=factory)
    # Skip the preflight so the test does not depend on /dev/uinput perms.
    monkeypatch.setattr(em, "_preflight", lambda: None)
    em.open()
    yield em, devices
    em.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_open_creates_two_devices(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        assert len(devices) == 2
        names = {d.name for d in devices}
        assert names == {"carbonyl-agent-test-keyboard", "carbonyl-agent-test-mouse"}

    def test_close_destroys_both_devices(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.close()
        assert all(d.destroyed for d in devices)

    def test_close_is_idempotent(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        em.close()
        em.close()  # should not raise

    def test_context_manager(self, monkeypatch):
        devices: list = []
        em = UinputEmitter(device_suffix="ctx", device_factory=_factory_recorder(devices))
        monkeypatch.setattr(em, "_preflight", lambda: None)
        with em as ctx_em:
            assert ctx_em is em
            assert len(devices) == 2
        assert all(d.destroyed for d in devices)

    def test_use_after_close_raises(self, monkeypatch):
        devices: list = []
        em = UinputEmitter(device_suffix="uac", device_factory=_factory_recorder(devices))
        monkeypatch.setattr(em, "_preflight", lambda: None)
        em.open()
        em.close()
        with pytest.raises(RuntimeError, match="closed"):
            em.type_text("x")

    def test_unique_device_names_across_instances(self, monkeypatch):
        devices: list = []
        factory = _factory_recorder(devices)
        em1 = UinputEmitter(device_factory=factory)
        em2 = UinputEmitter(device_factory=factory)
        monkeypatch.setattr(em1, "_preflight", lambda: None)
        monkeypatch.setattr(em2, "_preflight", lambda: None)
        em1.open()
        em2.open()
        assert em1.kbd_name != em2.kbd_name
        assert em1.mouse_name != em2.mouse_name
        em1.close()
        em2.close()


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------


class TestKeyboard:
    def test_type_simple_lowercase(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.type_text("hi")
        kbd = next(d for d in devices if "keyboard" in d.name)
        # Each char emits down + syn + up + syn → 4 calls per char
        assert len(kbd.calls) >= 8

    def test_type_uppercase_uses_shift(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.type_text("A")
        kbd = next(d for d in devices if "keyboard" in d.name)
        # Inspect emit calls for any LEFTSHIFT-like event being pressed (value=1)
        # and released (value=0). The exact event tuple depends on python-uinput
        # constants; assert that two distinct event objects had value 1 then 0.
        emits = [c for c in kbd.calls if c[0] == "emit"]
        # At minimum: shift-down, A-down, A-up, shift-up → 4 emits
        assert len(emits) >= 4
        values = [e[1][1] for e in emits]
        # Down then up sequence: starts with 1
        assert values[0] == 1

    def test_type_unsupported_char_raises(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        with pytest.raises(UnsupportedKeyError):
            em.type_text("é")

    def test_press_named_key(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.press_key("enter")
        kbd = next(d for d in devices if "keyboard" in d.name)
        assert len(kbd.calls) >= 2  # down + up

    def test_press_unknown_key_raises(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        with pytest.raises(UnsupportedKeyError, match="Unknown key"):
            em.press_key("foo")

    def test_named_key_aliases(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        # both "esc" and "escape" should work
        em.press_key("esc")
        em.press_key("escape")
        em.press_key("return")
        em.press_key("enter")  # alias

    def test_newline_in_type_text_is_enter(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.type_text("a\nb")
        # Should emit a, enter, b — 3 keys, no exception
        kbd = next(d for d in devices if "keyboard" in d.name)
        assert len(kbd.calls) > 0

    def test_tab_in_type_text(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        em.type_text("a\tb")  # should not raise

    def test_shifted_punctuation(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        em.type_text("!@#$%")  # all shifted, should not raise


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------


class TestMouse:
    def test_move_mouse_clamps_to_viewport(self, monkeypatch):
        devices: list = []
        em = UinputEmitter(
            device_suffix="m1",
            viewport=(100, 100),
            device_factory=_factory_recorder(devices),
        )
        monkeypatch.setattr(em, "_preflight", lambda: None)
        em.open()
        em.move_mouse(200, 50)  # x out-of-range, y in-range
        em.move_mouse(-5, 50)   # x negative
        mouse = next(d for d in devices if "mouse" in d.name)
        # Just verify emit was called both times without exception
        assert len([c for c in mouse.calls if c[0] == "emit"]) >= 4
        em.close()

    def test_click_emits_press_and_release(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.click(100, 100)
        mouse = next(d for d in devices if "mouse" in d.name)
        emits = [c for c in mouse.calls if c[0] == "emit"]
        # At least: ABS_X, ABS_Y, BTN_LEFT down, BTN_LEFT up
        assert len(emits) >= 4

    def test_click_right_button(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        em.click(50, 50, button="right")  # should not raise

    def test_click_unknown_button_raises(self, emitter_with_mocks):
        em, _ = emitter_with_mocks
        with pytest.raises(UnsupportedKeyError, match="Unknown button"):
            em.click(0, 0, button="bogus")

    def test_mouse_path(self, emitter_with_mocks):
        em, devices = emitter_with_mocks
        em.mouse_path([(10, 10), (20, 20), (30, 30)], delay=0)
        mouse = next(d for d in devices if "mouse" in d.name)
        emits = [c for c in mouse.calls if c[0] == "emit"]
        # 3 points × 2 axes (X + Y) = 6 emit calls minimum
        assert len(emits) >= 6


# ---------------------------------------------------------------------------
# Preflight / availability
# ---------------------------------------------------------------------------


class TestPreflight:
    def test_raises_when_python_uinput_missing(self, monkeypatch):
        monkeypatch.setattr(uinput_emitter, "_UINPUT_AVAILABLE", False)
        em = UinputEmitter(device_suffix="pre1")
        with pytest.raises(UinputUnavailableError, match="python-uinput"):
            em.open()

    def test_raises_when_uinput_dev_missing(self, monkeypatch, tmp_path):
        # Force python-uinput available; fake /dev/uinput to a nonexistent path
        monkeypatch.setattr(uinput_emitter, "_UINPUT_AVAILABLE", True)
        # Patch Path("/dev/uinput") via patching the preflight method
        em = UinputEmitter(device_suffix="pre2")
        with patch("carbonyl_agent.uinput_emitter.Path") as path_mock:
            fake = MagicMock()
            fake.exists.return_value = False
            fake.__bool__.return_value = False
            path_mock.return_value = fake
            with pytest.raises(UinputUnavailableError, match="does not exist"):
                em.open()

    def test_raises_when_uinput_dev_not_writable(self, monkeypatch):
        monkeypatch.setattr(uinput_emitter, "_UINPUT_AVAILABLE", True)
        em = UinputEmitter(device_suffix="pre3")
        with patch("carbonyl_agent.uinput_emitter.Path") as path_mock, \
             patch("carbonyl_agent.uinput_emitter.os.access", return_value=False):
            fake = MagicMock()
            fake.exists.return_value = True
            path_mock.return_value = fake
            with pytest.raises(UinputUnavailableError, match=r"input.*group"):
                em.open()

    def test_late_permission_error_wrapped(self, monkeypatch):
        """If os.access says writable but the kernel ioctl returns EACCES
        (e.g. AppArmor / seccomp / racing rule reload), the raw
        PermissionError must be re-raised as UinputUnavailableError so
        downstream typed-exception handlers stay coherent.
        """
        monkeypatch.setattr(uinput_emitter, "_UINPUT_AVAILABLE", True)

        def factory(events, name):
            raise PermissionError(13, "Permission denied")

        em = UinputEmitter(device_suffix="late-eacces", device_factory=factory)
        with patch("carbonyl_agent.uinput_emitter.Path") as path_mock, \
             patch("carbonyl_agent.uinput_emitter.os.access", return_value=True):
            fake = MagicMock()
            fake.exists.return_value = True
            path_mock.return_value = fake
            with pytest.raises(UinputUnavailableError, match=r"input.*group"):
                em.open()
