"""Tests for the cookie-import module (carbonyl-agent#122).

Covers:
- Firefox unencrypted read path
- Chromium read path (mocked libsecret + decryption)
- Copy-then-read tolerates source SQLite locked
- KeyringLocked raised when libsecret collection is locked
- Sensitive-domain denylist matching
- mode-0600 invariant on all written files
- Cookie values never appear in audit log
- write/list/revoke round-trip with provenance
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from carbonyl_agent import cookies as ck

# Chromium decrypt path needs the `cookies` extra (cryptography). Skip the
# whole class of tests cleanly when it isn't installed rather than erroring
# at fixture setup.
_requires_crypto = pytest.mark.skipif(
    not ck._CRYPTO_AVAILABLE,
    reason="cryptography not installed (pip install 'carbonyl-agent[cookies]')",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def firefox_profile(tmp_path: Path) -> ck.ProfileInfo:
    """Create a fake Firefox profile with a cookies.sqlite."""
    profile_dir = tmp_path / "abc123.default"
    profile_dir.mkdir()
    db_path = profile_dir / "cookies.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE moz_cookies (
            host TEXT, name TEXT, value TEXT, path TEXT,
            expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER
        );
        INSERT INTO moz_cookies VALUES
            ('.x.com', 'auth_token', 'ff-auth-value', '/', 1900000000, 1, 1, 2),
            ('.x.com', 'ct0', 'ff-ct0-value', '/', 1900000000, 1, 0, 1),
            ('.example.com', 'other', 'unrelated', '/', 0, 0, 0, 0);
        """
    )
    conn.commit()
    conn.close()
    return ck.ProfileInfo(name="default", path=profile_dir)


@pytest.fixture
def chromium_profile(tmp_path: Path) -> ck.ProfileInfo:
    """Create a fake Chromium profile with encrypted cookies in `Cookies` SQLite."""
    if not ck._CRYPTO_AVAILABLE:
        pytest.skip("cryptography not installed (pip install 'carbonyl-agent[cookies]')")
    profile_dir = tmp_path / "Default"
    profile_dir.mkdir()
    db_path = profile_dir / "Cookies"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE cookies (
            host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB,
            path TEXT, expires_utc INTEGER, is_secure INTEGER,
            is_httponly INTEGER, samesite INTEGER
        );
        """
    )
    # Encrypt one value using the Chromium fallback "peanuts" key so the test
    # exercises the real KDF + AES-CBC path without needing libsecret.
    key = ck._derive_chromium_key(ck._CHROMIUM_LINUX_FALLBACK_PW)
    plaintext = b"chrome-auth-value"
    # PKCS#7 pad to 16
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.CBC(ck._CHROMIUM_LINUX_IV))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    encrypted_blob = b"v10" + ct

    conn.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (".x.com", "auth_token", "", encrypted_blob, "/", 13400000000000000, 1, 1, 2),
    )
    conn.commit()
    conn.close()
    return ck.ProfileInfo(name="Default", path=profile_dir)


# ---------------------------------------------------------------------------
# Sensitive domain matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain,expected", [
    ("mail.google.com", True),
    ("accounts.google.com", True),
    ("paypal.com", True),
    ("login.microsoftonline.com", True),
    ("chase.com", True),
    ("subdomain.chase.com", True),
    ("x.com", False),
    ("example.com", False),
    ("docs.google.com", False),  # NOT mail.google.com
])
def test_is_sensitive_domain(domain: str, expected: bool) -> None:
    assert ck.is_sensitive_domain(domain) is expected


# ---------------------------------------------------------------------------
# Firefox read path
# ---------------------------------------------------------------------------

def test_read_firefox_filters_by_domain(firefox_profile: ck.ProfileInfo) -> None:
    records = ck.read_firefox(firefox_profile, ["x.com"])
    names = sorted(r.name for r in records)
    assert names == ["auth_token", "ct0"]
    # Values present in records (we have them post-read) but next test confirms
    # they don't leak to audit.
    assert all(r.source_browser == "firefox" for r in records)
    assert all(r.source_profile == "default" for r in records)


def test_read_firefox_excludes_other_domains(firefox_profile: ck.ProfileInfo) -> None:
    records = ck.read_firefox(firefox_profile, ["x.com"])
    assert not any(r.host.endswith("example.com") for r in records)


def test_copy_then_read_tolerates_open_source(firefox_profile: ck.ProfileInfo) -> None:
    """Source DB held open by another connection should not block the read."""
    src_db = firefox_profile.path / "cookies.sqlite"
    holder = sqlite3.connect(src_db)
    try:
        # Take a write lock on the source — simulates a live browser.
        holder.execute("BEGIN EXCLUSIVE")
        records = ck.read_firefox(firefox_profile, ["x.com"])
        assert len(records) == 2
    finally:
        holder.rollback()
        holder.close()


# ---------------------------------------------------------------------------
# Chromium decrypt path (no real libsecret — mock the passphrase fetch)
# ---------------------------------------------------------------------------

@_requires_crypto
def test_read_chromium_decrypts_value(
    chromium_profile: ck.ProfileInfo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ck, "_libsecret_safe_storage_password",
        lambda browser: ck._CHROMIUM_LINUX_FALLBACK_PW,
    )
    records = ck.read_chromium("chrome", chromium_profile, ["x.com"])
    assert len(records) == 1
    assert records[0].name == "auth_token"
    assert records[0].value == "chrome-auth-value"


def test_keyring_locked_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """When libsecret reports the collection is locked, raise — never auto-unlock."""
    def _raise(browser):
        raise ck.KeyringLocked("test keyring locked")

    monkeypatch.setattr(ck, "_libsecret_safe_storage_password", _raise)
    profile = ck.ProfileInfo(name="x", path=Path("/nonexistent"))
    with pytest.raises(ck.KeyringLocked):
        ck.read_chromium("chrome", profile, ["x.com"])


# ---------------------------------------------------------------------------
# Destination writer + provenance + revoke
# ---------------------------------------------------------------------------

def test_write_list_revoke_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "session-profile"
    records = [
        ck.CookieRecord(
            host=".x.com", name="auth_token", value="secret-abc",
            source_browser="firefox", source_profile="default",
            expires_utc=ck._now_chromium_micros() + 86400 * 1_000_000,
        ),
    ]
    n = ck.write_to_session_profile(dest, records)
    assert n == 1

    # mode-0600 invariant on the destination Cookies SQLite
    cookies_db = dest / "Default" / "Network" / "Cookies"
    assert cookies_db.is_file()
    mode = stat.S_IMODE(cookies_db.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    # list_imported returns provenance — and crucially, NO value column
    listing = ck.list_imported(dest)
    assert len(listing) == 1
    assert listing[0]["name"] == "auth_token"
    assert listing[0]["source"] == "firefox/default"
    assert "value" not in listing[0]

    # revoke blanks the value
    revoked = ck.revoke_imported(dest, domain="x.com")
    assert revoked == 1
    conn = sqlite3.connect(cookies_db)
    try:
        row = conn.execute(
            "SELECT value, expires_utc FROM cookies WHERE name='auth_token'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == ""
    assert row[1] == 0


def test_revoke_only_targets_imported_cookies(tmp_path: Path) -> None:
    """A non-imported cookie in the same DB must NOT be revoked by the
    'revoke all' path."""
    dest = tmp_path / "session-profile"
    records = [ck.CookieRecord(host=".x.com", name="imported_one", value="v",
                               source_browser="firefox", source_profile="default")]
    ck.write_to_session_profile(dest, records)

    # Inject a cookie that was NOT imported by carbonyl-agent.
    cookies_db = dest / "Default" / "Network" / "Cookies"
    conn = sqlite3.connect(cookies_db)
    conn.execute(
        "INSERT INTO cookies (creation_utc, host_key, name, value, encrypted_value, "
        "path, expires_utc, is_secure, is_httponly, last_access_utc) "
        "VALUES (1, '.x.com', 'native_cookie', 'native-val', x'', '/', 1, 0, 0, 1)"
    )
    conn.commit()
    conn.close()

    ck.revoke_imported(dest)  # no domain → revoke all imports

    conn = sqlite3.connect(cookies_db)
    try:
        imported_val = conn.execute(
            "SELECT value FROM cookies WHERE name='imported_one'"
        ).fetchone()[0]
        native_val = conn.execute(
            "SELECT value FROM cookies WHERE name='native_cookie'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert imported_val == "", "imported cookie should be revoked"
    assert native_val == "native-val", "non-imported cookie must be untouched"


# ---------------------------------------------------------------------------
# Audit log — values never written; mode 0600
# ---------------------------------------------------------------------------

def test_audit_log_redacts_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "audit.log"
    monkeypatch.setattr(ck, "_AUDIT_LOG_PATH", log)
    ck.audit_log_append({
        "op": "import", "decision": "approved", "domain": "x.com",
        "value": "this-must-never-appear",
        "encrypted_value": b"this-binary-must-never-appear",
        "cookie_names": ["auth_token", "ct0"],
    })
    contents = log.read_text()
    assert "this-must-never-appear" not in contents
    assert "this-binary-must-never-appear" not in contents
    assert "auth_token" in contents  # names are OK to log
    line = json.loads(contents.strip())
    assert "value" not in line
    assert "encrypted_value" not in line
    assert line["op"] == "import"

    # Mode 0600
    mode = stat.S_IMODE(log.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_audit_log_appends_does_not_truncate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "audit.log"
    monkeypatch.setattr(ck, "_AUDIT_LOG_PATH", log)
    ck.audit_log_append({"op": "import", "domain": "a.com"})
    ck.audit_log_append({"op": "import", "domain": "b.com"})
    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["domain"] == "a.com"
    assert json.loads(lines[1])["domain"] == "b.com"


# ---------------------------------------------------------------------------
# Profile discovery
# ---------------------------------------------------------------------------

def test_discover_profiles_chromium_sorts_by_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_root = tmp_path / "chromium"
    fake_root.mkdir()
    old = fake_root / "Default"
    old.mkdir()
    (old / "Cookies").touch()
    newer = fake_root / "Profile 1"
    newer.mkdir()
    (newer / "Cookies").touch()
    # Force mtimes so order is deterministic regardless of fs precision
    os.utime(old / "Cookies", (1000, 1000))
    os.utime(newer / "Cookies", (2000, 2000))

    monkeypatch.setitem(ck.PROFILE_ROOTS_LINUX, "chromium", fake_root)
    profiles = ck.discover_profiles("chromium")
    assert [p.name for p in profiles] == ["Profile 1", "Default"]


def test_discover_profiles_missing_root_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(ck.PROFILE_ROOTS_LINUX, "chrome", tmp_path / "nope")
    assert ck.discover_profiles("chrome") == []


# ---------------------------------------------------------------------------
# Chromium PKCS#7 padding edge cases
# ---------------------------------------------------------------------------

@_requires_crypto
def test_decrypt_chromium_value_rejects_bad_padding() -> None:
    key = ck._derive_chromium_key(ck._CHROMIUM_LINUX_FALLBACK_PW)
    # Encrypt a plaintext block but corrupt the last byte so padding is invalid.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.CBC(ck._CHROMIUM_LINUX_IV))
    enc = cipher.encryptor()
    pt = b"X" * 16  # 16-byte aligned with NO padding — invalid PKCS#7 on decrypt
    ct = enc.update(pt) + enc.finalize()
    blob = b"v10" + ct
    with pytest.raises(ValueError):
        ck.decrypt_chromium_value(blob, key)


@_requires_crypto
def test_decrypt_chromium_value_handles_empty_blob() -> None:
    key = ck._derive_chromium_key(ck._CHROMIUM_LINUX_FALLBACK_PW)
    assert ck.decrypt_chromium_value(b"", key) == ""
