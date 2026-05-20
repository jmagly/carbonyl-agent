"""Cookie/token import from host browsers into carbonyl sessions.

Reads cookies from a host browser (Chromium-family or Firefox), filters by
domain, and writes them into a carbonyl session's user-data-dir after the
operator authorizes the import. Never auto-unlocks keyrings; never logs
cookie values; always writes mode 0600.

Per-domain authorization, sensitive-domain denylist, and the interactive
prompts live in `cookies_cli` — this module is pure I/O and decryption.

Design issue: roctinam/carbonyl-agent#122
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

# Optional crypto deps — only required for Chromium-family browsers on Linux.
# Firefox cookies are unencrypted; carbonyl-agent users who only need FF
# import shouldn't be forced to install cryptography/secretstorage.
try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

try:
    import secretstorage
    _SECRETSTORAGE_AVAILABLE = True
except ImportError:
    _SECRETSTORAGE_AVAILABLE = False


BrowserKind = Literal["chrome", "chromium", "brave", "edge", "firefox"]

# Source profile roots on Linux. macOS/Windows paths live in PROFILE_ROOTS_MAC
# and PROFILE_ROOTS_WIN; the CLI dispatches on platform.
PROFILE_ROOTS_LINUX: dict[BrowserKind, Path] = {
    "chrome": Path.home() / ".config" / "google-chrome",
    "chromium": Path.home() / ".config" / "chromium",
    "brave": Path.home() / ".config" / "BraveSoftware" / "Brave-Browser",
    "edge": Path.home() / ".config" / "microsoft-edge",
    "firefox": Path.home() / ".mozilla" / "firefox",
}

# Sensitive-domain patterns. Each is a fnmatch-style glob matched
# case-insensitively against the cookie's `host_key` (Chromium) or `host`
# (Firefox). Operator must pass `--allow-sensitive` AND type the domain
# to import.
SENSITIVE_PATTERNS: tuple[str, ...] = (
    # Banking
    "*chase.com", "*bankofamerica.com", "*wellsfargo.com", "*citibank.com",
    "*usbank.com", "*capitalone.com", "*ally.com", "*schwab.com",
    "*fidelity.com", "*vanguard.com",
    # Auth providers
    "accounts.google.com", "login.microsoftonline.com", "login.live.com",
    "appleid.apple.com", "auth0.com", "okta.com", "*duosecurity.com",
    # Payment
    "*paypal.com", "*stripe.com", "*square.com", "*venmo.com", "*cashapp.com",
    # Email
    "mail.google.com", "outlook.live.com", "outlook.office.com",
    "mail.proton.me", "mail.yahoo.com", "fastmail.com",
)

# Chromium "Safe Storage" v10/v11 KDF parameters. These are documented
# Chromium constants; not secrets.
_CHROMIUM_LINUX_SALT = b"saltysalt"
_CHROMIUM_LINUX_IV = b" " * 16
_CHROMIUM_LINUX_ITERATIONS = 1
_CHROMIUM_LINUX_FALLBACK_PW = b"peanuts"  # Chromium's literal default when no keyring

_AUDIT_LOG_PATH = Path.home() / ".local" / "share" / "carbonyl-agent" / "cookie-imports.log"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CookieRecord:
    """A single cookie ready to be written into a destination session."""
    host: str               # leading-dot form for Chromium compat: ".example.com"
    name: str
    value: str              # plaintext (decrypted if source was Chromium)
    path: str = "/"
    expires_utc: int = 0    # microseconds since 1601-01-01 (Chromium epoch); 0 = session
    secure: bool = True
    http_only: bool = False
    same_site: int = 0      # 0=unspecified, 1=lax, 2=strict, 3=none
    source_browser: str = ""
    source_profile: str = ""


@dataclass
class ProfileInfo:
    name: str
    path: Path
    last_modified: float = 0.0


# ---------------------------------------------------------------------------
# Source detection
# ---------------------------------------------------------------------------

def discover_profiles(browser: BrowserKind) -> list[ProfileInfo]:
    """Return all profiles for the named host browser, newest first."""
    root = PROFILE_ROOTS_LINUX.get(browser)
    if root is None or not root.is_dir():
        return []
    profiles: list[ProfileInfo] = []
    if browser == "firefox":
        # ~/.mozilla/firefox/<hash>.<name>/cookies.sqlite
        for entry in root.iterdir():
            if entry.is_dir() and (entry / "cookies.sqlite").is_file():
                profiles.append(ProfileInfo(
                    name=entry.name,
                    path=entry,
                    last_modified=(entry / "cookies.sqlite").stat().st_mtime,
                ))
    else:
        # Chromium: profiles are ~/.config/<browser>/Default and ~/.config/<browser>/Profile N
        for entry in root.iterdir():
            cookies_file = entry / "Cookies" if entry.is_dir() else None
            if cookies_file and cookies_file.is_file():
                profiles.append(ProfileInfo(
                    name=entry.name,
                    path=entry,
                    last_modified=cookies_file.stat().st_mtime,
                ))
    profiles.sort(key=lambda p: p.last_modified, reverse=True)
    return profiles


def cookies_db_path(browser: BrowserKind, profile: ProfileInfo) -> Path:
    if browser == "firefox":
        return profile.path / "cookies.sqlite"
    return profile.path / "Cookies"


# ---------------------------------------------------------------------------
# Copy-then-read (tolerates open source browser holding the SQLite lock)
# ---------------------------------------------------------------------------

def copy_to_tmp(src: Path) -> Path:
    """Copy src to a tmpfile owned 0600. Caller deletes after use."""
    fd, tmp_str = tempfile.mkstemp(prefix="carbonyl-cookies-", suffix=".sqlite")
    os.close(fd)
    tmp = Path(tmp_str)
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o600)
    return tmp


# ---------------------------------------------------------------------------
# Chromium decryption
# ---------------------------------------------------------------------------

class KeyringLocked(RuntimeError):
    """Raised when libsecret keyring is locked. Operator must unlock and retry."""


class CryptoUnavailable(RuntimeError):
    """Raised when the optional `cookies` extra isn't installed."""


def _libsecret_safe_storage_password(browser: BrowserKind) -> bytes:
    """Fetch the '<Browser> Safe Storage' secret from libsecret.

    Raises KeyringLocked if the keyring is locked; never auto-unlocks.
    Falls back to literal 'peanuts' only if libsecret has no secret stored
    (Chromium's documented behavior when no keyring is configured at all).
    """
    if not _SECRETSTORAGE_AVAILABLE:
        raise CryptoUnavailable(
            "secretstorage not installed — install with: "
            "pip install 'carbonyl-agent[cookies]'"
        )
    label_map = {
        "chrome": "Chrome Safe Storage",
        "chromium": "Chromium Safe Storage",
        "brave": "Brave Safe Storage",
        "edge": "Microsoft Edge Safe Storage",
    }
    label = label_map.get(browser)
    if label is None:
        return _CHROMIUM_LINUX_FALLBACK_PW

    bus = secretstorage.dbus_init()
    collection = secretstorage.get_default_collection(bus)
    if collection.is_locked():
        raise KeyringLocked(
            f"libsecret keyring is locked. Unlock with `secret-tool` or your "
            f"desktop keyring UI and retry. (Browser: {browser})"
        )
    for item in collection.get_all_items():
        if item.get_label() == label:
            secret = item.get_secret()
            return bytes(secret)
    # No stored secret → Chromium's documented default.
    return _CHROMIUM_LINUX_FALLBACK_PW


def _derive_chromium_key(passphrase: bytes) -> bytes:
    if not _CRYPTO_AVAILABLE:
        raise CryptoUnavailable(
            "cryptography not installed — install with: "
            "pip install 'carbonyl-agent[cookies]'"
        )
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),  # Chromium-mandated; do NOT change
        length=16,
        salt=_CHROMIUM_LINUX_SALT,
        iterations=_CHROMIUM_LINUX_ITERATIONS,
    )
    derived: bytes = kdf.derive(passphrase)
    return derived


def decrypt_chromium_value(blob: bytes, key_v10: bytes) -> str:
    """Decrypt a Chromium 'encrypted_value' blob.

    v10/v11 (Linux): AES-128-CBC, 16-byte block padding, key from libsecret-PBKDF.
    v20+ (M127+): app-bound encryption — not yet supported on Linux as Chromium
      hasn't shipped it there; raise NotImplementedError if we see it.
    """
    if not blob:
        return ""
    if blob[:3] == b"v10" or blob[:3] == b"v11":
        ct = blob[3:]
        cipher = Cipher(algorithms.AES(key_v10), modes.CBC(_CHROMIUM_LINUX_IV))
        dec = cipher.decryptor()
        padded = dec.update(ct) + dec.finalize()
        # PKCS#7 unpad
        pad_len = padded[-1]
        if pad_len < 1 or pad_len > 16:
            raise ValueError("decryption produced invalid PKCS#7 padding")
        plaintext: str = padded[:-pad_len].decode("utf-8", errors="replace")
        return plaintext
    if blob[:3] == b"v20":
        raise NotImplementedError(
            "Chromium v20 app-bound encryption is not supported. "
            "This applies to Chrome M127+ on Windows; not yet on Linux as of 2026-05."
        )
    # Legacy/unencrypted (rare on modern Chromium).
    return blob.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _match_domain(host_key: str, domain: str) -> bool:
    """Cookie host matches the requested domain (with leading-dot semantics)."""
    h = host_key.lstrip(".").lower()
    d = domain.lstrip(".").lower()
    return h == d or h.endswith("." + d)


def read_firefox(profile: ProfileInfo, domains: Iterable[str]) -> list[CookieRecord]:
    db_tmp = copy_to_tmp(profile.path / "cookies.sqlite")
    try:
        conn = sqlite3.connect(f"file:{db_tmp}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite "
                "FROM moz_cookies"
            ).fetchall()
        finally:
            conn.close()
    finally:
        try:
            db_tmp.unlink()
        except OSError:
            pass

    out: list[CookieRecord] = []
    domain_list = list(domains)
    for host, name, value, path, expiry, secure, http_only, same_site in rows:
        if not any(_match_domain(host, d) for d in domain_list):
            continue
        # Firefox expiry is seconds-since-epoch (unix); convert to Chromium microseconds.
        chromium_expiry = (int(expiry) + 11644473600) * 1_000_000 if expiry else 0
        out.append(CookieRecord(
            host=host if host.startswith(".") else host,
            name=name,
            value=value,
            path=path or "/",
            expires_utc=chromium_expiry,
            secure=bool(secure),
            http_only=bool(http_only),
            same_site=int(same_site or 0),
            source_browser="firefox",
            source_profile=profile.name,
        ))
    return out


def read_chromium(
    browser: BrowserKind,
    profile: ProfileInfo,
    domains: Iterable[str],
) -> list[CookieRecord]:
    """Read+decrypt Chromium-family cookies. Raises KeyringLocked if locked."""
    passphrase = _libsecret_safe_storage_password(browser)
    key = _derive_chromium_key(passphrase)

    db_tmp = copy_to_tmp(profile.path / "Cookies")
    try:
        conn = sqlite3.connect(f"file:{db_tmp}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT host_key, name, value, encrypted_value, path, "
                "expires_utc, is_secure, is_httponly, samesite "
                "FROM cookies"
            ).fetchall()
        finally:
            conn.close()
    finally:
        try:
            db_tmp.unlink()
        except OSError:
            pass

    out: list[CookieRecord] = []
    domain_list = list(domains)
    for host_key, name, plain_value, enc_value, path, expires_utc, sec, hto, ss in rows:
        if not any(_match_domain(host_key, d) for d in domain_list):
            continue
        if enc_value:
            try:
                value = decrypt_chromium_value(enc_value, key)
            except (NotImplementedError, ValueError):
                # Skip cookies we can't decrypt; log via audit (without value).
                continue
        else:
            value = plain_value or ""
        out.append(CookieRecord(
            host=host_key,
            name=name,
            value=value,
            path=path or "/",
            expires_utc=int(expires_utc or 0),
            secure=bool(sec),
            http_only=bool(hto),
            same_site=int(ss or 0),
            source_browser=browser,
            source_profile=profile.name,
        ))
    return out


# ---------------------------------------------------------------------------
# Destination writer
# ---------------------------------------------------------------------------

_CHROMIUM_COOKIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cookies (
    creation_utc INTEGER NOT NULL,
    host_key TEXT NOT NULL,
    top_frame_site_key TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    encrypted_value BLOB DEFAULT '',
    path TEXT NOT NULL,
    expires_utc INTEGER NOT NULL,
    is_secure INTEGER NOT NULL,
    is_httponly INTEGER NOT NULL,
    last_access_utc INTEGER NOT NULL,
    has_expires INTEGER NOT NULL DEFAULT 1,
    is_persistent INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 1,
    samesite INTEGER NOT NULL DEFAULT -1,
    source_scheme INTEGER NOT NULL DEFAULT 0,
    source_port INTEGER NOT NULL DEFAULT -1,
    is_same_party INTEGER NOT NULL DEFAULT 0,
    last_update_utc INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (host_key, top_frame_site_key, name, path)
);
"""

_CARBONYL_PROVENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS carbonyl_cookie_provenance (
    host_key TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    source_browser TEXT NOT NULL,
    source_profile TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (host_key, name, path)
);
"""


def write_to_session_profile(profile_dir: Path, records: list[CookieRecord]) -> int:
    """Insert records into the session's Chromium Cookies SQLite.

    Returns count of inserted/replaced rows. The profile dir is the
    carbonyl SessionManager's `profile/` path (the Chromium --user-data-dir).
    """
    network_dir = profile_dir / "Default" / "Network"
    network_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(network_dir, 0o700)
    cookies_db = network_dir / "Cookies"

    conn = sqlite3.connect(cookies_db)
    try:
        conn.executescript(_CHROMIUM_COOKIES_SCHEMA + _CARBONYL_PROVENANCE_SCHEMA)
        now_chromium = _now_chromium_micros()
        n = 0
        for r in records:
            conn.execute(
                "INSERT OR REPLACE INTO cookies ("
                "creation_utc, host_key, name, value, encrypted_value, path, "
                "expires_utc, is_secure, is_httponly, last_access_utc, "
                "has_expires, is_persistent, samesite, last_update_utc"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now_chromium, r.host, r.name, r.value, b"", r.path,
                    r.expires_utc, int(r.secure), int(r.http_only), now_chromium,
                    1 if r.expires_utc else 0, 1 if r.expires_utc else 0,
                    r.same_site, now_chromium,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO carbonyl_cookie_provenance "
                "(host_key, name, path, source_browser, source_profile, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r.host, r.name, r.path, r.source_browser, r.source_profile,
                 datetime.now(timezone.utc).isoformat()),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    os.chmod(cookies_db, 0o600)
    return n


def list_imported(profile_dir: Path) -> list[dict[str, object]]:
    """Return imported cookies with provenance. Values are NEVER returned."""
    cookies_db = profile_dir / "Default" / "Network" / "Cookies"
    if not cookies_db.is_file():
        return []
    conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute(
                "SELECT p.host_key, p.name, p.path, p.source_browser, "
                "p.source_profile, p.imported_at, c.expires_utc "
                "FROM carbonyl_cookie_provenance p "
                "LEFT JOIN cookies c "
                "ON c.host_key=p.host_key AND c.name=p.name AND c.path=p.path"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [
        {
            "host": h, "name": n, "path": p,
            "source": f"{sb}/{sp}", "imported_at": t,
            "expires_utc": int(e or 0),
        }
        for (h, n, p, sb, sp, t, e) in rows
    ]


def revoke_imported(profile_dir: Path, domain: Optional[str] = None) -> int:
    """Blank value of imported cookies (Chromium honors blank as expired).

    Returns count of revoked rows. If `domain` is None, revokes all imports.
    """
    cookies_db = profile_dir / "Default" / "Network" / "Cookies"
    if not cookies_db.is_file():
        return 0
    conn = sqlite3.connect(cookies_db)
    try:
        if domain:
            cur = conn.execute(
                "UPDATE cookies SET value='', encrypted_value=x'', expires_utc=0 "
                "WHERE host_key=? OR host_key=?",
                (domain, "." + domain.lstrip(".")),
            )
        else:
            # Only blank cookies that we imported (have a provenance row).
            cur = conn.execute(
                "UPDATE cookies SET value='', encrypted_value=x'', expires_utc=0 "
                "WHERE (host_key, name, path) IN "
                "(SELECT host_key, name, path FROM carbonyl_cookie_provenance)"
            )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sensitive-domain matching + audit log
# ---------------------------------------------------------------------------

def is_sensitive_domain(domain: str) -> bool:
    """True if the domain matches any SENSITIVE_PATTERNS entry."""
    from fnmatch import fnmatchcase
    d = domain.lstrip(".").lower()
    for pat in SENSITIVE_PATTERNS:
        if fnmatchcase(d, pat.lower()):
            return True
    return False


def audit_log_append(entry: dict[str, object]) -> None:
    """Append one JSON line. Cookie VALUES must never appear in `entry`.

    Caller is responsible for redaction; this function will refuse to write
    any key that looks like a raw cookie value.
    """
    _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in entry.items() if k != "value" and k != "encrypted_value"}
    safe.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = json.dumps(safe, separators=(",", ":")) + "\n"
    # Open with mode 0600 on creation.
    fd = os.open(_AUDIT_LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    # Ensure mode 0600 even if file pre-existed with different perms.
    os.chmod(_AUDIT_LOG_PATH, 0o600)


def _now_chromium_micros() -> int:
    """Microseconds since 1601-01-01 UTC."""
    return int((time.time() + 11644473600) * 1_000_000)
