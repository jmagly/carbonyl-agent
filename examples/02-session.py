"""02-session — Named sessions persist cookies across runs.

A `SessionManager` provisions a named user-data-dir under
`~/.local/share/carbonyl/sessions/<name>/`. Passing `session=<name>` to
`CarbonylBrowser` reuses that profile, so cookies, localStorage, and
service-worker state all survive process exit.

Run twice — the second run starts on the same login or cookie state the
first run left behind.

Run:
    python examples/02-session.py
    python examples/02-session.py  # second run reuses the persisted state
"""

from __future__ import annotations

from carbonyl_agent import CarbonylBrowser, SessionManager

SESSION_NAME = "example-quickstart-session"


def main() -> None:
    sessions = SessionManager()

    if not sessions.exists(SESSION_NAME):
        sessions.create(SESSION_NAME, tags=["example"])
        print(f"Created new session: {SESSION_NAME}")
    else:
        print(f"Reusing session: {SESSION_NAME}")

    browser = CarbonylBrowser(session=SESSION_NAME)
    try:
        browser.open("https://httpbin.org/cookies/set?example_visit=1")
        browser.drain(3)
        browser.navigate("https://httpbin.org/cookies")
        browser.drain(3)
        print(browser.page_text())
    finally:
        browser.close()

    info = sessions.get(SESSION_NAME)
    print(f"\nSession persisted at: {info.profile_dir}")
    print("Re-run this script — the cookie set above will round-trip.")


if __name__ == "__main__":
    main()
