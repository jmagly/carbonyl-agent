"""Per-persona browser profile management.

Higher-level façade over :class:`carbonyl_agent.session.SessionManager` that
fronts named, persistent Chromium ``user-data-dir`` profiles with
persona-keyed semantics:

- Profiles live under ``CARBONYL_AGENT_PROFILES_DIR`` (default
  ``~/.config/carbonyl-agent/profiles/``), isolated from the runtime
  session store (``~/.local/share/carbonyl/sessions/``).
- A file lock prevents accidental dual-open of the same persona, mirroring
  Chromium's own ``SingletonLock`` but enforced *before* spawn so the caller
  gets an actionable :class:`RuntimeError` with the holding PID.
- ``export_profile`` / ``import_profile`` give operators a public tar.gz
  pipeline for backup, CI seeding, and persona transplant — no more reaching
  into private SessionManager paths.
- ``purge_profile`` empties a persona's data while preserving its name,
  supporting "rotate this persona" workflows distinct from full destroy.

Profile state is portable across input backends (``pty`` vs ``uinput``);
nothing in this module is keyed on the backend.

Usage::

    from carbonyl_agent import CarbonylBrowser

    b = CarbonylBrowser(persona="my_throwaway")
    b.open("https://example.com")
    b.close()  # cookies, localStorage, etc. survive

    b.purge_profile()                       # rotate the persona
    b.export_profile("/backups/p.tar.gz")  # snapshot
    b.import_profile("/backups/p.tar.gz")  # restore
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import shutil
import tarfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

_ENV_PROFILES_DIR = "CARBONYL_AGENT_PROFILES_DIR"
_DEFAULT_PROFILES_DIR = Path.home() / ".config" / "carbonyl-agent" / "profiles"

# Same slug grammar as SessionManager — letters, digits, underscores,
# hyphens, dots; max 64 chars. Defense-in-depth path-traversal checks
# below this regex.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_PROFILE_SUBDIR = "profile"
_LOCK_FILENAME = "profile.lock"
_META_FILENAME = "persona.json"


@dataclass
class PersonaMeta:
    """Metadata stored alongside each persona profile.

    ``last_input_backend`` is informational only — profiles are portable
    across backends, recorded purely so operators can audit which
    environments touched the persona.
    """

    name: str
    created_at: str
    last_input_backend: str | None = None
    tags: list[str] = field(default_factory=list)


def _resolve_profiles_dir(profiles_dir: Path | str | None) -> Path:
    if profiles_dir is not None:
        return Path(profiles_dir).expanduser()
    env = os.environ.get(_ENV_PROFILES_DIR)
    if env:
        return Path(env).expanduser()
    return _DEFAULT_PROFILES_DIR


def _validate_persona_name(name: str) -> None:
    if not name or "\x00" in name or ".." in name or "/" in name or "\\" in name:
        raise ValueError(
            f"Invalid persona name {name!r}: contains forbidden characters."
        )
    if not _SLUG_RE.match(name):
        raise ValueError(
            f"Invalid persona name {name!r}. "
            "Use letters, digits, underscores, hyphens, and dots (max 64 chars)."
        )


def _iso_now() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ProfileManager:
    """Owns one persona's on-disk state.

    Acquire the lock with :meth:`acquire_lock` before passing
    :attr:`profile_dir` to Chromium as ``--user-data-dir``. Release with
    :meth:`release_lock` (or :meth:`close`) when the browser exits.

    The class is a context manager — ``with ProfileManager(...) as pm:``
    acquires and releases the lock automatically.
    """

    def __init__(
        self,
        persona: str,
        *,
        profiles_dir: Path | str | None = None,
    ) -> None:
        _validate_persona_name(persona)
        self.persona = persona
        self._root = _resolve_profiles_dir(profiles_dir)
        self._persona_dir = self._root / persona
        self._profile_dir = self._persona_dir / _PROFILE_SUBDIR
        self._lock_path = self._persona_dir / _LOCK_FILENAME
        self._meta_path = self._persona_dir / _META_FILENAME
        self._lock_fd: int | None = None

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def profile_dir(self) -> Path:
        """Path to pass to Chromium as ``--user-data-dir``. Created on demand."""
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        return self._profile_dir

    @property
    def persona_dir(self) -> Path:
        return self._persona_dir

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------

    def acquire_lock(self) -> None:
        """Acquire an exclusive file lock on the persona's profile dir.

        Raises :class:`RuntimeError` with the holder's PID if another
        process already holds the lock.
        """
        if self._lock_fd is not None:
            return
        self._persona_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = self._read_lock_pid(fd)
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError(
                    f"Profile {self.persona!r} is already open by PID "
                    f"{holder if holder is not None else 'unknown'}"
                ) from exc
            raise
        # Lock acquired — record our PID so future contenders can report it.
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
        self._lock_fd = fd

    def release_lock(self) -> None:
        """Release the file lock if held. Safe to call repeatedly."""
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(self._lock_fd)
            finally:
                self._lock_fd = None

    @staticmethod
    def _read_lock_pid(fd: int) -> int | None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, 32).decode("ascii", errors="replace").strip()
            return int(data) if data else None
        except (OSError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __enter__(self) -> "ProfileManager":
        self.acquire_lock()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release_lock()

    def close(self) -> None:
        self.release_lock()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def metadata(self) -> PersonaMeta:
        if self._meta_path.is_file():
            data = json.loads(self._meta_path.read_text())
            return PersonaMeta(
                name=data["name"],
                created_at=data["created_at"],
                last_input_backend=data.get("last_input_backend"),
                tags=data.get("tags", []),
            )
        meta = PersonaMeta(name=self.persona, created_at=_iso_now())
        self._write_meta(meta)
        return meta

    def record_input_backend(self, backend: str) -> None:
        meta = self.metadata()
        if meta.last_input_backend == backend:
            return
        meta.last_input_backend = backend
        self._write_meta(meta)

    def _write_meta(self, meta: PersonaMeta) -> None:
        self._persona_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path.write_text(json.dumps(asdict(meta), indent=2) + "\n")

    # ------------------------------------------------------------------
    # Destructive ops
    # ------------------------------------------------------------------

    def purge_profile(self) -> None:
        """Delete the persona's stored browser state.

        Keeps the persona name and metadata; only wipes
        ``profile_dir`` contents. Use :meth:`destroy` to remove the
        persona entirely.

        Refuses to purge while the lock is held by another process.
        """
        self._require_not_externally_locked()
        if self._profile_dir.exists():
            shutil.rmtree(self._profile_dir)
        self._profile_dir.mkdir(parents=True, exist_ok=True)

    def destroy(self) -> None:
        """Remove the persona entirely (profile + metadata + lock file)."""
        self._require_not_externally_locked()
        if self._persona_dir.exists():
            shutil.rmtree(self._persona_dir)

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------

    def export_profile(self, path: Path | str) -> Path:
        """Write the persona's profile state to a tar.gz at ``path``.

        Excludes the lock file (it's not portable). Returns the
        resolved output path.
        """
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        # Ensure the profile dir exists so the archive is at least
        # well-formed even for a fresh persona.
        self._profile_dir.mkdir(parents=True, exist_ok=True)

        def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            if Path(info.name).name == _LOCK_FILENAME:
                return None
            # Exclude Chromium's SingletonLock — stale on restore by
            # definition, and a symlink to a hostname/PID that won't
            # match the destination host.
            if Path(info.name).name == "SingletonLock":
                return None
            return info

        with tarfile.open(out, "w:gz") as tar:
            tar.add(self._profile_dir, arcname=_PROFILE_SUBDIR, filter=_filter)
            if self._meta_path.is_file():
                tar.add(self._meta_path, arcname=_META_FILENAME)
        return out

    def import_profile(self, path: Path | str) -> None:
        """Replace the persona's profile state from a tar.gz at ``path``.

        Refuses to overwrite while the lock is held by another process.
        """
        src = Path(path).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"Import source not found: {src}")
        self._require_not_externally_locked()

        if self._profile_dir.exists():
            shutil.rmtree(self._profile_dir)
        self._persona_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(src, "r:gz") as tar:
            for member in tar.getmembers():
                # Defense-in-depth: refuse archive entries that escape
                # the persona dir.
                target = (self._persona_dir / member.name).resolve()
                try:
                    target.relative_to(self._persona_dir.resolve())
                except ValueError as exc:
                    raise RuntimeError(
                        f"Refusing to extract path-traversal entry {member.name!r}"
                    ) from exc
                tar.extract(member, self._persona_dir, filter="data")

        # If the import didn't include the metadata, regenerate a stub
        # so downstream code can still read created_at.
        if not self._meta_path.is_file():
            self._write_meta(PersonaMeta(name=self.persona, created_at=_iso_now()))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_not_externally_locked(self) -> None:
        """Allow the operation if we hold the lock or it is unlocked.

        Raises if a different process holds the lock.
        """
        if self._lock_fd is not None:
            return
        if not self._lock_path.exists():
            return
        # Try to acquire briefly; release immediately on success.
        fd = os.open(self._lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = self._read_lock_pid(fd)
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise RuntimeError(
                    f"Profile {self.persona!r} is in use by PID "
                    f"{holder if holder is not None else 'unknown'} — "
                    "stop the browser before mutating the profile."
                ) from exc
            raise
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def list_personas(profiles_dir: Path | str | None = None) -> list[str]:
    """Return persona names present under the resolved profiles root."""
    root = _resolve_profiles_dir(profiles_dir)
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / _PROFILE_SUBDIR).is_dir() or (entry / _META_FILENAME).is_file():
            out.append(entry.name)
    return out


__all__ = [
    "PersonaMeta",
    "ProfileManager",
    "list_personas",
]
