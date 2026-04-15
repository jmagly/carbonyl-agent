<div align="center">

<pre>
   O    O
    \  /
O —— Cr —— O
    /  \
   O    O
</pre>

# carbonyl-agent

**Python automation SDK for the Carbonyl headless browser**

```bash
pip install carbonyl-agent
carbonyl-agent install
```

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Carbonyl M147](https://img.shields.io/badge/carbonyl-M147-green?style=flat-square)](https://git.integrolabs.net/roctinam/carbonyl)

[**Get Started**](#-quick-start) · [**Session API**](#-session-persistence) · [**Daemon Mode**](#-daemon-mode) · [**Bot Detection**](#-bot-detection-flags) · [**Examples**](examples/)

</div>

---

## What carbonyl-agent Is

`carbonyl-agent` is the Python automation SDK for [Carbonyl](https://git.integrolabs.net/roctinam/carbonyl) — a Chromium-based headless browser that renders into terminal text. The SDK spawns Carbonyl via PTY, parses the screen via `pyte`, and exposes a high-level API for navigation, clicking, text extraction, and session persistence. It is designed for agent-driven web interaction: scripted scraping, automated form submission, and LLM-driven browsing loops that need a real browser but not a real display.

Unlike Playwright or Selenium, carbonyl-agent returns **terminal text**, not a DOM. This makes it fast (no screenshot decode), cheap (no GPU, no window server), and well-suited for the context windows of LLM-driven agents.

---

## Why This Matters

### For Developers

**A real browser, cheap and scriptable.** Most automation stacks require either a full display server (Selenium + Xvfb) or a heavyweight DevTools protocol (Playwright CDP). carbonyl-agent gives you Chromium rendering through a PTY — `pip install`, call `open()`, read `page_text()`. Named sessions persist cookies across runs; daemon mode keeps a browser warm across short-lived scripts.

### For Agents

**Rendered text is the native LLM format.** An LLM consuming `page_text()` gets the page as a human would read it in a terminal — headings, lists, table rows — without DOM noise or screenshot OCR. Built-in bot-detection evasion (Firefox UA, `AutomationControlled` suppression, HTTP/2 off) means agents aren't blocked by default on Akamai/Cloudflare-protected sites.

### For Operators

**Low footprint, no window server.** Runs in a safe-mode console, over SSH, or inside a container without X11/Wayland. Binary discovery is prioritized: env var → local install → PATH → Docker opt-in. Sessions and daemon sockets live under `~/.local/share/carbonyl/` with 0600/0700 permissions.

---

## Core Capabilities

1. **CarbonylBrowser** — spawn Carbonyl via PTY, `open()`, `drain()`, `page_text()`, `click()`, `send_key()`, `find_text()`, `click_text()`, `mouse_path()`
2. **SessionManager** — named persistent profiles, `create` / `fork` / `snapshot` / `restore`, live-session detection
3. **Daemon mode** — long-running Carbonyl exposed over a Unix socket; clients reconnect without losing state
4. **ScreenInspector** — coordinate-grid rendering, region annotation, crosshairs for debugging click targets
5. **Bot-detection evasion** — curated `_HEADLESS_FLAGS` set at spawn (UA spoof, webdriver suppression, HTTP/1.1 fallback)
6. **Verified install** — `carbonyl-agent install` downloads the runtime, verifies SHA256, optional `--checksum` pinning

---

## Quick Start

> **Prerequisites:** Python 3.11+. Linux (x86_64, aarch64) or macOS.

### Install

```bash
pip install carbonyl-agent

# Download the Carbonyl runtime binary (verified via SHA256)
carbonyl-agent install

# Or pin to a known checksum for reproducible installs
carbonyl-agent install --checksum <sha256-hex>
```

### Your first script

```python
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser()
b.open("https://example.com")
b.drain(8.0)
print(b.page_text())
b.close()
```

### Public API

All primary names importable directly from the package root:

```python
from carbonyl_agent import (
    CarbonylBrowser, SessionManager, ScreenInspector,
    DaemonClient, start_daemon, stop_daemon, daemon_status,
)
```

---

## Session Persistence

Named sessions persist cookies, localStorage, and IndexedDB across browser restarts:

```python
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser(session="myapp")
b.open("https://example.com")
b.drain(5.0)
b.close()
# Session data in ~/.local/share/carbonyl/sessions/myapp/
```

### Fork and snapshot

Fork a logged-in session for parallel scraping, or snapshot to pin a known-good state:

```python
from carbonyl_agent import SessionManager

sm = SessionManager()
sm.create("base")
# ... log in, accept cookies, etc. ...

# Fork: two independent profiles that both start logged in
sm.fork("base", "worker-1")
sm.fork("base", "worker-2")

# Snapshot / restore: roll back after A/B testing
sm.snapshot("base", "post-login")
# ... session drifts ...
sm.restore("base", "post-login")   # replaces profile with snapshot
```

See `SessionManager` for the full API: `list`, `destroy`, `exists`, `is_live`, `clean_stale_lock`.

---

## Daemon Mode

A long-running Carbonyl process exposed over a Unix socket. Clients reconnect without losing in-memory state — ideal for agent loops that want to amortize browser startup cost across many short scripts.

```python
from carbonyl_agent import DaemonClient, start_daemon, stop_daemon

# Start (forks a background process)
start_daemon("myapp", "https://example.com")

# Connect from any number of short-lived scripts
client = DaemonClient("myapp")
client.connect()
client.drain(5.0)
text = client.page_text()
client.disconnect()     # leave the daemon running

# ... later, from another script ...
client = DaemonClient("myapp")
client.connect()
client.navigate("https://example.com/login")
client.disconnect()

# Shut down the daemon + browser
stop_daemon("myapp")
```

### Daemon CLI

```bash
carbonyl-agent daemon start myapp https://example.com
carbonyl-agent daemon status
carbonyl-agent daemon attach myapp      # interactive REPL
carbonyl-agent daemon stop myapp
```

Socket: `~/.local/share/carbonyl/daemons/<name>.sock` (mode 0600, parent dir 0700).

---

## Screen Inspection

Find text, debug click targets, and visualize coordinates:

```python
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser()
b.open("https://example.com")
b.drain(8.0)

# Find text and click the first match's center
b.click_text("Sign In")

# Or inspect the screen first
si = b.inspector()
si.print_grid(marks=[(46, 45)])         # overlay a coordinate marker
matches = b.find_text("Continue")       # [{col, row, end_col}, ...]
print(si.annotate(marks=[(m["col"], m["row"]) for m in matches]))
```

---

## Bot Detection Flags

`CarbonylBrowser` applies a curated `_HEADLESS_FLAGS` set at spawn time to minimize detection by commercial bot-detection engines (Akamai, Cloudflare, PerimeterX):

- Spoofed Firefox User-Agent (removes the `(Carbonyl)` marker and Chrome identifier)
- `--disable-blink-features=AutomationControlled` (suppresses `navigator.webdriver=true`)
- `--disable-http2` (HTTP/2 SETTINGS frame is a server-side fingerprint)
- Standard `--no-first-run`, `--disable-sync`, `--use-mock-keychain` flags

**If you hit bot-detection walls, do not remove these flags — they are the baseline.** For additional entropy, call `CarbonylBrowser.mouse_path([...])` to simulate organic mouse movement before interaction.

---

## Binary Search Order

1. `CARBONYL_BIN` env var (explicit path)
2. `~/.local/share/carbonyl/bin/<triple>/carbonyl` (installed by `carbonyl-agent install`)
3. `carbonyl` on `$PATH`
4. Docker fallback (requires `CARBONYL_ALLOW_DOCKER=1`)

### Docker fallback (opt-in)

When no local binary is installed, the SDK can fall back to `docker run fathyb/carbonyl` — but this is opt-in for supply-chain safety:

```bash
export CARBONYL_ALLOW_DOCKER=1
python -c "from carbonyl_agent import CarbonylBrowser; CarbonylBrowser().open('https://example.com')"
```

Without `CARBONYL_ALLOW_DOCKER=1`, attempts to use Docker fallback raise `RuntimeError` with a clear message. The fallback pulls by pinned SHA256 digest, not a mutable `:latest` tag.

---

## Error Handling

Common exceptions:

| Exception | Raised when |
|-----------|-------------|
| `ValueError` | invalid session name (path traversal, too long, empty) |
| `FileExistsError` | session already exists on `create()` |
| `KeyError` | session not found on `get()` / `destroy()` / `restore()` |
| `RuntimeError` | destructive op on a live session; Docker fallback blocked |
| `pexpect.EOF` / `pexpect.TIMEOUT` | browser subprocess died or read timed out |

Retry pattern for flaky network:

```python
import pexpect
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser()
for attempt in range(3):
    try:
        b.open(url)
        b.drain(10)
        break
    except (pexpect.TIMEOUT, pexpect.EOF):
        b.close()
        b = CarbonylBrowser()
```

---

## Documentation

- [CHANGELOG](CHANGELOG.md) — release history
- [CONTRIBUTING](CONTRIBUTING.md) — dev setup, test suite, PR guidelines
- [pyproject.toml](pyproject.toml) — dependencies, CLI entry points

### Related projects

- **[carbonyl](https://git.integrolabs.net/roctinam/carbonyl)** — the Chromium fork that produces the runtime binary
- **[carbonyl-fleet](https://git.integrolabs.net/roctinam/carbonyl-fleet)** — server for managing N concurrent Carbonyl instances over PTY + Unix socket

---

## Contributing

PRs and issues welcome at [git.integrolabs.net/roctinam/carbonyl-agent](https://git.integrolabs.net/roctinam/carbonyl-agent) or [github.com/jmagly/carbonyl-agent](https://github.com/jmagly/carbonyl-agent).

- Run the test suite: `pytest`
- Type-check: `mypy --strict src/`
- Lint: `ruff check .`

---

## Community & Support

- **Issues**: [git.integrolabs.net/roctinam/carbonyl-agent/issues](https://git.integrolabs.net/roctinam/carbonyl-agent/issues)
- **Discussions**: [github.com/jmagly/carbonyl-agent/discussions](https://github.com/jmagly/carbonyl-agent/discussions)

---

## License

**MIT License** — see [LICENSE](LICENSE).

---

## Sponsors

<table>
<tr>
<td width="33%" align="center">

### [Roko Network](https://roko.network)

**The Temporal Layer for Web3**

Enterprise-grade timing infrastructure for blockchain applications.

</td>
<td width="33%" align="center">

### [Selfient](https://selfient.xyz)

**No-Code Smart Contracts for Everyone**

Making blockchain-based agreements accessible to all.

</td>
<td width="33%" align="center">

### [Integro Labs](https://integrolabs.io)

**AI-Powered Automation Solutions**

Custom AI and blockchain solutions for the digital age.

</td>
</tr>
</table>

**Interested in sponsoring?** Open a discussion on [GitHub](https://github.com/jmagly/carbonyl-agent/discussions).

---

## Acknowledgments

Built on top of [Carbonyl](https://github.com/fathyb/carbonyl) by Fathy Boundjadj. The `roctinam/carbonyl` fork is actively maintained through the M147 Chromium line. PTY handling via [pexpect](https://github.com/pexpect/pexpect); terminal parsing via [pyte](https://github.com/selectel/pyte).

---

<div align="center">

**[⬆ Back to Top](#carbonyl-agent)**

</div>
