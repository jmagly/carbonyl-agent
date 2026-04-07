# carbonyl-agent

Python automation SDK for the Carbonyl headless browser.

## Repository

- Gitea: `roctinam/carbonyl-agent` (primary)
- GitHub: `jmagly/carbonyl-agent` (mirror)

## Layout

```
src/carbonyl_agent/
    __init__.py          # public API: CarbonylBrowser, SessionManager, ScreenInspector
    __main__.py          # CLI entry point (carbonyl-agent install / status)
    browser.py           # CarbonylBrowser: PTY + pyte terminal emulation
    daemon.py            # DaemonClient + daemon server (Unix socket)
    install.py           # Runtime download from Gitea releases
    screen_inspector.py  # Coordinate visualization and region summaries
    session.py           # Named session management (user-data-dir)
tests/
    test_smoke.py        # Import + unit + integration smoke tests
```

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Install the Carbonyl runtime binary
.venv/bin/carbonyl-agent install

# Run tests (unit-level tests run without a binary)
.venv/bin/pytest tests/
```

## Binary Search Order

`CarbonylBrowser` finds the Carbonyl binary in this order:

1. `CARBONYL_BIN` env var — explicit path override
2. `~/.local/share/carbonyl/bin/<triple>/carbonyl` — installed by `carbonyl-agent install`
3. `carbonyl` on `$PATH`
4. Docker fallback: `docker run fathyb/carbonyl`

## Relationship to carbonyl repo

`carbonyl-agent` is extracted from `roctinam/carbonyl`'s `automation/` directory.
The `carbonyl` repo owns the Chromium build and the `libcarbonyl.so` Rust FFI layer.
`carbonyl-agent` owns the Python automation layer only.

`carbonyl-fleet` (Rust server) does NOT depend on `carbonyl-agent`.

## Runtime Distribution

Pre-built Carbonyl binaries (~75 MB tarballs) are hosted on Gitea releases at
`roctinam/carbonyl`, tagged `runtime-<hash>`. `carbonyl-agent install` downloads
the tarball for the current platform and extracts it to `~/.local/share/carbonyl/bin/`.
