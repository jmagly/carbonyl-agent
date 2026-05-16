# carbonyl-agent examples

Runnable scripts demonstrating each major capability. Every example assumes:

```bash
pip install -e ".[dev,egress]"
carbonyl-agent install
```

The `[egress]` extra is only required for examples 05 and 06. Example 06
additionally requires the Rust `carbonyl_wreq` native module (until #88 lands,
build manually with `maturin develop --manifest-path crates/carbonyl-wreq/Cargo.toml --features python`).

## Index

| Example | What it shows | Required extras |
|---------|---------------|-----------------|
| [01-quickstart.py](01-quickstart.py) | The four-line happy path: open, drain, read, close | none |
| [02-session.py](02-session.py) | Named sessions persisting cookies across runs | none |
| [03-daemon.py](03-daemon.py) | Daemon mode for warm-browser reuse across short scripts | none |
| [04-persona.py](04-persona.py) | Persona-bound browser with a typed `Persona` object | none |
| [05-egress.py](05-egress.py) | `browser.egress()` — persona-matched API calls alongside the browser session | `[egress]` |
| [06-wreq.py](06-wreq.py) | Verifying the wreq transport is in effect (vs httpx-fallback) | `[egress]` + `carbonyl_wreq` |

## Persona fixture

[`personas/desktop-chrome-linux.toml`](personas/desktop-chrome-linux.toml) is a
valid Chrome-148-on-Linux persona used by examples 04, 05, and 06. It's the
same fixture exercised by the validator's tests, so it's guaranteed to pass
schema validation.

## Running

```bash
python examples/01-quickstart.py
python examples/02-session.py
# ...
```

Each script is self-contained and exits cleanly. Press Ctrl-C if you need to
interrupt the daemon example (03), which holds the daemon open between calls.
