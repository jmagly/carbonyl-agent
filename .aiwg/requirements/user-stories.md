# User Stories — carbonyl-agent

**Status**: Baselined v1.0, 2026-04-09

**Personas**
- **SDK consumer** — Python developer writing scripts that drive Carbonyl
- **Automation engineer** — builds multi-step scraping / agent pipelines with persistent state
- **Library maintainer** — owns carbonyl-agent releases, CI, and support

Priority legend: **MUST** (0.1 release blocker), **SHOULD** (0.1 highly desired), **COULD** (post-0.1).

---

## Epic A — Browser Control

### US-001 — Open page and get text
**As an** SDK consumer, **I want** to open a URL and receive its rendered text, **so that** I can scrape content without writing PTY glue.
- Acceptance criteria:
  - `CarbonylBrowser().open(url); drain(n); page_text()` returns deduplicated text.
  - Box-drawing / block unicode characters are filtered from output.
  - `close()` leaves no orphaned Chromium PID.
- Priority: **MUST**   |   Use case: UC-001

### US-002 — Click by text
**As an** automation engineer, **I want** to click on a visible label (`click_text("Sign In")`), **so that** I don't have to hardcode coordinates.
- Acceptance criteria:
  - `click_text` returns the `(col, row)` clicked or `None`.
  - `click_at_row(text, row)` disambiguates repeated labels.
  - Works in both direct and daemon-connected modes.
- Priority: **MUST**   |   Use case: UC-001, UC-004

### US-003 — Mouse path and key input
**As an** automation engineer, **I want** to send mouse paths and named keys (`enter`, `tab`, arrows), **so that** I can satisfy interaction-entropy bot checks and fill forms.
- Acceptance criteria:
  - `mouse_path([...], delay)` emits SGR mouse-move sequences at each waypoint.
  - `send_key` supports `enter|tab|backspace|up|down|left|right|escape` and raises `ValueError` on unknown keys.
  - `send(text)` transmits UTF-8 bytes.
- Priority: **SHOULD**   |   Use case: UC-001

---

## Epic B — Session Persistence

### US-004 — Named sessions
**As an** automation engineer, **I want** to create/list/destroy named sessions backed by `--user-data-dir`, **so that** cookies survive restarts.
- Acceptance criteria:
  - `SessionManager.create/destroy/list/info` work against `$CARBONYL_SESSION_DIR` or default.
  - Slug validation rejects uppercase / leading-hyphen names.
  - `destroy` refuses a live session unless `force=True`.
- Priority: **MUST**   |   Use case: UC-002

### US-005 — Fork and snapshot/restore
**As an** automation engineer, **I want** to fork a session and snapshot/restore it by tag, **so that** I can branch from a known-good state (e.g. post-login).
- Acceptance criteria:
  - `fork` refuses a live source; copies profile; clears `SingletonLock` in the copy.
  - `snapshot(name, tag)` stores as `<name>--snap--<tag>` and sets `snapshot_of` metadata.
  - `restore(name, tag)` replaces the profile atomically and clears the stale lock.
- Priority: **SHOULD**   |   Use case: UC-002

### US-006 — Stale lock cleanup
**As an** SDK consumer, **I want** stale `SingletonLock` files removed automatically, **so that** a crashed previous run doesn't block the next open.
- Acceptance criteria:
  - `is_live()` verifies PID aliveness via `os.kill(pid, 0)`.
  - `clean_stale_lock()` unlinks only when the owning PID is dead.
  - `open()` calls `clean_stale_lock()` before passing the profile to Chromium.
- Priority: **MUST**   |   Use case: UC-002, UC-007

---

## Epic C — Daemon Mode

### US-007 — Persistent daemon
**As an** automation engineer, **I want** to run a long-lived browser daemon per session over a Unix socket, **so that** short Python scripts can reuse one browser.
- Acceptance criteria:
  - `daemon start <session> [url]` forks, daemonises, and listens on `<session_dir>/<name>.sock`.
  - Second invocation fails with "already running" when a live daemon exists.
  - `daemon status` lists live/stopped per session.
- Priority: **SHOULD**   |   Use case: UC-003

### US-008 — Transparent client reconnect
**As an** SDK consumer, **I want** `CarbonylBrowser(session=...).open(url)` to auto-attach to a live daemon, **so that** my script code is identical in direct and daemon modes.
- Acceptance criteria:
  - When daemon is live, `open()` creates a `DaemonClient` and navigates instead of spawning.
  - `send`, `click`, `drain`, `page_text`, `find_text`, `raw_lines`, `navigate` transparently route to the daemon.
  - `disconnect()` releases the socket without stopping the browser; `close()` stops the daemon.
- Priority: **SHOULD**   |   Use case: UC-003, UC-007

---

## Epic D — Runtime Management

### US-009 — One-shot runtime install
**As an** SDK consumer, **I want** `carbonyl-agent install` to download the platform-specific runtime tarball, **so that** I don't have to build Chromium locally.
- Acceptance criteria:
  - Resolves `runtime-latest` via Gitea releases API.
  - Streams progress, extracts to `~/.local/share/carbonyl/bin/<triple>/`, chmods executable.
  - `--force` reinstalls; default skips if present.
- Priority: **MUST**   |   Use case: UC-005

### US-010 — Runtime status and discovery
**As an** SDK consumer, **I want** `carbonyl-agent status` plus a documented binary discovery order, **so that** I can diagnose "binary not found" quickly.
- Acceptance criteria:
  - `status` prints resolved path and `carbonyl --version` output, or a clear "not found" message.
  - Discovery honours `CARBONYL_BIN` → install dir → `$PATH` → Docker fallback.
- Priority: **MUST**   |   Use case: UC-005, UC-006

---

## Epic E — Observability

### US-011 — Screen inspection for debugging
**As an** automation engineer, **I want** a `ScreenInspector` with grids, crosshairs, region summaries, and annotated snippets, **so that** I can debug click targets and feed structured context to LLM agents.
- Acceptance criteria:
  - `print_grid`, `crosshair`, `dot_map`, `annotate`, `summarise_region` all work against a `raw_lines()` snapshot.
  - Heuristic `summarise_region` flags `button`, `input_field`, `checkbox`, `url`.
  - All coordinates are 1-indexed, matching `find_text` return values.
- Priority: **SHOULD**   |   Use case: UC-004

---

## Epic F — Release Engineering

### US-012 — Releasable package with CI and integrity checks
**As a** library maintainer, **I want** CI-gated tests, SHA256-verified runtime downloads, and a tagged release workflow, **so that** I can publish to PyPI with confidence.
- Acceptance criteria:
  - CI runs `pytest tests/` on push across Python 3.11, 3.12.
  - `install.py` verifies a SHA256 manifest for the downloaded tarball before extraction.
  - Release process produces a git tag, Gitea release, GitHub mirror, and PyPI upload.
- Priority: **SHOULD**   |   Use case: UC-005
