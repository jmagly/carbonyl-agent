"""03-daemon — Daemon mode keeps a browser warm across short-lived scripts.

Spawning Chromium takes ~1-3 seconds. For agent workflows that fire many
short scripts against the same session, that overhead dominates. Daemon
mode runs the browser in a background process; client scripts attach via
a Unix socket and skip the spawn cost.

This example:
  1. Starts a daemon attached to a named session
  2. Connects two ephemeral clients in sequence to demonstrate the
     daemon survives between them
  3. Stops the daemon cleanly

Run:
    python examples/03-daemon.py
"""

from __future__ import annotations

import time

from carbonyl_agent import (
    DaemonClient,
    SessionManager,
    is_daemon_live,
    start_daemon,
    stop_daemon,
)

SESSION_NAME = "example-daemon-session"


def main() -> None:
    sessions = SessionManager()
    if not sessions.exists(SESSION_NAME):
        sessions.create(SESSION_NAME, tags=["example", "daemon"])

    if not is_daemon_live(SESSION_NAME):
        pid = start_daemon(SESSION_NAME, url="https://example.com")
        print(f"Started daemon (pid={pid})")
    else:
        print("Daemon already live; reusing")

    # Client 1 — read the rendered page
    with DaemonClient(SESSION_NAME) as client:
        client.drain(2)
        text = client.page_text()
        print(f"\n[client 1] page_text length: {len(text)} chars")
        print(text[:200].rstrip())

    # Client 2 — same daemon, no respawn
    print("\nReconnecting (no respawn cost)...")
    t0 = time.perf_counter()
    with DaemonClient(SESSION_NAME) as client:
        client.navigate("https://httpbin.org/headers")
        client.drain(2)
        text = client.page_text()
        print(f"[client 2] connected in {(time.perf_counter() - t0) * 1000:.0f} ms")
        print(text[:200].rstrip())

    stop_daemon(SESSION_NAME)
    print("\nDaemon stopped.")


if __name__ == "__main__":
    main()
