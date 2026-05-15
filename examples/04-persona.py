"""04-persona — Persona-bound browser with a typed Persona object.

A Persona is a typed, validated description of a browser's network and
device fingerprint (UA, ua-ch brands, screen, fonts, JA4, h2 SETTINGS).
Passing a Persona to CarbonylBrowser provisions a per-persona Chromium
profile under ~/.config/carbonyl-agent/profiles/ and applies the persona's
Chromium flags so the browser identifies as the persona declares.

This example loads a valid Chrome-147-on-Linux persona from disk, opens
a request inspector, and prints the resulting User-Agent. The browser's
profile persists, so re-running the script reuses the same identity.

Run:
    python examples/04-persona.py
"""

from __future__ import annotations

from pathlib import Path

from carbonyl_agent import CarbonylBrowser, Persona

PERSONA_PATH = Path(__file__).parent / "personas" / "desktop-chrome-linux.toml"


def main() -> None:
    persona = Persona.from_path(PERSONA_PATH)

    print(f"Loaded persona: {persona.id}")
    print(f"  family    : {persona.browser_family} {persona.browser_version}")
    print(f"  UA        : {persona.user_agent_full}")
    print(f"  locale    : {persona.accept_language} / {persona.timezone}")
    print(f"  viewport  : {persona.viewport[0]}x{persona.viewport[1]}\n")

    browser = CarbonylBrowser(persona=persona)
    try:
        browser.open("https://httpbin.org/user-agent")
        browser.drain(5)
        print(browser.page_text())
    finally:
        browser.close()


if __name__ == "__main__":
    main()
