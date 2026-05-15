"""05-egress — Persona-matched API calls alongside the browser session.

When a script makes API calls around its browser session (login flows,
data fetches, ancillary requests), those calls should look like they
came from the same client as the browser. If the browser identifies as
Chrome 147 on Linux but the API calls carry a Python-stdlib SSL
fingerprint, bot-detection systems trivially correlate the two and flag
the session.

`browser.egress()` returns an `EgressClient` bound to the browser's
persona. It:
  - Injects matching headers (UA, Accept-Language, sec-ch-ua-* for Chrome)
  - Shares the cookie jar with the browser profile so set-cookies converge
  - Writes a JSONL audit row per request to ~/.local/state/carbonyl-agent/
    egress-audit.log for drift detection

This example opens a page in the browser, then makes an API call from
the same persona via egress. The audit log shows both the transport
(httpx vs wreq) and any JA4 drift between expected and actual.

Requires the `[egress]` extra: `pip install carbonyl-agent[egress]`

Run:
    python examples/05-egress.py
"""

from __future__ import annotations

from pathlib import Path

from carbonyl_agent import CarbonylBrowser, Persona

PERSONA_PATH = Path(__file__).parent / "personas" / "desktop-chrome-linux.toml"


def main() -> None:
    persona = Persona.from_path(PERSONA_PATH)
    browser = CarbonylBrowser(persona=persona)
    try:
        browser.open("https://httpbin.org/")
        browser.drain(3)

        # Persona-matched API call — uses the same UA + ua-ch headers
        # as the browser, cookie jar co-located.
        with browser.egress() as client:
            response = client.get("https://httpbin.org/headers")
            print(f"Status: {response.status_code}")
            print(f"Transport: {client.audit_log.path}")
            print()
            print(response.text)
    finally:
        browser.close()

    print(
        "\nInspect the audit log for transport + drift detail:\n"
        f"  cat {client.audit_log.path}"
    )


if __name__ == "__main__":
    main()
