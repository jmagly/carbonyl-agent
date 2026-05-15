"""01-quickstart — The four-line happy path.

Spawn a Carbonyl browser, navigate to a URL, wait for render to settle,
print the rendered terminal text, and shut down. This is the smallest
useful carbonyl-agent program.

Run:
    python examples/01-quickstart.py
"""

from __future__ import annotations

from carbonyl_agent import CarbonylBrowser


def main() -> None:
    browser = CarbonylBrowser()
    try:
        browser.open("https://example.com")
        browser.drain(5)
        print(browser.page_text())
    finally:
        browser.close()


if __name__ == "__main__":
    main()
