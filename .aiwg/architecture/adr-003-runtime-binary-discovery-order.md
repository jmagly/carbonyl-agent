# ADR-003: Runtime Binary Discovery Order

**Status**: Accepted
**Date**: 2026-04-09
**Version**: 1.0 (Baselined)
**Deciders**: Joseph Magly (sole maintainer)

---

## Context

`carbonyl-agent` is a Python package that drives an external Carbonyl native binary. The binary can reach a user's system through several paths:

- Downloaded by `carbonyl-agent install` to a well-known user-local directory
- Built from source manually and placed on `$PATH`
- Pointed to by an explicit environment variable (e.g. for CI, custom builds, or testing an unreleased runtime)
- Not installed at all — in which case a Docker image is available as a fallback

The SDK must pick one deterministically on every invocation. Consumers need predictable behaviour: a developer who installs a custom build should be able to override whatever else is on the system, and a fresh user who has done nothing should still be able to `pip install carbonyl-agent && python -c "import carbonyl_agent; ..."` and see something work.

## Decision

`_local_binary()` in `src/carbonyl_agent/browser.py` (lines 79–107) implements a fixed four-step discovery order. `CarbonylBrowser.open()` (lines 201–248) uses its result to decide whether to spawn a local binary or fall back to Docker:

1. **`CARBONYL_BIN` environment variable** — explicit path override. Checked first. Must point to an executable file.
2. **`~/.local/share/carbonyl/bin/<triple>/carbonyl`** — the installed location written by `carbonyl-agent install` (install.py lines 60–115). `<triple>` is computed by `_platform_triple()` (e.g. `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin`).
3. **`carbonyl` on `$PATH`** — resolved via `which carbonyl`. Honors any manual installation the user has done.
4. **Docker fallback** — if steps 1–3 all return `None`, `open()` constructs a `docker run --rm -it fathyb/carbonyl ...` command line and spawns that through pexpect instead (browser.py lines 227–248).

## Consequences

### Positive

- **Explicit override always wins**: Setting `CARBONYL_BIN=/path/to/custom` is the escape hatch for CI, local development of the upstream Carbonyl binary, or testing patched builds. No flag juggling required.
- **Installer path takes precedence over `$PATH`**: If the user runs `carbonyl-agent install`, that binary is used even if a different Carbonyl is on `$PATH`. This avoids surprise version skew when `pip install -U carbonyl-agent && carbonyl-agent install` bumps the runtime.
- **Graceful first-run experience**: A developer who never runs the installer but has Docker available still gets a working `CarbonylBrowser` — the SDK transparently shells out to `docker run fathyb/carbonyl`. This is important for smoke tests and README snippets.
- **`LD_LIBRARY_PATH` is scoped**: When a local binary is used, the SDK sets `LD_LIBRARY_PATH=<binary_dir>` (browser.py line 217) so the adjacent `libcarbonyl.so` resolves without requiring `ldconfig` or system-wide `.so` placement.

### Negative

- **Silent fallback can mask misconfiguration**: If the user *intended* to use the installed binary but installed it to the wrong triple directory, discovery silently falls through to `$PATH` (and possibly Docker). A `--verbose` or warning mode would help. Today, the `log()` calls in `open()` (browser.py lines 211, 218, 228) are the only signal.
- **Docker fallback changes semantics**: Docker-mode sessions require volume mounts for the `--user-data-dir` path (browser.py lines 237–241), and the `_HEADLESS_FLAGS` list is dropped because the Docker image has its own baked-in entrypoint (line 234). Behaviour between local and Docker modes is therefore not bit-identical.
- **Platform-triple mismatch**: `_platform_triple()` returns `unknown-linux-gnu` on all non-Darwin systems (browser.py lines 68–76), which matches Rust's target-triple convention. Musl systems (Alpine) will not find the installed binary and will fall through to `$PATH` or Docker. This is acceptable since no musl builds exist upstream yet.
- **Race with `pexpect.spawn`**: Discovery is re-done on every `open()` call. A binary deleted between calls can produce a confusing error. Acceptable — the SDK is not expected to be resilient against active filesystem tampering.

### Neutral

- The order is not user-configurable. If a user wants `$PATH` to take precedence over the installed location, they must unset/remove the installed binary or set `CARBONYL_BIN` explicitly. This is a deliberate simplicity/flexibility trade-off.

## Alternatives Considered

- **`$PATH` first**: rejected because it breaks the "I just ran `carbonyl-agent install`" expectation. Users who install via the CLI expect that installer to be the source of truth.
- **Configuration file (`~/.config/carbonyl-agent/config.toml`)**: rejected for v0.x. Adds complexity (file parsing, schema, precedence rules) for a problem that an env var already solves.
- **Bundling the binary in the wheel**: rejected — see ADR-004.
- **Refusing Docker fallback and erroring out loudly**: rejected — the Docker fallback is a deliberate feature for first-run smoke testing, even at the cost of slight behavioural divergence.

## References

- `src/carbonyl_agent/browser.py` lines 68–107 (`_platform_triple`, `_local_binary`)
- `src/carbonyl_agent/browser.py` lines 201–248 (`CarbonylBrowser.open` with fallback)
- `src/carbonyl_agent/install.py` lines 60–115 (`cmd_install` install destination)
- `README.md` "Binary Search Order" section
- ADR-004 (runtime distribution via Gitea releases)
