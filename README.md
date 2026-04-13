# carbonyl-agent

Python automation SDK for the [Carbonyl](https://git.integrolabs.net/roctinam/carbonyl) headless browser.

## Install

```bash
pip install carbonyl-agent

# Download the Carbonyl runtime binary
carbonyl-agent install
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

## Session Persistence

```python
from carbonyl_agent import CarbonylBrowser, SessionManager

b = CarbonylBrowser(session="myapp")
b.open("https://example.com")
b.drain(5.0)
b.close()
# Session cookies and state persist in ~/.local/share/carbonyl/sessions/myapp/
```

## Daemon Mode

```python
from carbonyl_agent.daemon import DaemonClient, start_daemon

start_daemon("myapp", "https://example.com")

client = DaemonClient("myapp")
client.connect()
text = client.page_text()
client.close()
```

## Binary Search Order

1. `CARBONYL_BIN` env var
2. `~/.local/share/carbonyl/bin/<triple>/carbonyl` (installed by `carbonyl-agent install`)
3. `carbonyl` on `$PATH`
4. Docker fallback: `docker run fathyb/carbonyl`

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.
