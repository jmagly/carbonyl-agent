"""06-wreq — Verify the wreq transport is in effect.

The W3B Phase-2 wreq transport gives carbonyl-agent's API egress a real
browser-emulating TLS stack (BoringSSL via wreq + wreq-util). When it's
active, JA4 fingerprints on outbound API calls match the persona's
declared browser family. When it's not, EgressClient silently falls back
to httpx with the Python stdlib SSL signature.

This example checks which transport is actually in use by inspecting the
last audit row after a request. A `wreq` value means the native module
is wired in; `httpx-fallback` means it isn't.

Until #88 lands, building the native module is a manual step:
    maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml --features python

After that, `[egress]` users get the wreq transport automatically. Until
then, this example will print `httpx-fallback` on a fresh `pip install`.

Run:
    python examples/06-wreq.py
"""

from __future__ import annotations

import json
from pathlib import Path

from carbonyl_agent import EgressClient, Persona

PERSONA_PATH = Path(__file__).parent / "personas" / "desktop-chrome-linux.toml"


def main() -> None:
    persona = Persona.from_path(PERSONA_PATH)
    client = EgressClient(persona)

    response = client.get("https://httpbin.org/headers")
    print(f"Status: {response.status_code}")

    # Read the last audit row to discover which transport handled the
    # request. The audit log is JSONL; the last line is the request we
    # just made.
    audit_path = client.audit_log.path
    last_line = audit_path.read_text().rstrip().splitlines()[-1]
    row = json.loads(last_line)

    transport = row.get("transport", "<missing>")
    print(f"\nTransport: {transport}")
    print(f"JA4 expected: {row.get('ja4_expected')}")
    print(f"JA4 actual  : {row.get('ja4_actual')}")
    print(f"Drift       : {row.get('drift')}")

    if transport == "wreq":
        print("\n✓ wreq native transport active — JA4 matches persona declaration.")
    elif transport == "httpx-fallback":
        print(
            "\n⚠ Falling back to httpx (stdlib SSL). To activate wreq:\n"
            "    maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml --features python"
        )


if __name__ == "__main__":
    main()
