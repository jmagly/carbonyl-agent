# ADR-001: Drive Carbonyl via pexpect PTY + pyte Terminal Emulation

**Status**: Accepted
**Date**: 2026-04-09
**Version**: 1.0 (Baselined)
**Deciders**: Joseph Magly (sole maintainer)

---

## Context

Carbonyl is a Chromium fork that renders its output as ANSI escape sequences and Unicode block glyphs into a terminal (stdout), reading keyboard and SGR-mouse input from stdin. Unlike Puppeteer, Playwright, or Selenium, Carbonyl exposes **no DevTools Protocol, no WebDriver endpoint, and no native automation IPC**. The only documented interface is "run it in a terminal."

The `carbonyl-agent` SDK must drive this binary programmatically from Python to:

- Open URLs and wait for page load
- Read the rendered page as text (filtering out graphical block characters)
- Send keyboard input and SGR mouse events (click, move)
- Coexist with Chromium's PTY expectations (size, signals, control chars)

Candidate approaches considered:

1. **pexpect PTY + pyte terminal emulation** — Spawn Carbonyl inside a pseudo-terminal, feed its stdout bytes to an in-memory pyte screen, read the resulting buffer.
2. **Patch Carbonyl to expose a JSON-RPC socket** — Add an automation port upstream.
3. **Use the Carbonyl Rust FFI (`libcarbonyl.so`) directly via `ctypes`/`cffi`** — Bypass the terminal layer entirely.
4. **Screen-scrape via `script`/`tmux` + file tailing** — Record stdout to a file and parse.

## Decision

Use **pexpect (>=4.9) for PTY spawn and I/O**, and **pyte (>=0.8) for ANSI/VT100 terminal emulation**. The `CarbonylBrowser` class (`src/carbonyl_agent/browser.py` lines 153–551) owns a `pexpect.spawn` child and a `pyte.Screen` + `pyte.ByteStream` pair. Bytes are read via `read_nonblocking` in `drain(seconds)` and fed directly into the pyte stream, which maintains an authoritative in-memory screen buffer the SDK can query for text, coordinates, and cell content.

Keyboard input is written as UTF-8 bytes to the PTY (`_child.send`). Mouse events are encoded as SGR escape sequences (`\x1b[<0;{col};{row}M` press / `m` release, browser.py lines 308–311) — the same protocol Carbonyl expects from a real terminal.

## Consequences

### Positive

- **Zero upstream modifications**: The Carbonyl binary is consumed as shipped. SDK releases are decoupled from Chromium release cadence.
- **Minimal dependency footprint**: pexpect and pyte are both small, pure-Python, well-maintained libraries.
- **Works with the Docker fallback**: The same PTY-based driver works whether Carbonyl runs as a local binary or inside `docker run fathyb/carbonyl` (browser.py lines 227–248).
- **Full mouse + keyboard fidelity**: SGR mouse protocol lets us drive mousemove sequences (browser.py lines 272–301) that are indistinguishable from real terminal input — important for bot-detection evasion on sites like Akamai-protected targets.

### Negative

- **Tight coupling to Carbonyl's rendering output**: Any change in how Carbonyl draws the navbar, emits Unicode blocks, or positions UI chrome can break text extraction and click targeting. `extract_text` (browser.py lines 128–150) explicitly filters block/geometric characters and will drift if Carbonyl changes its glyph set.
- **Text extraction is heuristic**: `_is_text_char` uses Unicode category heuristics. There is no ground-truth DOM, so "page text" is best-effort, not authoritative.
- **Coordinate-based clicking**: All interaction is by terminal cell `(col, row)`, not by DOM selector. `click_text` (browser.py lines 313–335) papers over this but still depends on the text being visually present on the current screen.
- **No event-level access**: We cannot subscribe to DOM events, network requests, or console messages. Tests that need those capabilities cannot be built on this SDK.

### Neutral

- Dimensions are fixed at 220 × 50 (browser.py lines 32–33). Larger screens mean more page content visible per drain but also larger buffers.
- pyte handles enough of VT100 to render Chromium's output; the `ByteStream` path avoids unicode decode errors on partial reads.

## Alternatives Considered

- **Patch Carbonyl to add a JSON-RPC port** (Option 2) — rejected: would fork upstream, create maintenance burden, and tie SDK releases to Chromium rebuilds.
- **Direct FFI via `libcarbonyl.so`** (Option 3) — rejected: the Rust FFI layer owned by `roctinam/carbonyl` is a rendering target, not an automation API. Exposing enough surface to drive the browser would require a larger upstream change than Option 2.
- **`script` + file tailing** (Option 4) — rejected: introduces an extra process, slows I/O, makes coordinate-accurate mouse input awkward, and provides nothing pexpect doesn't already give us.

## References

- `src/carbonyl_agent/browser.py` lines 153–551 (`CarbonylBrowser`)
- `src/carbonyl_agent/browser.py` lines 128–150 (`extract_text`, block-char filtering)
- `pexpect` documentation: https://pexpect.readthedocs.io
- `pyte` documentation: https://pyte.readthedocs.io
- ADR-003 (runtime binary discovery order)
