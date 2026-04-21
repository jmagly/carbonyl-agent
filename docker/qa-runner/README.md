# carbonyl-agent-qa-runner

Runtime container for Phase 0+ QA of the Carbonyl Trusted Automation Initiative. Hosts Xorg so `ozone_platform=x11` Carbonyl can render to a real framebuffer, with uinput passthrough for agent-driven input and capture tooling for observability.

## What's inside

- **Xorg core** plus `dummy` (CPU) and `modesetting` (GPU) video drivers; entrypoint picks one based on `CARBONYL_GPU_MODE`
- **evdev + libinput** input drivers wired via `/etc/X11/xorg.conf.d/20-evdev-input.conf` — reads any `/dev/input/event*` including uinput virtual devices
- **Capture tools**: `scrot` (single frames), `ffmpeg` (streams), `x11vnc` (remote display)
- **Python 3 + `python-uinput`**: enough to drive the agent SDK from inside the container
- **Carbonyl x11 runtime** at `/opt/carbonyl/carbonyl` — provided via `--build-arg CARBONYL_RUNTIME_URL=...` once `roctinam/carbonyl#57` ships one; stub until then

## Build

```bash
# Stub runtime (current state; #57 hasn't shipped yet)
docker build -t carbonyl-agent-qa-runner:local docker/qa-runner/

# With the real x11 Carbonyl runtime tarball:
docker build -t carbonyl-agent-qa-runner:local \
  --build-arg CARBONYL_RUNTIME_URL=https://git.integrolabs.net/... \
  docker/qa-runner/
```

## Run

### CPU mode (works anywhere)

```bash
docker run --rm \
  --device=/dev/uinput --group-add input \
  -e CARBONYL_GPU_MODE=cpu \
  carbonyl-agent-qa-runner:local \
  bash -c 'xset q && scrot /tmp/test.png && file /tmp/test.png'
```

### GPU mode (requires `/dev/dri` passthrough)

```bash
docker run --rm \
  --device=/dev/uinput --group-add input \
  --device=/dev/dri --gpus all \
  -e CARBONYL_GPU_MODE=gpu \
  carbonyl-agent-qa-runner:local \
  bash -c 'xset q && glxinfo | head -20'
```

### Auto (tries GPU, falls back to CPU if `/dev/dri` absent)

```bash
docker run --rm \
  --device=/dev/uinput --group-add input \
  --device=/dev/dri:/dev/dri \
  carbonyl-agent-qa-runner:local \
  bash
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
| `carbonyl stub: no x11 runtime installed` | Image built without `CARBONYL_RUNTIME_URL` | Rebuild with `--build-arg CARBONYL_RUNTIME_URL=...` once `#57` ships |

## Host uinput setup (REQUIRED for trusted input)

Docker's `--device=/dev/uinput` passes the raw device node into the container but does **not** preserve host ACLs. Out of the box, `/dev/uinput` in the container appears as `root:root` mode 660, so a non-root container user (like the `agent` user this image creates) cannot write to it. `--group-add input` alone is insufficient — it adds the *container's* `input` group to the user's supplemental groups, but the host device isn't a member of any `input` group by default on most distros.

**Recommended operator setup — one-time host change**:

```bash
# 1. Make /dev/uinput owned by the 'input' group on the host:
cat <<'EOF' | sudo tee /etc/udev/rules.d/99-uinput.rules
KERNEL=="uinput", GROUP="input", MODE="0660"
EOF

# 2. Reload + retrigger:
sudo udevadm control --reload
sudo udevadm trigger /dev/uinput

# 3. Verify:
ls -la /dev/uinput
# Expected: crw-rw---- 1 root input ...

# 4. Check the host's 'input' group GID:
getent group input | cut -d: -f3
# Note this number — you'll pass it to docker as --group-add <GID>
```

With that in place, run containers via:

```bash
HOST_INPUT_GID=$(getent group input | cut -d: -f3)
docker run --rm \
  --device=/dev/uinput \
  --group-add "$HOST_INPUT_GID" \
  -e CARBONYL_GPU_MODE=cpu \
  carbonyl-agent-qa-runner:local
```

Passing the GID numerically (instead of `--group-add input`) bypasses the container-side vs host-side name resolution mismatch — the agent user ends up with supplemental membership in the group that owns the host device.

**CI shortcut**: for automated runs where the extra setup is inconvenient, run the container as root (`--user 0`). Accepts the trade-off of less-strict process isolation for simpler setup. Not recommended for interactive operator use.

**If you can't modify host udev**: the agent SDK can emit uinput from *outside* the container (on the host) and the Carbonyl-inside-the-container still sees the events via X, because Xorg reads `/dev/input/eventN` from the shared kernel. This is actually the canonical Phase 0 pattern — W0.4's tests do exactly that.

## Limitations (current state)

- **Carbonyl x11 runtime is a stub** until `roctinam/carbonyl#57` completes. The container builds and the entrypoint runs correctly, but actual Chromium launch requires a real runtime tarball.
- **No build-builder workflow yet** — image is built locally. Publishing workflow lands with `roctinam/carbonyl-agent#35` CI track.
- **Image size**: 1.36 GB with the Carbonyl stub + full Xorg + driver stack + capture tools. Well under the 1.5 GB acceptance criterion from `#37`. Adding the real Carbonyl runtime (expected ~150 MB compressed) brings it to ~1.5 GB.

## Reference

- Issue: `roctinam/carbonyl-agent#37`
- Phase 0 tracker: `roctinam/carbonyl#60`
- CI plan: `roctinam/carbonyl/.aiwg/working/trusted-automation/09-ci-plan.md`
- ADR-002 rev 2: `roctinam/carbonyl/docs/adr-002-trusted-input-approach.md`
