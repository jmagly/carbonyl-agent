#!/usr/bin/env python3
"""
carbonyl-agent install — Download and install the Carbonyl runtime binary.

Hash-pinned runtimes are hosted on the Gitea releases for roctinam/carbonyl,
tagged as `runtime-<hash>` where the hash encodes the Chromium version +
patches. Semantic `v*` runtime tags use public GitHub release assets first,
with the Gitea release as a fallback mirror.

Usage:
    carbonyl-agent install [--tag runtime-<hash>] [--dest ~/.local/share/carbonyl/bin]
    carbonyl-agent install --from-file <path-to-tarball>   # airgap install
    carbonyl-agent install --dry-run                       # preview only
    carbonyl-agent status
    carbonyl-agent --help

# Airgap / offline install (#95)

Hosts without GitHub or Gitea access can still install the runtime in
two steps:

1. On a connected host, run `carbonyl-agent install --dry-run` to get
   the resolved download URL for your platform triple. Download
   `{triple}.tgz` and `SHA256SUMS` from that URL.
2. Carry the tarball to the airgapped host and run:

       carbonyl-agent install --from-file path/to/triple.tgz \\
                              --checksum <hex-from-SHA256SUMS>

   The checksum flag is recommended; without it `--from-file` prints
   a warning and skips integrity verification.

# Proxy support

The download path uses `urllib.request`, which honors the standard
`HTTPS_PROXY` (and lowercase `https_proxy`) environment variables for
HTTPS URLs out of the box. Set `HTTPS_PROXY=http://corp-proxy:3128`
before running install if your network requires it. `--dry-run`
reports the active proxy setting.

# Binary discovery

`CarbonylBrowser` resolves the binary path in this order (see
`browser._local_binary`):

  1. `CARBONYL_BIN` env var (explicit absolute path — highest priority)
  2. `~/.local/share/carbonyl/bin/<triple>/carbonyl` (this installer's
     default install location, or whatever `--dest` was used)
  3. `carbonyl` on `$PATH`
  4. Docker fallback: `docker run ghcr.io/jmagly/carbonyl`

In airgap deployments where the runtime lives in a non-default path,
set `CARBONYL_BIN=/path/to/carbonyl` to skip discovery.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from carbonyl_agent import runtime_pin

GITEA_BASE = os.environ.get("GITEA_BASE", "https://git.integrolabs.net")
GITEA_REPO = "roctinam/carbonyl"
GITHUB_RELEASE_BASE = os.environ.get(
    "CARBONYL_GITHUB_RELEASE_BASE",
    "https://github.com/jmagly/carbonyl",
)

# Default install directory (same location _local_binary() checks)
DEFAULT_DEST = Path.home() / ".local" / "share" / "carbonyl" / "bin"

# Sentinel meaning "resolve to the latest release at install time" (drift-prone).
# Use this only when explicitly opting out of the pin file. See `runtime_pin`.
LATEST_TAG = runtime_pin.LATEST_SENTINEL


@dataclass(frozen=True)
class RuntimeDownload:
    label: str
    url: str
    asset_name: str
    checksum_url: str | None
    require_checksum: bool
    expected_version: str | None


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


def _is_semantic_tag(tag: str) -> bool:
    return tag.startswith("v")


def _semantic_version(tag: str) -> str | None:
    if not _is_semantic_tag(tag):
        return None
    return tag[1:]


def _asset_name_for_tag(tag: str, triple: str) -> str:
    version = _semantic_version(tag)
    if version:
        return f"carbonyl-{version}-{triple}.tgz"
    return f"{triple}.tgz"


def _download_candidates(tag: str, triple: str) -> list[RuntimeDownload]:
    asset_name = _asset_name_for_tag(tag, triple)
    version = _semantic_version(tag)
    if version:
        return [
            RuntimeDownload(
                label="GitHub public release",
                url=f"{GITHUB_RELEASE_BASE}/releases/download/{tag}/{asset_name}",
                asset_name=asset_name,
                checksum_url=f"{GITHUB_RELEASE_BASE}/releases/download/{tag}/{asset_name}.sha256",
                require_checksum=True,
                expected_version=version,
            ),
            RuntimeDownload(
                label="Gitea release mirror",
                url=f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/{asset_name}",
                asset_name=asset_name,
                checksum_url=f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/{asset_name}.sha256",
                require_checksum=True,
                expected_version=version,
            ),
        ]
    return [
        RuntimeDownload(
            label="Gitea runtime release",
            url=f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/{asset_name}",
            asset_name=asset_name,
            checksum_url=f"{GITEA_BASE}/{GITEA_REPO}/releases/download/{tag}/SHA256SUMS",
            require_checksum=False,
            expected_version=None,
        )
    ]


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


def _fetch_checksum_url(checksum_url: str, asset_name: str) -> str | None:
    """Fetch a checksum sidecar or SHA256SUMS file and return the asset digest."""
    req = urllib.request.Request(checksum_url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text: str = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        digest = parts[0].lower()
        if len(parts) == 1:
            return digest
        if parts[1].strip() == asset_name:
            return digest
    return None


def _verify_checksum(
    tarball: Path,
    tag: str,
    triple: str,
    *,
    pinned_checksum: str | None = None,
    skip_verify: bool = False,
    checksum_url: str | None = None,
    asset_name: str | None = None,
    require_remote: bool = False,
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
    elif checksum_url and asset_name:
        expected = _fetch_checksum_url(checksum_url, asset_name)
        source = checksum_url
        if expected is None:
            message = f"SHA-256 checksum not found at {checksum_url}"
            if require_remote:
                print(f"ERROR: {message}", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: {message}; skipping checksum verification.", file=sys.stderr)
            return
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


def _validate_binary_version(binary: Path, expected_version: str | None) -> None:
    if expected_version is None:
        return
    expected = f"Carbonyl {expected_version}"
    result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
    actual = result.stdout.strip()
    if result.returncode != 0:
        print(
            f"ERROR: could not validate Carbonyl runtime version: {binary}",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        sys.exit(1)
    if actual != expected:
        print(
            f"\nERROR: Carbonyl runtime version mismatch!\n"
            f"  Expected: {expected}\n"
            f"  Got:      {actual}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Version OK ({actual})")


def _extract_tarball(tarball: Path, install_dir: Path) -> Path:
    """Extract a Carbonyl runtime tarball to ``install_dir``, stripping
    the leading triple/ directory component. Returns the binary path.

    Refs: roctinam/carbonyl-agent#95 — factored out of cmd_install so
    --from-file can share the extraction code.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        for member in tar.getmembers():
            # member.name is like "x86_64-unknown-linux-gnu/carbonyl"
            parts = Path(member.name).parts
            if len(parts) >= 2:
                member.name = str(Path(*parts[1:]))
            elif len(parts) == 1 and parts[0] != ".":
                pass  # keep top-level files as-is
            tar.extract(member, install_dir)
    binary = install_dir / "carbonyl"
    binary.chmod(binary.stat().st_mode | 0o111)
    return binary


def cmd_install(args: argparse.Namespace) -> int:
    triple = _platform_triple()
    dest = Path(args.dest)
    install_dir = dest / triple
    binary = install_dir / "carbonyl"
    from_file = getattr(args, "from_file", None)
    dry_run = bool(getattr(args, "dry_run", False))

    # --from-file: install from a pre-downloaded tarball, no network. This
    # is the airgap path (#95) — operators on networks without GitHub /
    # Gitea access download {triple}.tgz manually on a connected host,
    # carry it over, and run install --from-file <path>.
    if from_file is not None:
        src = Path(from_file).expanduser()
        if not src.exists():
            print(f"ERROR: --from-file path does not exist: {src}", file=sys.stderr)
            return 1
        if not src.is_file():
            print(f"ERROR: --from-file must be a file: {src}", file=sys.stderr)
            return 1
        if binary.exists() and not args.force:
            print(f"Already installed: {binary}")
            print("Use --force to reinstall.")
            return 0
        if dry_run:
            print(f"[dry-run] Would extract {src} → {install_dir}")
            print(f"[dry-run] Would verify SHA-256 (skip_verify="
                  f"{getattr(args, 'no_verify', False)}, "
                  f"pinned_checksum={getattr(args, 'checksum', None)!r})")
            print(f"[dry-run] Would write binary at: {binary}")
            return 0
        # Checksum: --from-file disables the SHA256SUMS network fetch
        # (no network) but still verifies if --checksum was supplied.
        # Recommend running with --checksum on the airgap host.
        pinned = getattr(args, "checksum", None)
        skip = getattr(args, "no_verify", False)
        if pinned:
            actual = _sha256_file(src)
            if actual.lower() != pinned.lower():
                print(
                    f"\nERROR: SHA-256 mismatch!\n"
                    f"  Expected (--checksum): {pinned.lower()}\n"
                    f"  Got:                  {actual}",
                    file=sys.stderr,
                )
                return 1
            print("Checksum OK (--checksum flag)")
        elif not skip:
            print(
                "Warning: --from-file with no --checksum and no --no-verify; "
                "skipping verification. Pass --checksum <hex> for an integrity check.",
                file=sys.stderr,
            )
        print(f"Extracting {src} → {install_dir} ...")
        binary = _extract_tarball(src, install_dir)
        _validate_binary_version(binary, _semantic_version(args.tag or ""))
        print(f"Installed: {binary}")
        return 0

    if args.tag is None:
        default_tag, source = runtime_pin.resolve_default_tag()
        if source == "tag-pin":
            print(f"Using pinned runtime tag: {default_tag} (from .carbonyl-runtime-version)")
        elif source == "pin":
            print(f"Using pinned runtime: {default_tag} (from .carbonyl-runtime-version)")
        elif source == "env":
            print(f"Using runtime from CARBONYL_RUNTIME_TAG: {default_tag}")
        elif source == "latest-sentinel":
            print(f"Pin file requests {default_tag} — resolving to latest release")
        else:
            print(f"No pin file found; defaulting to {default_tag} (will resolve to latest)")
        tag_input = default_tag
    else:
        tag_input = args.tag
    tag = _resolve_tag(tag_input) if not dry_run else tag_input

    candidates = _download_candidates(tag, triple)

    if binary.exists() and not args.force:
        print(f"Already installed: {binary}")
        print("Use --force to reinstall.")
        return 0

    if dry_run:
        # No network calls in dry-run mode — print resolved URL + paths
        # so operators on an airgap host can copy the URL to a connected
        # machine, download, then install --from-file. The proxy-aware
        # warning helps users who don't realize urllib already honors
        # HTTPS_PROXY but not HTTP_PROXY for https:// URLs.
        print(f"[dry-run] Platform: {triple}")
        print(f"[dry-run] Tag: {tag} (input {tag_input!r}, not resolved against the API)")
        for candidate in candidates:
            print(f"[dry-run] Candidate ({candidate.label}): {candidate.url}")
            if candidate.checksum_url:
                print(f"[dry-run] Checksum: {candidate.checksum_url}")
        print(f"[dry-run] Would extract to: {install_dir}")
        print(f"[dry-run] Final binary path: {binary}")
        expected_version = candidates[0].expected_version
        if expected_version:
            print(f"[dry-run] Would validate: carbonyl --version == Carbonyl {expected_version}")
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            print(f"[dry-run] Network: HTTPS_PROXY={proxy} (urllib honors this for https://)")
        else:
            print("[dry-run] Network: no HTTPS_PROXY set (direct connection)")
        return 0

    install_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        selected: RuntimeDownload | None = None
        errors: list[str] = []
        for candidate in candidates:
            print(f"Downloading {candidate.url} ...")
            req = urllib.request.Request(candidate.url)
            try:
                with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as f:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    while chunk := resp.read(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            print(
                                f"\r  {pct}% ({downloaded // 1024 // 1024} MB)",
                                end="",
                                flush=True,
                            )
                print()
                selected = candidate
                break
            except urllib.error.URLError as exc:
                tmp_path.write_bytes(b"")
                errors.append(f"{candidate.url}: {exc}")
                print(f"Warning: download failed from {candidate.label}: {exc}", file=sys.stderr)

        if selected is None:
            print("\nERROR: could not download a compatible Carbonyl runtime.", file=sys.stderr)
            print("Attempted URLs:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1

        _verify_checksum(
            tmp_path,
            tag,
            triple,
            pinned_checksum=getattr(args, "checksum", None),
            skip_verify=getattr(args, "no_verify", False),
            checksum_url=selected.checksum_url,
            asset_name=selected.asset_name,
            require_remote=selected.require_checksum,
        )

        print(f"Extracting to {install_dir} ...")
        binary = _extract_tarball(tmp_path, install_dir)
        _validate_binary_version(binary, selected.expected_version)
        print(f"Installed: {binary}")

    except urllib.error.HTTPError as exc:
        print(f"\nERROR: {exc.code} {exc.reason}", file=sys.stderr)
        print("Check that the tag exists and a configured release source is reachable.", file=sys.stderr)
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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging (also: CARBONYL_DEBUG=1 / "
             "CARBONYL_LOG_LEVEL=DEBUG)",
    )
    sub = parser.add_subparsers(dest="command")

    p_install = sub.add_parser("install", help="Download and install the Carbonyl runtime")
    # No argparse default — None means "use the pin file" (resolved in cmd_install).
    p_install.add_argument(
        "--tag",
        default=None,
        help="Runtime release tag to download (default: read from "
             ".carbonyl-runtime-version pin file; falls back to runtime-latest "
             "if no pin is present)",
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
    p_install.add_argument(
        "--from-file",
        default=None,
        metavar="PATH",
        help="Install from a pre-downloaded tarball instead of fetching "
             "from the release. Airgap path — see module docstring.",
    )
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Resolve and print what would be done without downloading "
             "or modifying the filesystem. Useful for previewing the "
             "exact URL to fetch on an airgap host.",
    )

    sub.add_parser("status", help="Show carbonyl binary location and version")

    # daemon subcommand group
    p_daemon = sub.add_parser("daemon", help="Manage persistent browser daemons")
    daemon_sub = p_daemon.add_subparsers(dest="daemon_command", required=True)

    p_dstart = daemon_sub.add_parser("start", help="Start a persistent browser daemon")
    p_dstart.add_argument("session", help="Session name")
    p_dstart.add_argument("url", nargs="?", default=None, help="Initial URL (default: about:blank)")
    p_dstart.add_argument(
        "--backend", choices=("pty", "uinput"), default="pty",
        help="Input backend the daemon will run with (default: pty). "
             "uinput requires /dev/uinput access; see ADR-002 rev 2.",
    )

    p_dstop = daemon_sub.add_parser("stop", help="Stop a running daemon")
    p_dstop.add_argument("session")

    daemon_sub.add_parser("status", help="Show daemon status for all sessions")

    p_dattach = daemon_sub.add_parser("attach", help="Interactive REPL for a live daemon")
    p_dattach.add_argument("session")

    from carbonyl_agent import cookies_cli
    cookies_cli.register_subparser(sub)

    args = parser.parse_args()

    if getattr(args, "debug", False):
        from carbonyl_agent._logging import enable_debug_logging
        enable_debug_logging()

    if args.command == "install":
        sys.exit(cmd_install(args))
    elif args.command == "status":
        sys.exit(cmd_status(args))
    elif args.command == "daemon":
        from carbonyl_agent.daemon import _cmd_attach, _cmd_start, _cmd_status, _cmd_stop

        dispatch = {
            "start": _cmd_start,
            "stop": _cmd_stop,
            "status": _cmd_status,
            "attach": _cmd_attach,
        }
        dispatch[args.daemon_command](args)
        sys.exit(0)
    elif args.command == "cookies":
        sys.exit(cookies_cli.dispatch(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
