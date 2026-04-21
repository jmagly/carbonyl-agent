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
trap "kill $XORG_PID 2>/dev/null || true" EXIT

exec "$@"
