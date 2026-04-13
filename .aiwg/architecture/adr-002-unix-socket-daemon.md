# ADR-002: Daemon IPC via Unix Domain Sockets

**Status**: Accepted
**Date**: 2026-04-09
**Version**: 1.0 (Baselined)
**Deciders**: Joseph Magly (sole maintainer)

---

## Context

`carbonyl-agent` supports a "daemon mode" in which a Carbonyl browser is started once and kept alive in the background, so multiple client calls (or multiple processes) can attach without losing in-memory state, cookies, or an authenticated login. Starting Chromium is expensive (several seconds of cold start plus any login ceremony), and many automation workflows — scraping a logged-in dashboard, running a long-lived agent — benefit from reusing a single browser process across calls.

The daemon requires an inter-process communication mechanism. Candidate options:

1. **Unix domain sockets with line-delimited JSON** — local-only, filesystem-permission gated, no extra deps.
2. **TCP socket on localhost** — simple, cross-platform, but exposes a port that requires explicit authentication.
3. **gRPC over a Unix socket** — strongly-typed schema, streaming support.
4. **HTTP + JSON over a Unix socket** — leverage Python's standard library `http.server`.
5. **ZeroMQ / nanomsg** — richer messaging patterns (pub/sub, req/rep).

The SDK's design goals (ADR context, see SAD §2) emphasise **minimal dependencies** and **local-only execution**. There is no current requirement for remote access, streaming, or multi-client pub/sub.

## Decision

The daemon will expose its API as **newline-delimited JSON over a Unix domain socket**, implemented with Python's standard-library `socketserver.ThreadingUnixStreamServer` (`src/carbonyl_agent/daemon.py` lines 270–281) and a bespoke `DaemonClient` using `socket.AF_UNIX` directly (daemon.py lines 88–175).

Access control is delegated entirely to filesystem permissions on the socket path:

```
~/.local/share/carbonyl/sessions/<name>.sock
```

The socket lives inside the session directory (already user-owned, mode 0700 by default via the user's umask). There is **no wire-level authentication**, **no TLS**, **no capability tokens** — anyone with read/write access to the session directory can connect to the daemon.

The wire protocol is deliberately simple: one JSON object per line, request/response, documented in the module docstring (daemon.py lines 14–28):

```json
{"cmd": "navigate", "url": "https://example.com"}
{"ok": true, "result": null}
```

## Consequences

### Positive

- **Zero extra dependencies**: Uses only `socket`, `socketserver`, `json`, `threading` from the Python standard library. No gRPC toolchain, no protobuf compiler.
- **Strong local boundary**: Unix sockets cannot be reached across the network. An attacker would need local filesystem access to the user's `~/.local/share/carbonyl/sessions/` directory — at which point they have already won anyway.
- **Trivial liveness check**: `is_daemon_live` (daemon.py lines 68–81) attempts a one-second connect; if the connect fails, the stale socket file is unlinked on the spot, so the next `start_daemon` is clean.
- **Easy debugging**: A developer can `socat UNIX-CONNECT:<path>` and hand-type JSON commands; the `attach` REPL (daemon.py lines 457–505) exercises the same wire protocol.
- **Simple wire evolution**: Adding a new command means adding one `elif` in `_dispatch` (daemon.py lines 211–267). No schema migration.

### Negative

- **Not remotely accessible**: A user who wants to drive a daemon on a remote host must tunnel the socket (SSH `-L`, `socat`) or re-architect. This is accepted — ADR scope is local SDK usage.
- **No authentication beyond filesystem permissions**: If a user `chmod`s the session directory open, any local user can attach. Documentation must warn against this.
- **Per-connection threading with a shared `CarbonylBrowser`**: `ThreadingUnixStreamServer` with `daemon_threads=True` spawns a thread per client connection (daemon.py lines 270–271), but all threads dispatch into the same browser instance without a lock. Concurrent clients can race. Mitigation today: document "one client at a time." Long-term: add a `threading.Lock` around `_dispatch` (tracked in SAD §11).
- **JSON overhead**: Large `raw_lines` responses (up to 50 × 220 chars per full screen) serialise and parse as JSON on every call. Acceptable for the expected load.

### Neutral

- The socket path lives alongside session metadata (daemon.py line 64: `root / f"{session_name}{_SOCK_SUFFIX}"`), tightly coupling daemon state to session state. This is intentional: one daemon per session, discoverable from the session name alone.

## Alternatives Considered

- **TCP localhost** (Option 2): rejected — exposes a port, requires binding to 127.0.0.1, and introduces the question of authentication. Filesystem permissions are a better boundary.
- **gRPC over Unix socket** (Option 3): rejected — adds `grpcio`, `protobuf`, `.proto` tooling, code generation steps. Massive dependency expansion for a single-developer SDK.
- **HTTP over Unix socket** (Option 4): rejected — more framing overhead than a line-delimited protocol, and `http.server` is awkward to drive from a simple client.
- **ZeroMQ** (Option 5): rejected — richer messaging patterns unused, adds a native dependency.

## References

- `src/carbonyl_agent/daemon.py` lines 14–28 (wire protocol documentation)
- `src/carbonyl_agent/daemon.py` lines 88–175 (`DaemonClient`)
- `src/carbonyl_agent/daemon.py` lines 182–281 (`_BrowserHandler`, `_BrowserServer`)
- `src/carbonyl_agent/daemon.py` lines 354–400 (`start_daemon` fork/detach logic)
- ADR-001 (PTY + pyte terminal emulation — explains why the daemon holds a single `CarbonylBrowser`)
