# carbonyl-agent-qa-runner

Runtime container for Phase 0+ QA of the Carbonyl Trusted Automation Initiative. Hosts Xorg so `ozone_platform=x11` Carbonyl can render to a real framebuffer, with uinput passthrough for agent-driven input and capture tooling for observability.

## Three supported runtime modes

Trusted-input browser QA against carbonyl supports three runtimes. They are all first-class — pick the one that matches your isolation and ergonomic requirements.

| Mode | Isolation | Trusted-input completeness | When to use | Known constraint |
|---|---|---|---|---|
| **Bare metal** | None (host Xorg / uinput) | Full — host udev binds runtime uinput devices to host Xorg | Solo dev box; trusted environment; iterating on SDK code | Host hardware is visible to the test; not for multi-tenant |
| **Docker (this repo)** | Container netns + uid | Partial — `/dev/input/event*` populated via `sync-virtual-input` mknod helper (commit `5b3fa6e`); Xorg binding requires the worker-spawn fix tracked in `roctinam/carbonyl-agent#121` | Sandboxed local QA where Xorg binding isn't required (smoke tests, non-trusted-input paths, click-via-libcarbonyl tests) | systemd-udevd in the container netns does not spawn workers on Docker 29.4.3 + kernel 6.17 + the default seccomp profile — full Xorg binding is deferred to the VM path |
| **VM (`agentic-sandbox` `browser-qa` loadout)** | Full kernel isolation | Full — operator-validated 7/7 acceptance in agentic-sandbox v2026.5.5 | Trusted-input pipelines, CI runs, untrusted-site automation | Higher resource cost; cold-start latency for fresh VMs |

**Quick chooser**:
- "I just need to run the SDK against Chromium with `isTrusted=true` events" → **VM**
- "I want a quick sandboxed smoke test of carbonyl rendering / non-trusted paths" → **Docker**
- "I'm iterating on `UinputEmitter` or `CarbonylBrowser` code" → **Bare metal**

The rest of this README covers the Docker mode. For VM, see [`agentic-sandbox` `browser-qa` loadout](https://github.com/jmagly/agentic-sandbox/blob/main/images/qemu/loadouts/profiles/browser-qa.yaml) and [`scripts/validate-browser-qa.sh`](https://github.com/jmagly/agentic-sandbox/blob/main/scripts/validate-browser-qa.sh). For bare metal, run the SDK directly with `carbonyl-agent install` and a host-side Xorg (no special setup beyond the host `99-uinput.rules` rule documented below).

## What's inside

- **Xorg core** plus `dummy` (CPU) and `modesetting` (GPU) video drivers; entrypoint picks one based on `CARBONYL_GPU_MODE`
- **evdev + libinput** input drivers wired via `/etc/X11/xorg.conf.d/20-evdev-input.conf` — reads any `/dev/input/event*` including uinput virtual devices
- **Capture tools**: `scrot` (single frames), `ffmpeg` (streams), `x11vnc` (remote display)
- **Python 3 + `python-uinput`**: enough to drive the agent SDK from inside the container
- **Carbonyl x11 runtime** at `/opt/carbonyl/carbonyl` — fetched at image build time via `--build-arg CARBONYL_RUNTIME_URL=...`. Use a public GitHub release asset URL when available, or pass an explicitly managed internal URL for private x11 runtime cuts; a stub is left in place if no URL is passed so the image builds standalone

## Pull (preferred)

The image is published to GHCR by `.github/workflows/build-qa-runner.yml` (issue #38) on every push to `main` that touches `docker/qa-runner/**` or `.carbonyl-runtime-version`.

```bash
docker pull ghcr.io/jmagly/carbonyl-agent-qa-runner:latest

# Or pin to a specific runtime tag (matches .carbonyl-runtime-version):
docker pull ghcr.io/jmagly/carbonyl-agent-qa-runner:runtime-v0.2.0-alpha.17

# Or pin to a specific repo commit:
docker pull ghcr.io/jmagly/carbonyl-agent-qa-runner:sha-<short-sha>
```

Use the published image unless you're iterating on the Dockerfile itself or a network egress to the registry isn't available.

## Build

The repo ships `.carbonyl-runtime-version` at its root (issue #39) and a wrapper
script that reads it and passes the right URL to `docker build`. Use the wrapper
unless you have a specific reason not to.

```bash
# Pin-driven (preferred) — reads .carbonyl-runtime-version automatically.
docker/qa-runner/build.sh

# Override the hash for a one-off build:
CARBONYL_RUNTIME_HASH=<hex> docker/qa-runner/build.sh

# Override the full URL (e.g. local mirror):
CARBONYL_RUNTIME_URL=https://my-mirror/.../x86_64-unknown-linux-gnu.tgz \
  docker/qa-runner/build.sh

# Custom image tag:
docker/qa-runner/build.sh my-tag:dev

# Manual fallback (equivalent to what build.sh does):
docker build -t carbonyl-agent-qa-runner:local \
  --build-arg CARBONYL_RUNTIME_URL=https://github.com/jmagly/carbonyl/releases/download/<tag>/<asset>.tgz \
  docker/qa-runner/

# Stub runtime — useful for smoke-testing the entrypoint / Xorg without the heavy tarball.
docker build -t carbonyl-agent-qa-runner:local docker/qa-runner/
```

## Run

### Quickest — the wrapper handles everything

```bash
cd docker/qa-runner
./run.sh                          # interactive bash
./run.sh pytest tests/layer1       # run a command
CARBONYL_GPU_MODE=gpu ./run.sh    # force GPU mode
CARBONYL_RUN_MODE=root ./run.sh   # force root mode (default on hosts without udev rule)
```

`run.sh` auto-detects whether the host has the `99-uinput.rules` udev rule installed:

- **No udev rule** → `--user 0` (root in container), `--device=/dev/uinput`. Zero host setup; works anywhere.
- **Udev rule installed** (via `sudo scripts/setup-uinput-host.sh`) → non-root `agent` user, `--group-add <host-input-gid>`. Tighter isolation.

### What works inside Docker (and what doesn't)

This image is the right tool for sandboxed smoke tests and any path that does **not** require Xorg to bind runtime-created uinput devices. Per `roctinam/carbonyl-agent#121`, on Docker 29.4.3 + kernel 6.17 + the default seccomp profile, `systemd-udevd` inside the container netns does not spawn workers — so `KERNEL[]` events fire but no `UDEV[]` events are broadcast, and Xorg's libudev subscription never sees the new device. The mount-namespace gap (`/dev/input/event*` not populated) is closed by the `sync-virtual-input` mknod helper shipped in commit `5b3fa6e`; the worker-spawn gap is not.

| Capability | Bare metal | Docker (this repo) | VM (`browser-qa`) |
|---|---|---|---|
| `xset q` / Xorg startup | ✅ | ✅ | ✅ |
| `scrot` / `ffmpeg` / `x11vnc` capture | ✅ | ✅ | ✅ |
| `python-uinput` device creation | ✅ | ✅ | ✅ |
| `/dev/input/event*` materialized | ✅ | ✅ (mknod helper) | ✅ |
| `xinput list` shows runtime uinput devices | ✅ | ❌ (`#121`) | ✅ |
| `UinputEmitter.click()` reaches Chromium | ✅ | ⚠️ via libcarbonyl button-event path only | ✅ |
| `UinputEmitter.type_text()` / `mouse_path()` | ✅ | ❌ (`#121`) | ✅ |
| `tests/layer1` trusted-input suite passes | ✅ | ❌ (`#121`) | ✅ |
| Host hardware isolation | ❌ | ✅ | ✅ |
| Multi-tenant safe | ❌ | ⚠️ container-level | ✅ |

**Bottom line for Docker**: use it for what works above, and use the VM path for anything that requires Xorg-bound trusted input. Both are supported; pick the one matching your test's needs.

### root vs nonroot operator mode (Docker only)

Independently of the `#121` worker-spawn issue, the Docker mode supports two operator-account configurations:

| Mode | `uinput` device write access | When to use |
|---|---|---|
| **root** (default if no host udev rule) | ✅ via `--user 0` | Zero host setup, works anywhere |
| **nonroot** (host udev rule installed) | ✅ via `--group-add <host-input-gid>` | Tighter operator isolation |

`run.sh` auto-detects which mode the host is set up for. Neither mode bypasses `#121`'s Xorg-binding limitation.

### Compose

```bash
# Default service: root mode, works anywhere
docker compose up

# Non-root (requires one-time host setup, below)
HOST_INPUT_GID=$(getent group input | cut -d: -f3) \
  docker compose --profile nonroot up runner-nonroot
```

### Raw docker (lowest-level)

```bash
# Simplest — root mode, works anywhere
docker run --rm --device=/dev/uinput -e CARBONYL_GPU_MODE=cpu --user 0 \
  carbonyl-agent-qa-runner:local

# Non-root (requires 99-uinput.rules on host)
docker run --rm --device=/dev/uinput \
  --group-add "$(getent group input | cut -d: -f3)" \
  -e CARBONYL_GPU_MODE=cpu \
  carbonyl-agent-qa-runner:local

# GPU mode
docker run --rm --device=/dev/uinput --device=/dev/dri --gpus all \
  --user 0 -e CARBONYL_GPU_MODE=gpu \
  carbonyl-agent-qa-runner:local
```

## What the entrypoint does

1. Reads `CARBONYL_GPU_MODE` (default `auto`); resolves `auto` to `gpu` or `cpu` based on `/dev/dri/card0` presence
2. Starts Xorg on `:99` with the appropriate config (writes log to `/tmp/xorg.log`)
3. Waits up to 10s for the `/tmp/.X11-unix/X99` socket to appear; fails hard with log tail if it doesn't
4. Exports `DISPLAY=:99` and `CARBONYL_GL_FLAGS` (ANGLE backend selection)
5. `exec`s the container's command
6. On exit, kills Xorg cleanly via `trap`

## Smoke test

```bash
docker run --rm --device=/dev/uinput --group-add input \
  -e CARBONYL_GPU_MODE=cpu \
  carbonyl-agent-qa-runner:local \
  bash -c '
    echo "--- Xorg alive? ---"
    xset q > /dev/null && echo OK || { echo FAIL; exit 1; }

    echo "--- scrot produces output? ---"
    scrot /tmp/out.png
    file /tmp/out.png | grep -q "PNG image" && echo OK || { echo FAIL; exit 1; }

    echo "--- uinput accessible? ---"
    python3 -c "import uinput; print(\"python-uinput OK\")"

    echo "--- carbonyl stub responds? ---"
    carbonyl 2>&1 | head -1

    echo "ALL SMOKE CHECKS PASSED"
  '
```

## CI wiring

Image is published to GHCR at `ghcr.io/jmagly/carbonyl-agent-qa-runner:sha-<7>` by a `build-builder.yml` workflow (to be added; mirrors the `carbonyl-builder` pattern in `jmagly/carbonyl/docs/ci-cd-plan.md`). Downstream workflows pin to the SHA tag, never `latest`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Xorg socket did not appear` | Missing xkb data or evdev driver conflict | `tail /tmp/xorg.log`; often a permissions issue on shared host |
| `/dev/uinput: Permission denied` | Host udev doesn't group the device correctly; `--group-add input` alone isn't enough | See §"Host uinput setup" below — needs a host udev rule **and** aligned GIDs, OR `--user 0` |
| `CARBONYL_GPU_MODE=gpu but /dev/dri/card0 absent` | GPU mode requested but no passthrough | Add `--device=/dev/dri --gpus all` to `docker run` |
| `carbonyl stub: no x11 runtime installed` | Image built without `CARBONYL_RUNTIME_URL` | Rebuild with `--build-arg CARBONYL_RUNTIME_URL=https://github.com/jmagly/carbonyl/releases/download/<tag>/<asset>.tgz` |

## Host uinput setup — OPTIONAL (only for non-root operator mode)

The default path (`./run.sh`, `docker compose up`, or `docker run --user 0`) works on any host without setup. If you want the tighter-isolation non-root mode, run the one-command installer on the host once:

```bash
sudo scripts/setup-uinput-host.sh
```

What it does (idempotent; safe to re-run):

1. Verifies the kernel has `uinput` support (loads module + persists via `/etc/modules-load.d/uinput.conf`)
2. Ensures the `input` group exists on the host
3. Installs `/etc/udev/rules.d/99-uinput.rules` (shipped in `docker/qa-runner/host-setup/`)
4. Reloads udev + retriggers `/dev/uinput`
5. Prints next steps (add yourself to `input` group; host input GID for `--group-add`)

After that, `./run.sh` auto-detects the setup and switches to non-root mode. You can also use it manually:

```bash
HOST_INPUT_GID=$(getent group input | cut -d: -f3)
docker run --rm \
  --device=/dev/uinput \
  --group-add "$HOST_INPUT_GID" \
  -e CARBONYL_GPU_MODE=cpu \
  carbonyl-agent-qa-runner:local
```

Passing the GID numerically (instead of `--group-add input`) bypasses the container-side vs host-side name resolution mismatch — the agent user ends up with supplemental membership in the group that owns the host device.

### Why the two modes exist

Docker's `--device=/dev/uinput` passes the raw device node into the container but does **not** preserve host ACLs. Out of the box, `/dev/uinput` in the container appears as `root:root` mode 660:

- `--user 0` mode (default in `run.sh` / compose) → root inside container, has access. Simple.
- Non-root mode → requires the udev rule above, plus matching GID alignment via `--group-add <host-gid>`.

The `run.sh` wrapper hides this distinction: it picks whichever mode the host is set up for. Compose's `runner` service (default) uses root; `runner-nonroot` profile uses the group-add approach.

**If you can't modify host udev and don't want root**: the agent SDK can emit uinput from *outside* the container (on the host) and the Carbonyl-inside-the-container still sees the events via X, because Xorg reads `/dev/input/eventN` from the shared kernel. This is actually the canonical Phase 0 pattern — W0.4's tests do exactly that.

## Status (2026-05-20)

- ✅ **Three runtime modes supported.** Bare metal, Docker (this repo), and VM (`agentic-sandbox` `browser-qa` loadout, shipped in v2026.5.5). Pick per the chooser above.
- ✅ **Carbonyl x11 runtime ships.** `jmagly/carbonyl#57` and `#63` (X-mirror) closed in `v0.2.0-alpha.3`. Use the matching GitHub release asset as `CARBONYL_RUNTIME_URL`. With `CARBONYL_X_MIRROR=1` set, the runtime mirrors compositor frames into a real X window so `scrot`/`ffmpeg`/`x11vnc` capture works alongside the terminal render.
- ✅ **Mount-namespace fix shipped (`#120` first half).** `sync-virtual-input` mknod helper (commit `5b3fa6e`) populates `/dev/input/event*` for runtime-created uinput devices in the container. Host hardware is filtered out — only `/sys/devices/virtual/input/`-rooted devices are exposed.
- ⚠️ **Xorg-binding limitation in Docker (`#121`).** systemd-udevd in the container netns does not spawn workers on this Docker/kernel combination, so Xorg never sees the new devices. Documented as a Docker-mode constraint; the VM mode covers the same workload without the constraint. Probing for the minimum capability set that fixes the worker spawn was abandoned after the privileged-flag probe destabilized the host kernel.
- ✅ **End-to-end validation runs in CI.** `jmagly/carbonyl/scripts/test-x-mirror.sh` exercises both pipelines (terminal SGR stream + X framebuffer pixel histogram) inside this image on every `build-runtime.yml` x11 build. See commit `eee943d`.
- 🔵 **Image-publish workflow** is the remaining piece — `jmagly/carbonyl-agent#35` CI track. Today the image is built locally / inline by `build-runtime.yml`'s validation step.
- **Image size**: ~1.5 GB with the real x11 runtime included.

## Reference

- Issue: `roctinam/carbonyl-agent#37` (closed)
- Phase 0 tracker: `roctinam/carbonyl#60`
- X-mirror feature: `roctinam/carbonyl#63` (closed)
- Operator reference: `roctinam/carbonyl/docs/runtime-modes.md`
- CI plan: `roctinam/carbonyl/.aiwg/working/trusted-automation/09-ci-plan.md`
- ADR-002 rev 2: `roctinam/carbonyl/docs/adr-002-trusted-input-approach.md`
