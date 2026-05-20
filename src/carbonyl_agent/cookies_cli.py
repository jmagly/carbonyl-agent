"""CLI handlers for `carbonyl-agent cookies` — import, list, revoke.

Per-domain authorization gate, sensitive-domain denylist with
type-the-domain confirmation, and operator prompts live here. The
underlying I/O and decryption are in `cookies.py`.

Design issue: roctinam/carbonyl-agent#122
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from carbonyl_agent import cookies as ck
from carbonyl_agent.session import SessionManager


def _stdin_yes(prompt: str, *, default_no: bool = True) -> bool:
    sys.stderr.write(prompt + (" [y/N]: " if default_no else " [Y/n]: "))
    sys.stderr.flush()
    answer = sys.stdin.readline().strip().lower()
    if not answer:
        return not default_no
    return answer in ("y", "yes")


def _stdin_match(prompt: str, expected: str) -> bool:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip() == expected


def _pick_profile(
    browser: ck.BrowserKind,
    profile_name: Optional[str],
) -> Optional[ck.ProfileInfo]:
    profiles = ck.discover_profiles(browser)
    if not profiles:
        sys.stderr.write(
            f"No {browser} profiles found at {ck.PROFILE_ROOTS_LINUX.get(browser)}\n"
        )
        return None
    if profile_name:
        for p in profiles:
            if p.name == profile_name:
                return p
        sys.stderr.write(
            f"Profile '{profile_name}' not found. Available: "
            f"{', '.join(p.name for p in profiles)}\n"
        )
        return None
    if len(profiles) == 1:
        return profiles[0]
    # Interactive prompt — multiple profiles, none specified.
    sys.stderr.write(f"\nMultiple {browser} profiles found:\n")
    for i, p in enumerate(profiles, 1):
        sys.stderr.write(f"  [{i}] {p.name}\n")
    sys.stderr.write(f"Select profile [1-{len(profiles)}]: ")
    sys.stderr.flush()
    try:
        idx = int(sys.stdin.readline().strip()) - 1
        if 0 <= idx < len(profiles):
            return profiles[idx]
    except ValueError:
        pass
    sys.stderr.write("Invalid selection.\n")
    return None


def _authorization_prompt(
    domain: str,
    records: list[ck.CookieRecord],
    *,
    source_browser: str,
    source_profile: str,
    destination: str,
    sensitive: bool,
    allow_sensitive: bool,
) -> bool:
    """Render the per-domain authorization gate. Returns True if approved.

    For sensitive domains, requires --allow-sensitive AND typing the domain.
    """
    if not records:
        sys.stderr.write(f"\nNo cookies found for {domain} in {source_browser}.\n")
        return False

    cookie_names = [r.name for r in records]
    latest_expiry = max((r.expires_utc for r in records), default=0)
    # Convert Chromium micros back to a human date for display.
    if latest_expiry:
        latest_secs = (latest_expiry / 1_000_000) - 11644473600
        from datetime import datetime
        latest_str = datetime.utcfromtimestamp(latest_secs).strftime("%Y-%m-%d")
    else:
        latest_str = "session-only"

    banner = "═" * 66
    sys.stderr.write(f"\n╔{banner}╗\n")
    if sensitive:
        sys.stderr.write("║ ⚠  SENSITIVE DOMAIN — AUTHORIZATION REQUIRED" + " " * 19 + "║\n")
    else:
        sys.stderr.write("║ COOKIE IMPORT — AUTHORIZATION REQUIRED" + " " * 26 + "║\n")
    sys.stderr.write(f"╠{banner}╣\n")
    sys.stderr.write(f"║ Source:  {source_browser} ({source_profile})\n")
    sys.stderr.write(f"║ Domain:  {domain}\n")
    sys.stderr.write(f"║ Cookies: {len(records)} ({', '.join(cookie_names[:5])}"
                     f"{'…' if len(cookie_names) > 5 else ''})\n")
    sys.stderr.write(f"║ Latest expiry: {latest_str}\n")
    sys.stderr.write(f"║ Destination: {destination}\n")
    if sensitive:
        sys.stderr.write("║\n")
        sys.stderr.write("║ RISK: This domain is on the sensitive denylist.\n")
        sys.stderr.write(f"║ Importing these cookies grants full account access for {domain}.\n")
    sys.stderr.write(f"╚{banner}╝\n")

    if sensitive and not allow_sensitive:
        sys.stderr.write(
            f"\n✗ Refused: {domain} is sensitive. Pass --allow-sensitive to override.\n"
        )
        return False
    if sensitive:
        return _stdin_match(
            f"Type the domain '{domain}' to confirm import: ",
            domain,
        )
    return _stdin_yes("Import these cookies?", default_no=True)


def cmd_import(args: argparse.Namespace) -> int:
    if args.source not in ("chrome", "chromium", "brave", "edge", "firefox"):
        sys.stderr.write(f"Unsupported --from browser: {args.source}\n")
        return 2

    profile = _pick_profile(args.source, args.profile)
    if profile is None:
        return 1

    domains = [d.strip() for d in args.domain.split(",") if d.strip()]
    if not domains:
        sys.stderr.write("--domain is required (comma-separated allowed)\n")
        return 2

    # Determine destination session.
    sm = SessionManager()
    if args.persist_to_session:
        if not sm.exists(args.persist_to_session):
            sm.create(args.persist_to_session, tags=["cookie-import"])
        dest_profile = sm.profile_dir(args.persist_to_session)
        dest_label = f"session '{args.persist_to_session}'"
    else:
        # Ephemeral: create a tagged session with timestamp.
        from datetime import datetime
        ephemeral = f"cookie-import-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        sm.create(ephemeral, tags=["cookie-import", "ephemeral"])
        dest_profile = sm.profile_dir(ephemeral)
        dest_label = f"ephemeral session '{ephemeral}'"

    # Per-domain authorization loop.
    total = 0
    for domain in domains:
        sensitive = ck.is_sensitive_domain(domain)
        try:
            if args.source == "firefox":
                records = ck.read_firefox(profile, [domain])
            else:
                records = ck.read_chromium(args.source, profile, [domain])
        except ck.KeyringLocked as e:
            sys.stderr.write(f"\n✗ {e}\n")
            return 3
        except ck.CryptoUnavailable as e:
            sys.stderr.write(f"\n✗ {e}\n")
            return 4

        approved = _authorization_prompt(
            domain, records,
            source_browser=args.source,
            source_profile=profile.name,
            destination=dest_label,
            sensitive=sensitive,
            allow_sensitive=bool(args.allow_sensitive),
        )
        if not approved:
            ck.audit_log_append({
                "op": "import", "decision": "refused", "domain": domain,
                "source_browser": args.source, "source_profile": profile.name,
                "destination": dest_label,
                "sensitive": sensitive,
                "cookie_count": len(records),
                "cookie_names": [r.name for r in records],
            })
            continue

        n = ck.write_to_session_profile(dest_profile, records)
        total += n
        ck.audit_log_append({
            "op": "import", "decision": "approved", "domain": domain,
            "source_browser": args.source, "source_profile": profile.name,
            "destination": dest_label,
            "sensitive": sensitive,
            "cookie_count": n,
            "cookie_names": [r.name for r in records],
        })
        sys.stderr.write(f"✓ Imported {n} cookies for {domain}\n")

    sys.stderr.write(f"\nDone. {total} cookies imported into {dest_label}.\n")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    sm = SessionManager()
    if args.session:
        sessions = [args.session]
    else:
        sessions = [s["name"] for s in sm.list(include_snapshots=False)]
    any_found = False
    for name in sessions:
        if not sm.exists(name):
            continue
        profile_dir = sm.profile_dir(name)
        imports = ck.list_imported(profile_dir)
        if not imports:
            continue
        any_found = True
        sys.stdout.write(f"\nSession: {name}\n")
        for row in imports:
            sys.stdout.write(
                f"  {row['host']:<30} {row['name']:<20} "
                f"from {row['source']:<20} at {row['imported_at']}\n"
            )
    if not any_found:
        sys.stderr.write("No imported cookies found.\n")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    sm = SessionManager()
    if not sm.exists(args.session):
        sys.stderr.write(f"Session '{args.session}' not found.\n")
        return 1
    profile_dir = sm.profile_dir(args.session)
    n = ck.revoke_imported(profile_dir, domain=args.domain)
    ck.audit_log_append({
        "op": "revoke", "decision": "approved",
        "session": args.session,
        "domain": args.domain or "(all imports)",
        "revoked_count": n,
    })
    sys.stderr.write(f"✓ Revoked {n} cookies in session '{args.session}'.\n")
    return 0


def register_subparser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    p = subparsers.add_parser(
        "cookies",
        help="Import session cookies from a host browser (per-domain authorization)",
    )
    sub = p.add_subparsers(dest="cookies_command", required=True)

    p_imp = sub.add_parser("import", help="Import cookies for a domain")
    p_imp.add_argument("--from", dest="source", required=True,
                       choices=["chrome", "chromium", "brave", "edge", "firefox"],
                       help="Source host browser")
    p_imp.add_argument("--profile", help="Source browser profile name (interactive if omitted)")
    p_imp.add_argument("--domain", required=True,
                       help="Comma-separated list of domains to import")
    p_imp.add_argument("--persist-to-session", dest="persist_to_session",
                       help="Persist to named session (default: ephemeral)")
    p_imp.add_argument("--allow-sensitive", action="store_true",
                       help="Allow import from sensitive domains (still requires typing the domain)")
    p_imp.set_defaults(func=cmd_import)

    p_lst = sub.add_parser("list", help="List imported cookies with source provenance")
    p_lst.add_argument("--session", help="Limit to one session (default: all)")
    p_lst.set_defaults(func=cmd_list)

    p_rev = sub.add_parser("revoke", help="Blank imported cookies in a session")
    p_rev.add_argument("--session", required=True)
    p_rev.add_argument("--domain", help="Only revoke this domain (default: all imports)")
    p_rev.set_defaults(func=cmd_revoke)


def dispatch(args: argparse.Namespace) -> int:
    result: int = args.func(args)
    return result
