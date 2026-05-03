"""Tests for carbonyl_agent.profile (W1.4 — issue #41)."""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from carbonyl_agent.profile import (
    PersonaMeta,
    ProfileManager,
    _resolve_profiles_dir,
    list_personas,
)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestProfilesDirResolution:
    def test_explicit_arg_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CARBONYL_AGENT_PROFILES_DIR", str(tmp_path / "env"))
        assert _resolve_profiles_dir(tmp_path / "explicit") == tmp_path / "explicit"

    def test_env_var_falls_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CARBONYL_AGENT_PROFILES_DIR", str(tmp_path / "env"))
        assert _resolve_profiles_dir(None) == tmp_path / "env"

    def test_default_when_no_arg_or_env(self, monkeypatch):
        monkeypatch.delenv("CARBONYL_AGENT_PROFILES_DIR", raising=False)
        result = _resolve_profiles_dir(None)
        assert result.parts[-3:] == ("carbonyl-agent", "profiles", "")[:2] or \
            str(result).endswith("carbonyl-agent/profiles")

    def test_tilde_expansion(self, monkeypatch):
        monkeypatch.delenv("CARBONYL_AGENT_PROFILES_DIR", raising=False)
        result = _resolve_profiles_dir("~/foo/bar")
        assert "~" not in str(result)
        assert str(result).endswith("foo/bar")


# ---------------------------------------------------------------------------
# Slug validation (defense-in-depth)
# ---------------------------------------------------------------------------


class TestPersonaNameValidation:
    @pytest.mark.parametrize("bad", [
        "",
        "../escape",
        "a/b",
        "a\\b",
        "with\x00null",
        "x" * 65,
        "has space",
        "has?",
    ])
    def test_invalid_names_rejected(self, bad, tmp_path):
        with pytest.raises(ValueError):
            ProfileManager(bad, profiles_dir=tmp_path)

    @pytest.mark.parametrize("good", [
        "alice",
        "alice_throwaway",
        "alice-test-1",
        "x.y.z",
        "A1B2",
    ])
    def test_valid_names_accepted(self, good, tmp_path):
        pm = ProfileManager(good, profiles_dir=tmp_path)
        assert pm.persona == good


# ---------------------------------------------------------------------------
# Lock semantics
# ---------------------------------------------------------------------------


class TestLock:
    def test_acquire_release_idempotent(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.acquire_lock()
        pm.acquire_lock()  # idempotent
        pm.release_lock()
        pm.release_lock()  # idempotent

    def test_context_manager(self, tmp_path):
        with ProfileManager("alice", profiles_dir=tmp_path) as pm:
            assert pm.lock_path.exists()
        # File still exists, just unlocked

    def test_lock_records_pid(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.acquire_lock()
        try:
            content = pm.lock_path.read_text().strip()
            assert content == str(os.getpid())
        finally:
            pm.release_lock()

    def test_two_personas_no_collision(self, tmp_path):
        a = ProfileManager("alice", profiles_dir=tmp_path)
        b = ProfileManager("bob", profiles_dir=tmp_path)
        a.acquire_lock()
        b.acquire_lock()
        try:
            assert a.profile_dir != b.profile_dir
            assert a.lock_path != b.lock_path
        finally:
            a.release_lock()
            b.release_lock()


def _hold_lock(profiles_dir: str, persona: str, ready_q, release_q) -> None:
    pm = ProfileManager(persona, profiles_dir=profiles_dir)
    pm.acquire_lock()
    ready_q.put(os.getpid())
    release_q.get(timeout=10)
    pm.release_lock()


class TestLockConcurrency:
    def test_second_open_raises_with_pid(self, tmp_path):
        ctx = mp.get_context("spawn")
        ready: "mp.Queue[int]" = ctx.Queue()
        release: "mp.Queue[bool]" = ctx.Queue()
        proc = ctx.Process(
            target=_hold_lock, args=(str(tmp_path), "alice", ready, release)
        )
        proc.start()
        try:
            holder_pid = ready.get(timeout=10)
            other = ProfileManager("alice", profiles_dir=tmp_path)
            with pytest.raises(RuntimeError, match=str(holder_pid)):
                other.acquire_lock()
        finally:
            release.put(True)
            proc.join(timeout=10)
            if proc.is_alive():
                proc.terminate()


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


class TestPurge:
    def test_purge_removes_contents_keeps_dir(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        # Materialize some state
        (pm.profile_dir / "Cookies").write_bytes(b"chocolate-chip")
        (pm.profile_dir / "Default").mkdir()
        (pm.profile_dir / "Default" / "Local Storage").write_text("token=abc")

        pm.purge_profile()

        assert pm.profile_dir.exists()
        assert list(pm.profile_dir.iterdir()) == []

    def test_purge_refuses_when_locked_by_other_process(self, tmp_path):
        ctx = mp.get_context("spawn")
        ready: "mp.Queue[int]" = ctx.Queue()
        release: "mp.Queue[bool]" = ctx.Queue()
        proc = ctx.Process(
            target=_hold_lock, args=(str(tmp_path), "alice", ready, release)
        )
        proc.start()
        try:
            ready.get(timeout=10)
            other = ProfileManager("alice", profiles_dir=tmp_path)
            with pytest.raises(RuntimeError, match="in use"):
                other.purge_profile()
        finally:
            release.put(True)
            proc.join(timeout=10)
            if proc.is_alive():
                proc.terminate()

    def test_purge_allowed_when_self_holds_lock(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.acquire_lock()
        try:
            (pm.profile_dir / "Cookies").write_bytes(b"x")
            pm.purge_profile()
            assert list(pm.profile_dir.iterdir()) == []
        finally:
            pm.release_lock()


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_roundtrip_preserves_files(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        (pm.profile_dir / "Cookies").write_bytes(b"chocolate-chip")
        (pm.profile_dir / "Default").mkdir()
        (pm.profile_dir / "Default" / "Preferences").write_text('{"foo": 1}')
        # Touch metadata so it makes it into the archive
        pm.metadata()

        archive = tmp_path / "backup.tar.gz"
        out = pm.export_profile(archive)
        assert out == archive
        assert archive.is_file()
        assert archive.stat().st_size > 0

        # Wipe and re-import
        pm.purge_profile()
        assert list(pm.profile_dir.iterdir()) == []

        pm.import_profile(archive)
        assert (pm.profile_dir / "Cookies").read_bytes() == b"chocolate-chip"
        assert (pm.profile_dir / "Default" / "Preferences").read_text() == '{"foo": 1}'

    def test_export_excludes_lock_file(self, tmp_path):
        import tarfile

        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.acquire_lock()
        try:
            (pm.profile_dir / "Cookies").write_bytes(b"x")
            archive = tmp_path / "backup.tar.gz"
            pm.export_profile(archive)
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
            assert not any("profile.lock" in n for n in names)
            assert not any("SingletonLock" in n for n in names)
        finally:
            pm.release_lock()

    def test_import_into_fresh_persona(self, tmp_path):
        # Persona A exports
        a = ProfileManager("alice", profiles_dir=tmp_path)
        (a.profile_dir / "Cookies").write_bytes(b"alice-token")
        archive = tmp_path / "alice.tar.gz"
        a.export_profile(archive)

        # Persona B imports into a different profile root
        new_root = tmp_path / "elsewhere"
        b = ProfileManager("bob", profiles_dir=new_root)
        b.import_profile(archive)
        # The imported tree had top-level "profile/" so cookies land at the
        # right place under bob's persona.
        assert (b.profile_dir / "Cookies").read_bytes() == b"alice-token"

    def test_import_missing_file_raises(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            pm.import_profile(tmp_path / "does-not-exist.tar.gz")

    def test_import_rejects_path_traversal(self, tmp_path):
        import tarfile

        pm = ProfileManager("alice", profiles_dir=tmp_path)
        # Hand-craft a tarball with an entry that escapes
        evil = tmp_path / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            payload = tmp_path / "payload"
            payload.write_text("pwn")
            ti = tar.gettarinfo(name=str(payload), arcname="../escape.txt")
            with payload.open("rb") as f:
                tar.addfile(ti, f)
        with pytest.raises(RuntimeError, match="path-traversal"):
            pm.import_profile(evil)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_first_call_creates_meta(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        meta = pm.metadata()
        assert isinstance(meta, PersonaMeta)
        assert meta.name == "alice"
        assert meta.created_at  # ISO timestamp
        assert meta.last_input_backend is None

    def test_record_input_backend_persists(self, tmp_path):
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.record_input_backend("uinput")
        # Re-read from disk
        pm2 = ProfileManager("alice", profiles_dir=tmp_path)
        assert pm2.metadata().last_input_backend == "uinput"

    def test_record_input_backend_is_informational(self, tmp_path):
        """Profile state must not be partitioned by input_backend — recording
        a backend then reopening with a different one must still see the same
        profile_dir."""
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        pm.record_input_backend("pty")
        original_dir = pm.profile_dir
        (original_dir / "marker").write_text("x")

        pm2 = ProfileManager("alice", profiles_dir=tmp_path)
        pm2.record_input_backend("uinput")
        assert pm2.profile_dir == original_dir
        assert (pm2.profile_dir / "marker").read_text() == "x"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestListPersonas:
    def test_empty_root(self, tmp_path):
        assert list_personas(tmp_path / "missing") == []
        assert list_personas(tmp_path) == []

    def test_lists_created_personas(self, tmp_path):
        ProfileManager("alice", profiles_dir=tmp_path).profile_dir
        ProfileManager("bob", profiles_dir=tmp_path).metadata()
        # Add a junk dir that should be ignored
        (tmp_path / "not-a-persona").mkdir()
        assert list_personas(tmp_path) == ["alice", "bob"]


# ---------------------------------------------------------------------------
# CarbonylBrowser integration
# ---------------------------------------------------------------------------


class TestBrowserIntegration:
    def test_persona_and_session_mutually_exclusive(self):
        from carbonyl_agent import CarbonylBrowser

        with pytest.raises(ValueError, match="mutually exclusive"):
            CarbonylBrowser(session="s", persona="p")

    def test_purge_export_import_require_persona(self):
        from carbonyl_agent import CarbonylBrowser

        b = CarbonylBrowser()
        with pytest.raises(RuntimeError, match="persona="):
            b.purge_profile()
        with pytest.raises(RuntimeError, match="persona="):
            b.export_profile("/tmp/x.tar.gz")
        with pytest.raises(RuntimeError, match="persona="):
            b.import_profile("/tmp/x.tar.gz")

    def test_purge_via_browser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CARBONYL_AGENT_PROFILES_DIR", str(tmp_path))
        from carbonyl_agent import CarbonylBrowser

        b = CarbonylBrowser(persona="alice")
        # Pre-populate
        pm = ProfileManager("alice", profiles_dir=tmp_path)
        (pm.profile_dir / "Cookies").write_text("x")
        b.purge_profile()
        assert list(pm.profile_dir.iterdir()) == []

    def test_export_import_via_browser(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CARBONYL_AGENT_PROFILES_DIR", str(tmp_path))
        from carbonyl_agent import CarbonylBrowser

        pm = ProfileManager("alice", profiles_dir=tmp_path)
        (pm.profile_dir / "Cookies").write_text("alice-token")

        b = CarbonylBrowser(persona="alice")
        archive = tmp_path / "alice.tar.gz"
        out = b.export_profile(str(archive))
        assert Path(out).is_file()

        b.purge_profile()
        b.import_profile(str(archive))
        assert (pm.profile_dir / "Cookies").read_text() == "alice-token"
