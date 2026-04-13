"""Tests for carbonyl_agent.session.SessionManager."""
import pytest

from carbonyl_agent.session import SessionManager


@pytest.fixture
def sm(tmp_path):
    """SessionManager with a temporary session directory."""
    return SessionManager(session_dir=tmp_path)


# --- Name validation ---

class TestNameValidation:
    def test_valid_names(self, sm):
        for name in ["myapp", "my-app", "My.App", "test_session", "a", "A1.b-c_d"]:
            profile = sm.create(name)
            assert profile.is_dir()

    def test_rejects_empty(self, sm):
        with pytest.raises(ValueError):
            sm.create("")

    def test_rejects_too_long(self, sm):
        with pytest.raises(ValueError):
            sm.create("a" * 65)

    def test_max_length_ok(self, sm):
        profile = sm.create("a" * 64)
        assert profile.is_dir()

    def test_rejects_path_traversal_dotdot(self, sm):
        with pytest.raises(ValueError):
            sm.create("../../etc")

    def test_rejects_path_traversal_slash(self, sm):
        with pytest.raises(ValueError):
            sm.create("foo/bar")

    def test_rejects_backslash(self, sm):
        with pytest.raises(ValueError):
            sm.create("foo\\bar")

    def test_rejects_null_byte(self, sm):
        with pytest.raises(ValueError):
            sm.create("foo\x00bar")

    def test_rejects_spaces(self, sm):
        with pytest.raises(ValueError):
            sm.create("foo bar")

    def test_rejects_special_chars(self, sm):
        for name in ["foo@bar", "foo#bar", "foo$bar", "foo;bar"]:
            with pytest.raises(ValueError):
                sm.create(name)


# --- CRUD operations ---

class TestCRUD:
    def test_create_and_exists(self, sm):
        sm.create("test1")
        assert sm.exists("test1")

    def test_create_duplicate_raises(self, sm):
        sm.create("test1")
        with pytest.raises(FileExistsError):
            sm.create("test1")

    def test_list_sessions(self, sm):
        sm.create("alpha")
        sm.create("beta")
        sessions = sm.list()
        names = [s["name"] for s in sessions]
        assert "alpha" in names
        assert "beta" in names

    def test_destroy(self, sm):
        sm.create("doomed")
        assert sm.exists("doomed")
        sm.destroy("doomed")
        assert not sm.exists("doomed")

    def test_destroy_nonexistent_raises(self, sm):
        with pytest.raises(KeyError):
            sm.destroy("nope")

    def test_get_returns_session(self, sm):
        sm.create("s1", tags=["web"])
        s = sm.get("s1")
        assert s.meta.name == "s1"
        assert "web" in s.meta.tags

    def test_get_nonexistent_raises(self, sm):
        with pytest.raises(KeyError):
            sm.get("nope")


# --- Fork and snapshot ---

class TestForkAndSnapshot:
    def test_fork(self, sm):
        sm.create("original")
        # Write a marker file in the profile
        marker = sm.profile_dir("original") / "marker.txt"
        marker.write_text("hello")

        sm.fork("original", "copy")
        assert sm.exists("copy")
        copy_marker = sm.profile_dir("copy") / "marker.txt"
        assert copy_marker.read_text() == "hello"

        # Verify independence
        s = sm.get("copy")
        assert s.meta.forked_from == "original"

    def test_snapshot_and_restore(self, sm):
        sm.create("main")
        marker = sm.profile_dir("main") / "state.txt"
        marker.write_text("v1")

        snap = sm.snapshot("main", "checkpoint")
        assert sm.exists(snap)

        # Modify main
        marker.write_text("v2")
        assert marker.read_text() == "v2"

        # Restore
        sm.restore("main", "checkpoint")
        assert marker.read_text() == "v1"

    def test_snapshot_meta(self, sm):
        sm.create("src")
        snap_name = sm.snapshot("src", "tag1")
        s = sm.get(snap_name)
        assert s.meta.snapshot_of == "src"
        assert s.meta.forked_from == "src"
