#!/usr/bin/env python3
"""
carbonyl-agent install — Download and install the Carbonyl runtime binary.

The runtime is hosted on the Gitea releases for roctinam/carbonyl, tagged
as `runtime-<hash>` where the hash encodes the Chromium version + patches.

Usage:
    carbonyl-agent install [--tag runtime-<hash>] [--dest ~/.local/share/carbonyl/bin]
    carbonyl-agent status
    carbonyl-agent --help
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

GITEA_BASE = os.environ.get("GITEA_BASE", "https://git.integrolabs.net")
GITEA_REPO = "roctinam/carbonyl"

# Default install directory (same location _local_binary() checks)
DEFAULT_DEST = Path.home() / ".local" / "share" / "carbonyl" / "bin"

# Latest known runtime tag — update when a new runtime is pushed
LATEST_TAG = "runtime-latest"


def _platform_triple() -> str:
    machine = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip().lower()
    if system == "darwin":
        vendor, os_name = "apple", "darwin"
    else:
        vendor, os_name = "unknown", "linux-gnu"
    return f"{machine}-{vendor}-{os_name}"


def _resolve_tag(tag: str) -> str:
    """Resolve 'runtime-latest' to the actual latest release tag."""
    if tag != "runtime-latest":
        return tag
    url = f"{GITEA_BASE}/api/v1/repos/{GITEA_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            import json
            data = json.loads(resp.read())
            result: str = data["tag_name"]
            return result
    except Exception as exc:
        print(f"Warning: could not resolve latest tag: {exc}", file=sys.stderr)
        return tag


def _sha256_file(path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _fetch_sha256sums(tag: str, triple: str) -> str | None:
    """Download SHA256SUMS from a release and return the expected hex digest
    for ``{triple}.tgz``, or *None* if the file is missing (404)."""
    url = f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/SHA256SUMS"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text: str = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    filename = f"{triple}.tgz"
    for line in text.splitlines():
        # Format: "<hex>  <filename>"
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip() == filename:
            return parts[0].lower()
    return None


def _verify_checksum(
    tarball: Path,
    tag: str,
    triple: str,
    *,
    pinned_checksum: str | None = None,
    skip_verify: bool = False,
) -> None:
    """Verify the SHA-256 checksum of *tarball*.

    Raises ``SystemExit`` on mismatch.  Prints a warning (but continues) when
    the remote ``SHA256SUMS`` file is missing and no *pinned_checksum* was
    supplied.
    """
    if skip_verify:
        print("Warning: checksum verification skipped (--no-verify)", file=sys.stderr)
        return

    expected: str | None
    if pinned_checksum:
        expected = pinned_checksum.lower()
        source = "--checksum flag"
    else:
        expected = _fetch_sha256sums(tag, triple)
        source = "SHA256SUMS"
        if expected is None:
            print(
                "Warning: SHA256SUMS not found for this release; "
                "skipping checksum verification.",
                file=sys.stderr,
            )
            return

    actual = _sha256_file(tarball)
    if actual != expected:
        print(
            f"\nERROR: SHA-256 mismatch!\n"
            f"  Expected ({source}): {expected}\n"
            f"  Got:                 {actual}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Checksum OK ({source})")


def cmd_install(args: argparse.Namespace) -> int:
    triple = _platform_triple()
    tag = _resolve_tag(args.tag)
    dest = Path(args.dest)

    url = f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/{triple}.tgz"
    install_dir = dest / triple
    binary = install_dir / "carbonyl"

    if binary.exists() and not args.force:
        print(f"Already installed: {binary}")
        print("Use --force to reinstall.")
        return 0

    print(f"Downloading {url} ...")
    install_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while chunk := resp.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {pct}% ({downloaded // 1024 // 1024} MB)", end="", flush=True)
        print()

        _verify_checksum(
            tmp_path,
            tag,
            triple,
            pinned_checksum=getattr(args, "checksum", None),
            skip_verify=getattr(args, "no_verify", False),
        )

        print(f"Extracting to {install_dir} ...")
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Strip the leading triple/ directory component from the tarball
            for member in tar.getmembers():
                # member.name is like "x86_64-unknown-linux-gnu/carbonyl"
                parts = Path(member.name).parts
                if len(parts) >= 2:
                    member.name = str(Path(*parts[1:]))
                elif len(parts) == 1 and parts[0] != ".":
                    pass  # keep top-level files as-is
                tar.extract(member, install_dir)

        binary.chmod(binary.stat().st_mode | 0o111)
        print(f"Installed: {binary}")

    except urllib.error.HTTPError as exc:
        print(f"\nERROR: {exc.code} {exc.reason} — {url}", file=sys.stderr)
        print("Check that the tag exists and the Gitea server is reachable.", file=sys.stderr)
        return 1
    finally:
        tmp_path.unlink(missing_ok=True)

    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    from carbonyl_agent.browser import _local_binary
    binary = _local_binary()
    if binary:
        print(f"carbonyl binary: {binary}")
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
        if result.stdout:
            print(f"version: {result.stdout.strip()}")
    else:
        print("carbonyl binary: not found")
        print("Run `carbonyl-agent install` to install it.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="carbonyl-agent",
        description="Carbonyl browser automation SDK",
    )
    sub = parser.add_subparsers(dest="command")

    p_install = sub.add_parser("install", help="Download and install the Carbonyl runtime")
    p_install.add_argument(
        "--tag",
        default=LATEST_TAG,
        help="Gitea release tag to download (default: runtime-latest)",
    )
    p_install.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help=f"Install directory (default: {DEFAULT_DEST})",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Reinstall even if binary already exists",
    )
    p_install.add_argument(
        "--checksum",
        default=None,
        metavar="HEX",
        help="Expected SHA-256 hex digest (skips SHA256SUMS download)",
    )
    p_install.add_argument(
        "--no-verify",
        action="store_true",
        default=False,
        help="Skip checksum verification entirely (not recommended)",
    )

    sub.add_parser("status", help="Show carbonyl binary location and version")

    args = parser.parse_args()

    if args.command == "install":
        sys.exit(cmd_install(args))
    elif args.command == "status":
        sys.exit(cmd_status(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
