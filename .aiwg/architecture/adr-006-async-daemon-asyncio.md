# ADR-006: Async Daemon Mode via `asyncio` Unix-Socket Server

**Status**: Rejected (2026-05-05)
**Date**: 2026-05-04 (drafted), 2026-05-05 (rejected)
**Version**: 0.1 (Draft, retained as historical record)
**Deciders**: Joseph Magly (sole maintainer)
**Supersedes**: none
**Refines**: ADR-002 (transport unchanged; concurrency model replaced) — N/A, see rejection rationale
**Related issues**: #19 (closed as won't-do, 2026-05-05), #23 (recovery hooks the async client would have honoured)

> **Rejection rationale (2026-05-05).** carbonyl-agent stays single-actor. Multi-browser concurrency is owned by `carbonyl-fleet` (Rust) — the language and runtime built for it. Adding a parallel async surface to this Python SDK doubles the maintenance burden for a marginal UX win on a layer that is fundamentally single-actor (one PTY, one `pyte.Screen`). The body of this ADR remains as the analysis trail; do not implement it.

> Note: the implementation issue (#19) refers to "ADR-005". That number was claimed by #42 (wreq TLS-fingerprint client) before this work was written up. This ADR is therefore numbered **ADR-006**.

---

## Context

ADR-002 chose a `socketserver.ThreadingUnixStreamServer` for the daemon. The current server (`src/carbonyl_agent/daemon.py` lines 442–563) accepts concurrent client connections via per-handler threads but dispatches into a **single shared `CarbonylBrowser`** without locking. SAD §11.2 ("Daemon concurrency") and §11.5 ("Async daemon interface") have flagged two distinct shortcomings:

1. **Browser-side single-actor reality.** Carbonyl exposes a single PTY whose bytes feed one `pyte.Screen`. Two threads concurrently calling `navigate()` and `page_text()` race on the same screen buffer; the documented mitigation is "one client at a time."
2. **Client-side blocking API.** `DaemonClient` is a synchronous socket consumer. Async consumers (FastAPI/aiohttp endpoints, agent loops driving multiple daemons, LLM tool-use bridges, the `carbonyl-fleet` Rust server's Python callouts) currently have to either run the SDK in a thread pool or block their event loop.

The daemon's wire protocol (line-delimited JSON over `AF_UNIX`) is unchanged by this decision; only the I/O model and client surface change.

US-019 has been "could / size L" since Iteration 1 because the value is real but the surface area is large: any change to the daemon process is also a change to its on-disk PID file, signal handling, and the integration test fixtures (`tests/test_daemon_integration.py`, the `carbonyl-agent-qa-runner` fixture, `tests/e2e/`). #23 cycles 1–3 added `BrowserCrashed`, `DaemonConnectionError`, `RenderTimeoutError`, opt-in `auto_reconnect`, and stale-lock auto-cleanup; an async rewrite must preserve every one of those behaviours bit-for-bit.

Candidate concurrency models considered for the daemon and the corresponding client surface:

1. **`asyncio.start_unix_server` server, async-native client, sync facade preserves backwards compatibility.**
2. **Status quo (`ThreadingUnixStreamServer`) plus a `threading.Lock` around `_dispatch`.** Ships SAD §11.2 directly; does nothing for client-side blocking.
3. **Single-threaded `selectors`-based server.** Lower-dependency than `asyncio` but reinvents framing, timeouts, and cancellation by hand.
4. **`trio` / `anyio` structured-concurrency framework.** Strong cancellation story, but introduces a third-party runtime dependency (`anyio` is already pulled by FastAPI/Starlette ecosystems but is not on the SDK's current dependency list).
5. **gRPC / streaming RPC over the existing socket.** Would unlock server-push semantics but is the "ADR-002 alternative considered and rejected" path; not revisited here.

The SDK's design constraints from ADR-002 still hold: **minimal dependencies, local-only, standard library wherever possible, and a single-developer maintenance budget.** Whatever lands must not double the daemon's source size or introduce a runtime consumers don't already trust.

## Decision

Adopt **option 1**: rewrite the daemon server using `asyncio.start_unix_server` and split the client surface into a native-async `AsyncDaemonClient` plus a backwards-compatible synchronous `DaemonClient` facade. Concretely:

### 1. Server: `asyncio.start_unix_server`

The daemon process replaces `_BrowserServer(socketserver.ThreadingUnixStreamServer)` with an `asyncio.start_unix_server` server bound to the same socket path (`<session_dir>/<session_name>.sock`, mode `0o600`). Connection callbacks are coroutines reading line-delimited JSON via `asyncio.StreamReader.readuntil(b"\n")`.

**Browser dispatch is serialised by an `asyncio.Lock`** held for the duration of each request's `_dispatch`. The browser is and remains a single-actor — the lock makes that explicit instead of pretending threading buys parallelism. Concurrent connections are still accepted; they just queue at the dispatch lock. This kills the SAD §11.2 race without papering over it with documentation.

**Blocking browser calls run on `loop.run_in_executor(None, ...)`** so the event loop stays responsive for connection accepts, heartbeats, and shutdown. Specifically: `browser.drain(seconds)` (which can block up to `seconds + 10`s), `browser.navigate`, `browser.send`, `browser.click`, `browser.send_key` all route through the default executor. `page_text`/`url`/`raw_lines`/`find_text` are pyte buffer reads that already complete in microseconds and run inline on the loop.

**Fork model is unchanged.** `start_daemon()` still does `os.fork() + os.setsid()` (ADR-002, lines 690–714 of the current daemon). Inside the child, the loop is started with `asyncio.run(_run_daemon_async(...))` instead of `_run_daemon` calling `server.serve_forever()`. This preserves the readiness contract (parent polls `is_daemon_live` until the child binds the socket) and the PID-tracking semantics that `daemon_status` and the metadata file rely on.

**Graceful shutdown**: the existing `close` command sets a `shutdown_event = asyncio.Event()`. The main coroutine `await`s the event then calls `server.close()` + `await server.wait_closed()` + `browser.close()`, replacing the watcher thread + `atexit` pair. The `atexit` socket-unlink handler is kept as defence-in-depth for crashes.

### 2. Client: native-async `AsyncDaemonClient`

A new `AsyncDaemonClient` exposes the same method names as `DaemonClient` (`send`, `click`, `navigate`, `page_text`, `nav_bar_url`, `find_text`, `raw_lines`, `wait_for_render_settle`, `ping`, `close_daemon`, `connect`/`disconnect`) but every method is `async` and uses `asyncio.open_unix_connection`. Auto-reconnect (#23) is implemented as an `async` retry loop with `asyncio.sleep(backoff)`.

The async client supports `async with AsyncDaemonClient(name) as c: ...` and is the recommended surface for new async code.

### 3. Client: backwards-compatible sync `DaemonClient`

`DaemonClient` is preserved as a sync facade. Two implementation options were weighed:

- **3a. Keep the existing direct-socket implementation** (sync `socket.AF_UNIX`). Zero regression risk. `AsyncDaemonClient` is added alongside as a parallel implementation. Slight DRY hit: connect/handshake/RPC framing is implemented twice but each is ~100 lines.
- **3b. Rewrite `DaemonClient` as a thin sync wrapper that runs an `AsyncDaemonClient` on a private event loop** (background thread + `asyncio.run_coroutine_threadsafe`). Single source of truth for the wire protocol, but introduces a thread-per-client and the well-known footguns of cross-loop cancellation.

**Decision**: ship **3a** for v0.x, with the option to revisit 3b in a future minor version once `AsyncDaemonClient` has bedded in. The DRY argument is real but the existing sync client is 200 lines of audited code that #23 just fortified; replacing it with a thread-bridged async wrapper to save ~100 lines is a poor trade until the async client's behaviour is itself stable. See "Alternatives Considered" §C below for the explicit comparison.

### 4. Wire protocol: unchanged

All commands (`hello`, `send`, `click`, `mouse_move`, `key`, `drain`, `navigate`, `page_text`, `url`, `find_text`, `raw_lines`, `close`) and their request/response shapes are byte-for-byte identical. `PROTOCOL_VERSION` stays at 1. An old sync `DaemonClient` MUST be able to talk to the new async daemon and vice versa; this is a regression test (see §Acceptance below).

### 5. Compatibility window

For one minor release (v0.2 → v0.3), both server implementations remain in the codebase behind a `CARBONYL_DAEMON_BACKEND={threaded|asyncio}` environment variable, defaulting to `threaded` in v0.2 and flipping to `asyncio` in v0.3. The threaded implementation is removed in v0.4. This gives downstream consumers (`carbonyl-fleet`, `carbonyl-agent-qa-runner`, in-house users) one release to validate the async server in their environment before it becomes the only option.

## Consequences

### Positive

- **SAD §11.2 ("Daemon concurrency") is closed.** Browser dispatch is serialised by a lock instead of documented as "one client at a time."
- **SAD §11.5 ("Async daemon interface") is closed** for downstream consumers. FastAPI handlers, aiohttp middleware, and async agent loops can `await client.navigate(...)` without thread-pool wrapping. Concretely: an LLM agent loop driving N daemons can `asyncio.gather()` their `page_text` calls and overlap network I/O with browser I/O.
- **Cancellation is first-class.** `asyncio.wait_for(client.drain(30), timeout=5)` actually unwinds; today, `_rpc_once`'s `socket.settimeout` only bounds the RPC, not the caller's wall-clock budget.
- **`DaemonClient` (sync) keeps zero behavioural regressions.** Existing tests, `attach` REPL, and the `auto_reconnect` retry policy from #23 cycle 2 are unchanged.
- **No new runtime dependencies.** `asyncio` is stdlib; `pexpect ≥ 4.9` (already required) supports `expect_async` if the browser side ever wants to go async too — out of scope here, but the door isn't closed.
- **The wire protocol unification means the test matrix doubles for free**: every existing daemon integration test runs against both backend implementations during the v0.2/v0.3 window, exposing protocol-drift bugs before they ship.

### Negative

- **Browser is still a single actor.** `asyncio.Lock` makes contention explicit but doesn't reduce it. Two clients calling `navigate()` simultaneously still wait for each other. This is not a regression — it matches reality — but consumers who hoped "async = parallel" will be disappointed. Documentation must call this out clearly in the `AsyncDaemonClient` docstring and in the SAD §5.2 update.
- **Two source-of-truth client implementations (3a)** until v0.4 or until 3b is revisited. The sync `_rpc_once` body and the async `_rpc_once` body must be kept in lockstep on framing changes. Mitigation: a shared `_FRAMING` module containing JSON encode/decode + line-split helpers, exercised by both.
- **Forked-process + `asyncio.run` interaction is delicate.** The daemon already uses `os.fork()` + `os.setsid()` and redirects FDs 0/1/2 to `/dev/null` before calling `_run_daemon`. `asyncio.run()` after fork is supported on Linux/macOS but the loop must be created **after** the fork (never before — the parent's loop, if any, must not be inherited). The current code already constructs the server inside the child, so the structure is preserved; this is called out so future maintainers don't move loop creation to the parent.
- **Default executor sizing.** `loop.run_in_executor(None, ...)` uses Python's default `ThreadPoolExecutor(max_workers=min(32, os.cpu_count()+4))`. With the dispatch lock held, only one executor thread is ever active at a time, but multiple connections accumulating queued requests could theoretically spawn threads. This is acceptable because the dispatch lock bounds true parallelism to 1; we are not paying for unused threads.
- **Test fixtures must be updated.** `tests/test_daemon_integration.py` uses `_BrowserServer` directly. It must be parametrised across backends during the compatibility window, then collapse to async-only in v0.4. The `carbonyl-agent-qa-runner` fixture's wait-for-daemon loop already uses `is_daemon_live` (which doesn't care about the implementation), so no fixture change is required there.
- **#23 auto-reconnect must be re-implemented** in `AsyncDaemonClient` from scratch. This is straightforward — the retry policy is purely state-machine — but the test surface must be cloned. Estimated: ~5 new tests mirroring `TestAutoReconnect`, all using `asyncio` injection helpers.

### Neutral

- **Logging.** `_log` is the existing `get_logger` instance and is thread-safe; coroutines log into it without change.
- **Permissions.** `os.chmod(sock_path, 0o600)` and the parent-dir `0o700` from ADR-002 are unchanged; they are filesystem operations, not transport-level.
- **Wire protocol versioning.** `PROTOCOL_VERSION` does not bump because the wire is unchanged. If the async server later adds server-push commands (e.g. `subscribe page_changed`) that becomes a separate decision and bumps the version then.

## Alternatives Considered

### A. Status quo + `threading.Lock` around `_dispatch`

Closes SAD §11.2 but leaves §11.5 entirely open. Async consumers still have to thread-pool-wrap the SDK. **Rejected** as a partial fix that uses the maintenance budget on the easier half of the problem.

### B. `selectors`-based single-threaded server

Smaller surface than `asyncio`, no `await` keyword, no event loop. But it forces us to hand-roll framing, timeouts, and cancellation, and the client surface still has to be either sync-only or rewritten for async. **Rejected** because the saving (no `asyncio` import) does not justify the framework reinvention.

### C. Sync `DaemonClient` rewritten as a thread-bridged async wrapper (option 3b above)

Single source of truth for the wire protocol. But every sync call now blocks on `asyncio.run_coroutine_threadsafe(...).result()`, which means the sync facade always carries a background thread + private loop, and exception propagation across the bridge has well-known sharp edges (e.g. `CancelledError` inside `result()` becomes a `concurrent.futures.CancelledError`, which doesn't subclass the asyncio one in older Python versions). **Deferred** — option 3a is shipped first; 3b becomes a future cleanup once the async client is stable.

### D. `trio` / `anyio` structured concurrency

Materially better cancellation semantics than `asyncio` raw. But `trio` is not on the dependency list, and `anyio` would force consumers to use `anyio.run` or accept a bridge layer. SDK targets a wide audience of Python automation users; `asyncio` is the lowest common denominator. **Rejected** for this iteration; revisit if the cancellation story becomes painful.

### E. gRPC over the Unix socket

ADR-002 already considered and rejected this. Async-mode does not change that calculus. **Out of scope.**

### F. Drop daemon mode entirely; rely on warm browser caches

Re-architect so each call cold-starts Carbonyl. Eliminates the daemon's existence. **Rejected**: Chromium cold-start is multi-second; the daemon is the entire reason UC-005 ("run a persistent daemon") exists.

## Implementation Plan (Acceptance Criteria → Sequencing)

The work is split across at least three commits to keep each reviewable:

1. **Foundation: shared framing module** — extract `_encode_request`, `_decode_response`, line-split helpers from `daemon.py` into a new `_protocol.py`. Sync `DaemonClient` switches to it without any behaviour change. Existing tests must pass unchanged. **No new tests**; the regression suite is the contract.
2. **Async server (behind feature flag)** — add `_run_daemon_async`, `_AsyncBrowserServer`, the dispatch lock, and the executor offload. Gate behind `CARBONYL_DAEMON_BACKEND=asyncio`. Add an integration test parametrisation that runs the existing `tests/test_daemon_integration.py` suite against both backends (~25 existing tests × 2 = full coverage of the wire, both directions).
3. **`AsyncDaemonClient`** — new file `async_daemon.py` (or new class in `daemon.py`; module split TBD by reviewer). Mirrors every method on `DaemonClient`. Auto-reconnect cloned from cycle 2 of #23 with `asyncio.sleep` instead of `time.sleep`. New test class `TestAsyncDaemonClient` mirroring the existing `TestAutoReconnect` (~5 tests). Cross-version smoke test: sync client ↔ async server, async client ↔ threaded server.

The flag flip (default `threaded` → default `asyncio`) is a separate v0.3 release-prep commit. Threaded backend removal is a v0.4 commit. Both are mechanical at that point.

### Quality gate (acceptance criteria, mapped to issue body)

- [x] `DaemonServer` rewritten with `asyncio` event loop (Unix socket via `asyncio.start_unix_server`) — §1
- [x] Backward-compatible `DaemonClient` — sync wrapper over async internals — §3 (option 3a; 3b deferred)
- [x] New `AsyncDaemonClient` for native async consumers — §2
- [x] Concurrent client connections handled — §1; serialised at dispatch by an `asyncio.Lock`, which is the correct semantic
- [x] Existing daemon tests pass unchanged — Implementation Plan step 2; parametrisation runs the whole suite against both backends
- [x] New async-specific tests added — Implementation Plan step 3
- [x] ADR-005 documenting the sync→async migration decision — **this document, renumbered to ADR-006 because #42 took ADR-005**

## References

- `src/carbonyl_agent/daemon.py` lines 442–563 (current `socketserver.ThreadingUnixStreamServer` implementation)
- `src/carbonyl_agent/daemon.py` lines 254–319 (`_rpc` / `_rpc_once`; same framing as the async server will use)
- `src/carbonyl_agent/daemon.py` lines 639–714 (`start_daemon` fork + readiness probe — preserved verbatim)
- `tests/test_daemon_integration.py` (the regression suite that must keep passing)
- ADR-001 (PTY + pyte — explains why the browser is fundamentally single-actor and an `asyncio.Lock` is the correct model)
- ADR-002 (Unix-socket + line-delimited JSON — transport unchanged by this ADR)
- SAD §5.2 ("Daemon mode") — process model; will be updated to reflect the lock + executor offload
- SAD §11.2 ("Daemon concurrency") — closed by this decision
- SAD §11.5 ("Async daemon interface") — closed by this decision
- Python docs: `asyncio.start_unix_server`, `asyncio.run_coroutine_threadsafe`, `loop.run_in_executor`
- Issue #19 (US-019) — implementation tracker
- Issue #23 — auto-reconnect, exception family, stale-PID cleanup; all behaviours preserved

---

*End of ADR. Draft v0.1, awaiting review and sign-off before implementation begins.*
