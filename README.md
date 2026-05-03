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

with CarbonylBrowser() as b:
    b.open("https://example.com")
    b.drain(8.0)
    print(b.page_text())
# close() runs automatically on exit, even on exception
```

`CarbonylBrowser` and `DaemonClient` both implement the context-manager
protocol (#24) — preferred for any script where an unhandled exception
should still tear the browser down cleanly.

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

### Persona profiles

`persona=` is a higher-level alternative to `session=` keyed on a stable persona identity. Profiles live under `CARBONYL_AGENT_PROFILES_DIR` (default `~/.config/carbonyl-agent/profiles/`), separate from the runtime session store, and ship with public `purge_profile` / `export_profile` / `import_profile` operations:

```python
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser(persona="my_throwaway")
b.open("https://example.com")
b.drain(5.0)
b.close()                                     # cookies, localStorage persist

# Backup / CI seeding
b.export_profile("/backups/my_throwaway.tar.gz")
b.import_profile("/backups/my_throwaway.tar.gz")

# Rotate the persona — wipe its state but keep the name registered
b.purge_profile()
```

A file lock prevents accidental dual-open of the same persona; a second open raises `RuntimeError` naming the holding PID. Profiles are portable across `input_backend="pty"` and `input_backend="uinput"` — recording happens at the metadata level only.

`persona=` and `session=` are mutually exclusive on the constructor; pick one per browser instance.

---

## Daemon Mode

A long-running Carbonyl process exposed over a **Unix domain socket** (not TCP/HTTP — there is no listen port or base URL). Clients reconnect without losing in-memory state — ideal for agent loops that want to amortize browser startup cost across many short scripts.

**Transport contract** (issue #47):

| Concern | Default | Override |
|---|---|---|
| Socket path | `~/.local/share/carbonyl/sessions/<session>.sock` | `session_dir=` kwarg or `CARBONYL_SESSION_DIR` env var |
| Permissions | socket `0o600`, parent dir `0o700` | (not configurable) |
| Public path API | `from carbonyl_agent import sock_path, DEFAULT_SOCKET_DIR` | — |
| TCP-style readiness | `is_daemon_live(session_name)` — checks the socket accepts connections | — |
| Semantic readiness | `client.ping()` — round-trips the `hello` handshake; returns `bool`, never raises | — |

**Containers**: the daemon and clients must share a filesystem path for the socket. Either run both inside the same container, or bind-mount the session dir from host into container so the host can `DaemonClient("myapp", session_dir=Path("/host/path"))` to reach the in-container daemon.

```python
from carbonyl_agent import DaemonClient, start_daemon, stop_daemon

# Start (forks a background process)
start_daemon("myapp", "https://example.com")

# Connect from any number of short-lived scripts. The context manager
# disconnects the local socket on exit but leaves the daemon running.
with DaemonClient("myapp") as client:
    client.drain(5.0)
    text = client.page_text()

# ... later, from another script ...
with DaemonClient("myapp") as client:
    client.navigate("https://example.com/login")
    client.wait_for_render_settle()        # #50: same probe as CarbonylBrowser

# Shut down the daemon + browser
stop_daemon("myapp")
```

Multiple short-lived clients can share one long-running daemon — that's
the whole point. The browser keeps its in-memory cookies / localStorage
across clients, so a login script and a scraping script can run as two
separate Python processes against the same authenticated session.

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

`ScreenInspector` also exposes `region(top, left, bottom, right)` for
extracting a rectangular slice of the rendered grid — useful when the
page has multiple lookalike controls and you need to scope `find_text`
to a known panel.

---

## Error Handling

The SDK raises specific exceptions you can catch by category instead of
matching on string messages:

```python
from carbonyl_agent import (
    CarbonylBrowser, DaemonClient, BackendMismatchError, is_daemon_live,
)

# Binary not found at install / first spawn
try:
    b = CarbonylBrowser()
    b.open("https://example.com")
except FileNotFoundError as exc:
    # Run `carbonyl-agent install` or set CARBONYL_BIN
    print(f"runtime missing: {exc}")

# Daemon connect when nothing is listening
if not is_daemon_live("myapp"):
    raise RuntimeError("start the daemon first: carbonyl-agent daemon start myapp")

# Backend contract enforcement (#40) — fail fast when a uinput-only
# script connects to a pty-only daemon
try:
    client = DaemonClient("myapp", require_backend="uinput")
    client.connect()
except BackendMismatchError as exc:
    print(f"daemon has wrong input backend: {exc}")

# Render-readiness without wall-clock guessing (#48 / #50)
with CarbonylBrowser() as b:
    b.open("https://slow-site.example.com")
    if not b.wait_for_render_settle(timeout=10.0):
        raise TimeoutError("page never settled within 10s")
```

For the persona profile lock (raised when two processes try to open the
same persona): `RuntimeError` is raised with the holding PID in the
message so the second caller can decide whether to wait, kill, or pick
a different persona.

---

## Bot Detection Flags

`CarbonylBrowser` applies a curated `_HEADLESS_FLAGS` set at spawn time to minimize detection by commercial bot-detection engines (Akamai, Cloudflare, PerimeterX):

- Spoofed Firefox User-Agent (removes the `(Carbonyl)` marker and Chrome identifier)
- `--disable-blink-features=AutomationControlled` (suppresses `navigator.webdriver=true`)
- `--disable-http2` (HTTP/2 SETTINGS frame is a server-side fingerprint)
- Standard `--no-first-run`, `--disable-sync`, `--use-mock-keychain` flags

**If you hit bot-detection walls, do not remove these flags — they are the baseline.** For additional entropy, call `CarbonylBrowser.mouse_path([...])` to simulate organic mouse movement before interaction.

### Trusted input backend (uinput)

Synthetic browser events arrive at JavaScript with `event.isTrusted = false`. Modern React forms and bot-detection libraries refuse to update controlled-input state when this flag is false, so scripted login on X, LinkedIn, and similar sites silently fails — typed text is rendered into the input but never submitted.

`CarbonylBrowser` accepts an `input_backend="uinput"` constructor argument. When set, every `send()` / `send_key()` / `click()` / `mouse_move()` routes through `/dev/uinput`. The kernel routes the events through Xorg into Chromium with `isTrusted = true`, indistinguishable from a physical keyboard and mouse.

```python
from carbonyl_agent import CarbonylBrowser, ANTI_FEDCM_FLAGS

with CarbonylBrowser(
    cols=500, rows=150,
    viewport=(1280, 800),
    input_backend="uinput",
    extra_flags=ANTI_FEDCM_FLAGS,
) as b:
    b.open("https://x.com/i/flow/login")
    b.drain(15)
    b.click(320, 88)         # focus the input
    b.send("jmagly")         # typed via uinput → isTrusted=true
    b.send_key("enter")      # advances the form
    ...
```

**Requirements**:

- Linux host with `/dev/uinput` writable (`sudo modprobe uinput` if missing; user in `input` group or use the `99-uinput.rules` udev rule from `scripts/setup-uinput-host.sh`)
- An X server running so Carbonyl's `--ozone-platform=x11` build has a display to attach to
- The `python-uinput` package: `pip install python-uinput`

**Recommended deployment**: run inside the `carbonyl-agent-qa-runner` container, which packages Xorg, the X-Carbonyl runtime, and uinput passthrough so you don't have to assemble the environment yourself:

```bash
docker pull git.integrolabs.net/roctinam/carbonyl-agent/qa-runner:latest
cd docker/qa-runner && ./run.sh pytest tests/
```

See `roctinam/carbonyl/docs/runtime-modes.md` for the full deployment-shape reference (terminal-only / x11+uinput / x11+uinput+X-mirror) and ADR-002 rev 2 for the architecture rationale.

### Composing flags for specific scenarios

Flag groups are published as module constants so agents can pick and choose:

```python
from carbonyl_agent import (
    CarbonylBrowser,
    DEFAULT_HEADLESS_FLAGS,   # baseline (applied automatically)
    BASE_CHROMIUM_FLAGS,      # first-run / keychain suppression only
    ANTI_BOT_FLAGS,           # UA spoof, no-webdriver, HTTP/1.1
    ANTI_FEDCM_FLAGS,         # disable Google One Tap (X, LinkedIn, publishers)
    ANTI_ONETAP_FLAGS,        # alias for ANTI_FEDCM_FLAGS
)

# Default: BASE_CHROMIUM_FLAGS + ANTI_BOT_FLAGS
b = CarbonylBrowser()

# Add Google One Tap suppression — required for scripted X/Twitter login
b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS)

# Compose multiple groups:
b = CarbonylBrowser(extra_flags=ANTI_FEDCM_FLAGS + ["--disable-extensions"])

# Completely replace the defaults (rarely needed):
b = CarbonylBrowser(base_flags=[*BASE_CHROMIUM_FLAGS, "--my-flag"])
```

When to reach for `ANTI_FEDCM_FLAGS`: any site that aggressively overlays
Google Sign-In on top of its own login form. Without this, the overlay's
autofocused input steals your keystrokes and the underlying form is
unreachable.

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
