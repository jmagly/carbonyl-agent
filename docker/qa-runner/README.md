# carbonyl-agent-qa-runner

Runtime container for Phase 0+ QA of the Carbonyl Trusted Automation Initiative. Hosts Xorg so `ozone_platform=x11` Carbonyl can render to a real framebuffer, with uinput passthrough for agent-driven input and capture tooling for observability.

## What's inside

- **Xorg core** plus `dummy` (CPU) and `modesetting` (GPU) video drivers; entrypoint picks one based on `CARBONYL_GPU_MODE`
- **evdev + libinput** input drivers wired via `/etc/X11/xorg.conf.d/20-evdev-input.conf` — reads any `/dev/input/event*` including uinput virtual devices
- **Capture tools**: `scrot` (single frames), `ffmpeg` (streams), `x11vnc` (remote display)
- **Python 3 + `python-uinput`**: enough to drive the agent SDK from inside the container
- **Carbonyl x11 runtime** at `/opt/carbonyl/carbonyl` — fetched at image build time via `--build-arg CARBONYL_RUNTIME_URL=...`. The runtime ships as `runtime-x11-<hash>` Gitea releases (e.g. `runtime-x11-dd69bef0ea4b2512` for current main); a stub is left in place if no URL is passed so the image builds standalone

## Build

```bash
# Real x11 runtime — preferred. URL points at any runtime-x11-<hash> Gitea release.
docker build -t carbonyl-agent-qa-runner:local \
  --build-arg CARBONYL_RUNTIME_URL=https://git.integrolabs.net/roctinam/carbonyl/releases/download/runtime-x11-dd69bef0ea4b2512/x86_64-unknown-linux-gnu.tgz \
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

Image is published to the Gitea container registry at `git.integrolabs.net/roctinam/carbonyl-agent-qa-runner:sha-<7>` by a `build-builder.yml` workflow (to be added; mirrors the `carbonyl-builder` pattern in `roctinam/carbonyl/docs/ci-cd-plan.md`). Downstream workflows pin to the SHA tag, never `latest`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Xorg socket did not appear` | Missing xkb data or evdev driver conflict | `tail /tmp/xorg.log`; often a permissions issue on shared host |
| `/dev/uinput: Permission denied` | Host udev doesn't group the device correctly; `--group-add input` alone isn't enough | See §"Host uinput setup" below — needs a host udev rule **and** aligned GIDs, OR `--user 0` |
| `CARBONYL_GPU_MODE=gpu but /dev/dri/card0 absent` | GPU mode requested but no passthrough | Add `--device=/dev/dri --gpus all` to `docker run` |
| `carbonyl stub: no x11 runtime installed` | Image built without `CARBONYL_RUNTIME_URL` | Rebuild with `--build-arg CARBONYL_RUNTIME_URL=https://git.integrolabs.net/roctinam/carbonyl/releases/download/runtime-x11-<hash>/x86_64-unknown-linux-gnu.tgz` |

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

## Status (2026-04-29)

- ✅ **Carbonyl x11 runtime ships.** `roctinam/carbonyl#57` and `#63` (X-mirror) closed in `v0.2.0-alpha.3`. Use `runtime-x11-<hash>` Gitea releases as `CARBONYL_RUNTIME_URL`. With `CARBONYL_X_MIRROR=1` set, the runtime mirrors compositor frames into a real X window so `scrot`/`ffmpeg`/`x11vnc` capture works alongside the terminal render.
- ✅ **End-to-end validation runs in CI.** `roctinam/carbonyl/scripts/test-x-mirror.sh` exercises both pipelines (terminal SGR stream + X framebuffer pixel histogram) inside this image on every `build-runtime.yml` x11 build. See commit `eee943d`.
- 🔵 **Image-publish workflow** is the remaining piece — `roctinam/carbonyl-agent#35` CI track. Today the image is built locally / inline by `build-runtime.yml`'s validation step.
- **Image size**: ~1.5 GB with the real x11 runtime included.

## Reference

- Issue: `roctinam/carbonyl-agent#37` (closed)
- Phase 0 tracker: `roctinam/carbonyl#60`
- X-mirror feature: `roctinam/carbonyl#63` (closed)
- Operator reference: `roctinam/carbonyl/docs/runtime-modes.md`
- CI plan: `roctinam/carbonyl/.aiwg/working/trusted-automation/09-ci-plan.md`
- ADR-002 rev 2: `roctinam/carbonyl/docs/adr-002-trusted-input-approach.md`
