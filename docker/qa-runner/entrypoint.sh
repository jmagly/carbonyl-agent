#!/bin/bash
#
# carbonyl-agent-qa-runner entrypoint
#
# Starts Xorg on :99 with the appropriate driver based on CARBONYL_GPU_MODE,
# exports DISPLAY=:99, then execs the container command.
#
# CARBONYL_GPU_MODE:
#   auto — detect /dev/dri/card0; gpu if present + readable, else cpu
#   cpu  — force dummy driver (CPU-only framebuffer)
#   gpu  — require modesetting + /dev/dri passthrough; fail if absent
#
# Xorg log: /tmp/xorg.log (tailed on failure)

set -euo pipefail

MODE="${CARBONYL_GPU_MODE:-auto}"
XORG_DISPLAY="${XORG_DISPLAY:-:99}"
XORG_LOG="/tmp/xorg.log"
XORG_READY_TIMEOUT="${XORG_READY_TIMEOUT:-10}"  # seconds

# --- udev daemon for Xorg hot-plug (#52) -----------------------------------
# Xorg uses libudev's NETLINK_KOBJECT_UEVENT subscription to detect input
# devices that appear after Xorg has already started — including the
# uinput virtual devices that UinputEmitter creates at test time. The
# subscription is per-netns: container netns gets uevents only for
# devices created by a udevd running inside the same netns.
#
# Without a container-side udevd, runtime-created uinput devices never
# register with Xorg and KEY_*/ABS_X/ABS_Y events go nowhere. (Mouse
# button events sometimes leak through because Carbonyl's libcarbonyl
# bridge has its own button-event path; treat that as accidental, not
# a workaround.)
#
# udevd needs root to bind kernel netlink and manage device nodes. When
# the container runs in non-root mode (CARBONYL_RUN_MODE=nonroot), udev
# can't start and uinput-trust tests that depend on hot-plug will be
# affected. Log clearly so it's not a silent failure mode.

UDEVD_BIN="/lib/systemd/systemd-udevd"
UDEV_LOG="/tmp/udevd.log"

if [[ -x "$UDEVD_BIN" ]] && [[ "$(id -u)" == "0" ]]; then
  if ! pgrep -x systemd-udevd >/dev/null 2>&1; then
    # CARBONYL_UDEVD_DEBUG=1 launches udevd with verbose logging so worker-
    # level errors surface in /tmp/udevd.log. Defaults off in production
    # (noisy) but enabled by carbonyl-agent#54 cycle 3 to investigate why
    # workers never broadcast UDEV[] events despite a clean daemon start.
    UDEVD_FLAGS=(--daemon)
    if [[ "${CARBONYL_UDEVD_DEBUG:-0}" == "1" ]]; then
      UDEVD_FLAGS+=(--debug)
      echo "[entrypoint] systemd-udevd: debug logging enabled (CARBONYL_UDEVD_DEBUG=1)" >&2
    fi
    echo "[entrypoint] starting systemd-udevd for X hot-plug" >&2
    "$UDEVD_BIN" "${UDEVD_FLAGS[@]}" > "$UDEV_LOG" 2>&1 || {
      echo "[entrypoint] WARNING: systemd-udevd failed to start; runtime input hot-plug disabled" >&2
    }
    # Settle initial device tree so /run/udev/data/ is populated before
    # Xorg subscribes. udevadm trigger fires synthetic add events for
    # existing nodes; udevadm settle waits for the queue to drain.
    udevadm trigger --action=add 2>/dev/null || true
    udevadm settle --timeout=10 2>/dev/null || true
  fi
  echo "[entrypoint] udev hot-plug enabled — uinput devices will register dynamically with X" >&2
else
  if [[ "$(id -u)" != "0" ]]; then
    echo "[entrypoint] WARNING: running as $(id -un) (uid $(id -u)); udev cannot start." >&2
    echo "  Runtime-created uinput devices will NOT register with Xorg, so" >&2
    echo "  uinput keyboard / motion events will not reach Chromium (#52)." >&2
    echo "  Click events may still work via Carbonyl's button-event path." >&2
    echo "  Fix: rerun with CARBONYL_RUN_MODE=root (or --user 0)." >&2
  else
    echo "[entrypoint] WARNING: $UDEVD_BIN not found; install 'udev' package" >&2
  fi
fi

# --- Resolve CPU vs GPU mode -----------------------------------------------

if [[ "$MODE" == "auto" ]]; then
  if [[ -e /dev/dri/card0 && -r /dev/dri/card0 ]]; then
    MODE=gpu
  else
    MODE=cpu
  fi
  echo "[entrypoint] CARBONYL_GPU_MODE=auto resolved to: $MODE" >&2
fi

# --- Start Xorg ------------------------------------------------------------

case "$MODE" in
  cpu)
    XORG_CONFIG=/etc/X11/xorg.conf.d/10-dummy.conf
    CHROMIUM_GL_FLAGS="--use-gl=angle --use-angle=swiftshader --disable-gpu-compositing"
    echo "[entrypoint] starting Xorg with dummy driver (CPU framebuffer)" >&2
    ;;
  gpu)
    if [[ ! -e /dev/dri/card0 ]]; then
      echo "[entrypoint] ERROR: CARBONYL_GPU_MODE=gpu but /dev/dri/card0 absent." >&2
      echo "  Run with: docker run --device=/dev/dri --gpus all ..." >&2
      exit 1
    fi
    XORG_CONFIG=/etc/X11/xorg.conf.d/10-modesetting.conf
    CHROMIUM_GL_FLAGS="--use-gl=angle --use-angle=default --enable-gpu-rasterization"
    echo "[entrypoint] starting Xorg with modesetting driver (GPU)" >&2
    ;;
  *)
    echo "[entrypoint] ERROR: CARBONYL_GPU_MODE must be auto|cpu|gpu (got: $MODE)" >&2
    exit 1
    ;;
esac

# Xorg needs an Xauth file to start cleanly (even though we're not using
# network auth). Create an empty one for the agent user.
XAUTH_FILE=/tmp/.Xauth-agent
touch "$XAUTH_FILE"
export XAUTHORITY="$XAUTH_FILE"

# -noreset: don't exit when the last X client disconnects
# -nolisten tcp: bind only to Unix socket; network access not wanted
# +extension GLX +extension RANDR +extension RENDER: Chromium expects these
Xorg "$XORG_DISPLAY" \
  -config "$XORG_CONFIG" \
  -noreset \
  -nolisten tcp \
  +extension GLX +extension RANDR +extension RENDER \
  > "$XORG_LOG" 2>&1 &

XORG_PID=$!

# --- Wait for Xorg to open its socket --------------------------------------

X_SOCKET="/tmp/.X11-unix/X${XORG_DISPLAY#:}"
for ((i=0; i<XORG_READY_TIMEOUT*10; i++)); do
  if [[ -S "$X_SOCKET" ]]; then
    break
  fi
  if ! kill -0 "$XORG_PID" 2>/dev/null; then
    echo "[entrypoint] ERROR: Xorg process died during startup." >&2
    echo "--- Xorg log tail ---" >&2
    tail -n 40 "$XORG_LOG" >&2
    exit 1
  fi
  sleep 0.1
done

if [[ ! -S "$X_SOCKET" ]]; then
  echo "[entrypoint] ERROR: Xorg socket $X_SOCKET did not appear within ${XORG_READY_TIMEOUT}s." >&2
  echo "--- Xorg log tail ---" >&2
  tail -n 40 "$XORG_LOG" >&2
  exit 1
fi

# --- Environment for child process -----------------------------------------

export DISPLAY="$XORG_DISPLAY"
export CARBONYL_GL_FLAGS="$CHROMIUM_GL_FLAGS"

echo "[entrypoint] Xorg ready on $DISPLAY (mode=$MODE, pid=$XORG_PID)" >&2

# --- /dev/input/ mount-namespace mitigation (carbonyl-agent#120) -----------
# Docker's container /dev is a regular tmpfs, not devtmpfs, so kernel
# device-node creation for uinput-registered devices does not propagate.
# /sys/class/input/eventN exists inside the container (sysfs is host-shared)
# but /dev/input/eventN does not — udev/Xorg have nothing to bind to.
#
# Closed #52 / #54 fixed udevd liveness; this is a separate mount-namespace
# gap. Mitigation: mknod nodes for container-CREATED virtual (uinput)
# devices only — filter by checking /sys/devices/virtual/input/ ancestry,
# so host hardware visible via shared sysfs is NOT exposed in /dev.
#
# Boot-time once-pass plus a background watcher to handle devices created
# at test time by UinputEmitter.open().

if [[ "$(id -u)" == "0" ]] && command -v sync-virtual-input >/dev/null 2>&1; then
  /usr/local/bin/sync-virtual-input --once 2>&1 | grep -v '^$' >&2 || true
  /usr/local/bin/sync-virtual-input --watch >/tmp/sync-virtual-input.log 2>&1 &
  SVI_PID=$!
  echo "[entrypoint] sync-virtual-input watcher started (pid=$SVI_PID)" >&2
fi

# --- Re-trigger input udev events post-Xorg (carbonyl-agent#54 cycle 8) ----
# Xorg's libudev hot-plug listener may not be receiving runtime add events
# for /dev/input/* devices despite Option "AutoAddDevices" "true". Cycle 7
# verified the udev pipeline works end-to-end (workers process, broadcast
# UDEV[] events, /run/udev/data/ populated, /dev/input/eventN accessible
# via bind-mount) but Phase D `xinput list` still doesn't show any input
# devices — including the host's hardware visible through the bind mount.
# Synthesize add events for the entire input subsystem so Xorg's listener
# (whether or not it auto-subscribed at startup) receives a fresh
# enumeration. Idempotent — safe to run unconditionally.
if [[ "$(id -u)" == "0" ]] && command -v udevadm >/dev/null 2>&1; then
  echo "[entrypoint] re-triggering input udev events post-Xorg-start" >&2
  udevadm trigger --action=add --subsystem-match=input 2>/dev/null || true
  udevadm settle --timeout=5 2>/dev/null || true
fi

# Quick sanity check: xinput list works against the running X server.
# If this fails, X is up but its IPC is broken — we fail loudly so the
# operator knows before tests start emitting.
if command -v xinput >/dev/null 2>&1; then
  if ! xinput list >/dev/null 2>&1; then
    echo "[entrypoint] WARNING: xinput list failed against $DISPLAY — input subsystem may be misconfigured" >&2
  fi
fi

# --- /dev/uinput accessibility check ---------------------------------------
# Common gotcha: Docker's --device passthrough preserves the host node's
# ownership/ACL, which inside the container resolves to root:root. A non-root
# container user (like our 'agent') needs either:
#   (a) host udev rule making /dev/uinput group-readable by the 'input' group,
#       matching GID between host and container; or
#   (b) container run as root (--user 0); or
#   (c) no uinput emission (agent drives from outside the container).
# We warn clearly if (a)/(b) isn't met; emission will fail loudly at use time.

if [[ -e /dev/uinput ]]; then
  if [[ ! -w /dev/uinput ]]; then
    echo "[entrypoint] WARNING: /dev/uinput not writable for user $(id -un) ($(id -u))." >&2
    echo "  Container sees: $(ls -la /dev/uinput | awk '{print $1, $3, $4}')" >&2
    echo "  Container groups: $(id -G -n)" >&2
    echo "  Fix options:" >&2
    echo "    (a) Host udev rule: echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-uinput.rules && sudo udevadm control --reload && sudo udevadm trigger" >&2
    echo "    (b) Run container as root: docker run --user 0 ..." >&2
    echo "    (c) Match host input GID: docker run --group-add \$(getent group input | cut -d: -f3 on host) ..." >&2
    echo "  uinput-based tests will fail until resolved; non-uinput paths unaffected." >&2
  else
    echo "[entrypoint] /dev/uinput writable — uinput emission available." >&2
  fi
else
  echo "[entrypoint] NOTE: /dev/uinput not present. Pass --device=/dev/uinput if you need trusted input." >&2
fi

echo "[entrypoint] exec: $*" >&2

# --- Graceful shutdown -----------------------------------------------------
# Kill Xorg when the container's main command exits, so the container
# stops cleanly instead of lingering on Xorg.
trap "kill ${XORG_PID:-} ${SVI_PID:-} 2>/dev/null || true" EXIT

exec "$@"
