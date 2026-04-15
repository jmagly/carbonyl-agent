<table align="center">
  <tbody>
    <tr>
      <td>
        <p></p>
        <pre>
   O    O
    \  /
O —— Cr —— O
    /  \
   O    O</pre>
      </td>
      <td><h1>carbonyl-agent</h1></td>
    </tr>
  </tbody>
</table>

Python automation SDK for the [Carbonyl](https://git.integrolabs.net/roctinam/carbonyl) headless browser.

## Install

```bash
pip install carbonyl-agent

# Download the Carbonyl runtime binary (verified via SHA256)
carbonyl-agent install

# Or pin to a known checksum for reproducible installs
carbonyl-agent install --checksum <sha256-hex>
```

## Quick Start

```python
from carbonyl_agent import CarbonylBrowser

b = CarbonylBrowser()
b.open("https://example.com")
b.drain(8.0)
print(b.page_text())
b.close()
```

All public API is importable directly from the package root:

```python
from carbonyl_agent import (
    CarbonylBrowser, SessionManager, ScreenInspector,
    DaemonClient, start_daemon, stop_daemon,
)
```

## Session Persistence

Named sessions persist cookies, localStorage, and IndexedDB across browser restarts:

```python
from carbonyl_agent import CarbonylBrowser, SessionManager

b = CarbonylBrowser(session="myapp")
b.open("https://example.com")
b.drain(5.0)
b.close()
# Session data in ~/.local/share/carbonyl/sessions/myapp/
```

### Session fork and snapshot

Fork a logged-in session for parallel scraping, or snapshot to pin a known-good state:

```python
sm = SessionManager()
sm.create("base")
# ... log in, accept cookies, etc. ...

# Fork: two independent profiles
sm.fork("base", "worker-1")
sm.fork("base", "worker-2")

# Snapshot / restore: roll back after A/B testing
sm.snapshot("base", "post-login")
# ... session drifts ...
sm.restore("base", "post-login")   # replaces profile with snapshot
```

See `SessionManager` for the full API (list, destroy, exists, is_live, clean_stale_lock).

## Daemon Mode

A long-running Carbonyl process exposed over a Unix socket. Clients reconnect without losing in-memory state:

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

## Bot Detection Flags

`CarbonylBrowser` applies a curated `_HEADLESS_FLAGS` set at spawn time to minimize detection by commercial bot-detection engines (Akamai, Cloudflare, PerimeterX):

- Spoofed Firefox User-Agent (removes the "(Carbonyl)" marker and Chrome identifier)
- `--disable-blink-features=AutomationControlled` (suppresses `navigator.webdriver=true`)
- `--disable-http2` (HTTP/2 SETTINGS frame is a fingerprint used server-side)
- Standard no-first-run, disable-sync, mock-keychain flags

If you're hitting bot-detection walls, **do not remove these flags**. They are the baseline. For additional entropy, use `CarbonylBrowser.mouse_path([...])` to simulate organic mouse movement before interaction.

## Docker Fallback (opt-in)

When no local binary is installed, the SDK can fall back to `docker run fathyb/carbonyl` — but this is opt-in for supply-chain safety:

```bash
export CARBONYL_ALLOW_DOCKER=1
python -c "from carbonyl_agent import CarbonylBrowser; CarbonylBrowser().open('https://example.com')"
```

Without `CARBONYL_ALLOW_DOCKER=1`, attempts to use Docker fallback raise `RuntimeError` with a clear message. The fallback pulls by pinned SHA256 digest, not a mutable `:latest` tag.

## Error Handling

Common exceptions:

- `ValueError` — invalid session name (path traversal, too long, empty)
- `FileExistsError` — session already exists on `create()`
- `KeyError` — session not found on `get()` / `destroy()` / `restore()`
- `RuntimeError` — session is live when a destructive op is requested (destroy/fork/restore); also when Docker fallback is blocked
- `pexpect.EOF` / `pexpect.TIMEOUT` — browser subprocess died or read timed out (handled internally by `drain()`; propagates on `send()`)

Retry pattern for flaky network:

```python
for attempt in range(3):
    try:
        b.open(url)
        b.drain(10)
        break
    except (pexpect.TIMEOUT, pexpect.EOF):
        b.close()
        b = CarbonylBrowser()
```

## Binary Search Order

1. `CARBONYL_BIN` env var (explicit path)
2. `~/.local/share/carbonyl/bin/<triple>/carbonyl` (installed by `carbonyl-agent install`)
3. `carbonyl` on `$PATH`
4. Docker fallback (requires `CARBONYL_ALLOW_DOCKER=1`)

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.
