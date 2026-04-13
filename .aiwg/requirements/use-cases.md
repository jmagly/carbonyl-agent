# Use Cases — carbonyl-agent

**Status**: Baselined v1.0, 2026-04-09
**Scope**: Python automation SDK for the Carbonyl headless browser
**Primary actor**: Python developer / automation script (SDK consumer)

---

## UC-001 — Open a page and extract readable text

**Actor**: SDK consumer
**Related modules**: `browser.py:CarbonylBrowser.open`, `drain`, `page_text`, `extract_text`

**Preconditions**
- Carbonyl runtime binary is resolvable (see UC-006).
- Python ≥3.11 environment with `pexpect` and `pyte` installed.

**Main flow**
1. Consumer instantiates `CarbonylBrowser()`.
2. Consumer calls `browser.open(url)`; SDK spawns Carbonyl under a PTY sized to COLS×ROWS (220×50) with hardened Chromium flags.
3. Consumer calls `browser.drain(seconds)` to feed PTY bytes into the pyte screen buffer until the page has settled.
4. Consumer calls `browser.page_text()` to receive deduplicated, block-character-filtered text.
5. Consumer calls `browser.close()` to terminate the child process.

**Alternative flows**
- **1a**. Binary not found locally → SDK falls back to `docker run fathyb/carbonyl` (UC-006).
- **3a**. PTY reports EOF mid-drain → drain loop exits early; `page_text()` still returns whatever was buffered.
- **4a**. Consumer calls `find_text()` / `raw_lines()` / `inspector()` instead of `page_text()` for structured queries.

**Postconditions**
- Child process is terminated; no lingering Chromium PID.
- Pyte buffer state is discarded on close.

---

## UC-002 — Persist session state across browser restarts

**Actor**: Automation engineer building multi-step flows (login → navigate → scrape)
**Related modules**: `session.py:SessionManager`, `browser.py:CarbonylBrowser(session=...)`

**Preconditions**
- Session store directory (`~/.local/share/carbonyl/sessions/` or `$CARBONYL_SESSION_DIR`) is writable.

**Main flow**
1. Consumer creates or references a named session: `SessionManager().create("my-session")`.
2. Consumer instantiates `CarbonylBrowser(session="my-session")`.
3. On `open()`, SDK resolves the session's `profile/` directory and passes it as `--user-data-dir` to Chromium.
4. Consumer performs interactive steps (login, cookie acceptance, etc.).
5. Consumer calls `close()`; SDK sends SIGTERM to let Chromium flush cookies, waits `graceful_timeout` seconds, then SIGKILL.
6. On next run with the same session name, cookies / localStorage / IndexedDB are restored.

**Alternative flows**
- **3a**. Previous run left a stale `SingletonLock` → `clean_stale_lock()` removes it if the owning PID is dead.
- **3b**. Session is already live (lock PID alive) → `is_live()` returns True; consumer must stop the existing browser or use a fork (UC-002 variant).
- **4a**. Consumer snapshots post-login state via `SessionManager.snapshot("my-session", "post-login")` for later `restore()`.

**Postconditions**
- Profile directory contains updated Chromium state files.
- No stale SingletonLock.

---

## UC-003 — Run a daemon for a long-lived browser instance

**Actor**: Automation engineer running multiple short Python processes against the same browser
**Related modules**: `daemon.py:start_daemon`, `DaemonClient`, `is_daemon_live`; `browser.py:CarbonylBrowser.open` (reconnect path)

**Preconditions**
- Session exists or will be auto-created.
- No existing live daemon for the same session.

**Main flow**
1. Operator runs `python -m carbonyl_agent.daemon start my-session https://example.com`.
2. Parent process forks; child calls `setsid()`, redirects stdio to `/dev/null`, opens the browser, and creates a Unix socket at `<session_dir>/my-session.sock`.
3. Parent polls `is_daemon_live()` until the socket accepts connections (bounded by `wait` seconds).
4. A separate Python script instantiates `CarbonylBrowser(session="my-session")` and calls `open(url)`; SDK detects the live daemon and returns a `DaemonClient` proxy instead of spawning a new browser.
5. Client sends newline-delimited JSON RPCs (`send`, `click`, `drain`, `navigate`, `page_text`, `find_text`, `raw_lines`, …); daemon dispatches to the single shared `CarbonylBrowser`.
6. Client calls `browser.disconnect()` to release the socket while leaving the browser running, or `browser.close()` to stop the daemon entirely.

**Alternative flows**
- **2a**. Socket file is stale (previous crash) → `is_daemon_live()` unlinks it and returns False; fresh start succeeds.
- **5a**. Long `drain` command → client extends socket timeout to `seconds + 10s` (`daemon.py:DaemonClient._rpc`).
- **6a**. Operator runs `python -m carbonyl_agent.daemon stop my-session` to shut down externally.

**Postconditions**
- Daemon PID and socket path are recorded in the session metadata JSON.
- Socket file is removed on graceful shutdown.

---

## UC-004 — Inspect screen coordinates and regions

**Actor**: SDK consumer debugging click targets or building LLM-driven agents that reason about screen layout
**Related modules**: `screen_inspector.py:ScreenInspector`, `browser.py:CarbonylBrowser.inspector`, `find_text`, `raw_lines`

**Preconditions**
- Browser has rendered at least one page (screen buffer populated).

**Main flow**
1. Consumer calls `si = browser.inspector()` (snapshot of current raw lines).
2. Consumer calls `si.find("Sign In")` to locate text occurrences (1-indexed `col`/`row`/`end_col`).
3. Consumer calls `si.print_grid(marks=[(col, row)])` to render a ruled ASCII grid with the target highlighted.
4. Consumer optionally calls `si.annotate(marks=..., context_rows=3)` to produce LLM-friendly context windows, or `si.summarise_region(...)` for heuristic form / button / input detection.
5. Consumer uses the returned coordinates to drive `browser.click(col, row)` or `browser.click_text("Sign In")`.

**Alternative flows**
- **3a**. Consumer uses `si.crosshair(col, row)` for a localised view instead of the full grid.
- **3b**. Consumer uses `si.dot_map(step_col=20, step_row=5)` to calibrate coordinates against a novel page layout.

**Postconditions**
- No state change; ScreenInspector is read-only.

---

## UC-005 — Install or upgrade the Carbonyl runtime binary

**Actor**: Developer or CI job preparing an environment
**Related modules**: `install.py:cmd_install`, `_resolve_tag`, `_platform_triple`

**Preconditions**
- Network access to `$GITEA_BASE` (default `https://git.integrolabs.net`).
- Write access to the install destination (default `~/.local/share/carbonyl/bin/`).

**Main flow**
1. User runs `carbonyl-agent install` (optionally `--tag runtime-<hash>` or `--dest <path>`).
2. CLI resolves the platform triple via `uname -m` / `uname -s` (e.g. `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin`).
3. If `--tag runtime-latest`, CLI queries Gitea `releases/latest` API and resolves to the concrete tag.
4. CLI downloads `<GITEA_BASE>/roctinam/carbonyl/releases/download/<tag>/<triple>.tgz` into a temp file, streaming a progress indicator.
5. CLI extracts the tarball into `<dest>/<triple>/`, stripping the leading triple directory component.
6. CLI chmods the extracted `carbonyl` binary executable and reports the final path.

**Alternative flows**
- **4a**. Binary already installed and `--force` not given → CLI prints "Already installed" and exits 0.
- **4b**. HTTP error (404, network failure) → CLI prints error and exits 1; temp file is cleaned up.
- **5a**. Tarball layout differs from the expected `<triple>/…` prefix → fallback preserves top-level files.

**Postconditions**
- `<dest>/<triple>/carbonyl` is executable.
- Temp tarball is deleted.

---

## UC-006 — Detect runtime via env / install dir / PATH / Docker fallback

**Actor**: `CarbonylBrowser` internal resolution (transparent to consumer)
**Related modules**: `browser.py:_local_binary`, `_platform_triple`, Docker fallback branch in `open`

**Preconditions**
- None (always runs on `open()`).

**Main flow**
1. SDK checks `$CARBONYL_BIN`; if set and executable, use it.
2. Otherwise, SDK checks `~/.local/share/carbonyl/bin/<triple>/carbonyl`; if present and executable, use it.
3. Otherwise, SDK runs `which carbonyl`; if found and executable, use it.
4. Otherwise, SDK spawns `bash -c "docker run --rm -it [-v profile:/data/profile] fathyb/carbonyl …"`.

**Alternative flows**
- **1a**. `$CARBONYL_BIN` is set but not executable → fall through to step 2 (no error).
- **4a**. Docker not installed → `pexpect.spawn` fails at runtime; consumer sees a clear failure from bash.

**Postconditions**
- `self._child` is a live pexpect process, or a clear failure is raised.

---

## UC-007 — Recover from runtime crash or daemon disconnect

**Actor**: Automation engineer running long flows or supervising daemons
**Related modules**: `browser.py:CarbonylBrowser.close`, `reconnect`, `disconnect`; `daemon.py:is_daemon_live`, `_run_daemon` cleanup

**Preconditions**
- Prior `open()` or `start_daemon()` succeeded at some point.

**Main flow**
1. Consumer detects failure (exception from `drain`, `send`, or RPC).
2. Consumer calls `browser.close()` to best-effort terminate the child (SIGTERM → SIGKILL via process group).
3. Consumer calls `SessionManager().clean_stale_lock(session)` to drop any leftover `SingletonLock`.
4. Consumer re-instantiates `CarbonylBrowser(session=session)` and calls `open(url)` to resume.
5. If a daemon was previously running, `is_daemon_live()` unlinks a stale socket and the consumer starts a new daemon.

**Alternative flows**
- **2a**. Process already dead → `close()` swallows `ProcessLookupError` and returns.
- **4a**. Consumer only needs to observe current browser state without changing URL → `browser.reconnect()` attempts daemon reconnection without navigation.

**Postconditions**
- No orphaned Chromium or daemon PIDs.
- Stale locks and sockets are cleaned; next session start succeeds.
