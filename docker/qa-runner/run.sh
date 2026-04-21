#!/bin/bash
#
# run.sh — convenience wrapper for carbonyl-agent-qa-runner
#
# Auto-detects the host's uinput setup and picks the right `docker run`
# flags. Sane default: if host has the 99-uinput.rules udev rule in place,
# use the non-root agent user with --group-add <host-input-gid>. Otherwise,
# fall back to --user 0 so things just work.
#
# Override with CARBONYL_RUN_MODE env:
#   auto   — detect and pick (default)
#   root   — force --user 0
#   nonroot — force non-root; fail if host udev rule missing
#
# GPU: set CARBONYL_GPU_MODE=auto|cpu|gpu (default: auto).
#
# Examples:
#   ./run.sh                      # interactive bash in the container
#   ./run.sh pytest tests/layer1  # run a specific command
#   CARBONYL_RUN_MODE=root ./run.sh bash   # force root mode

set -euo pipefail

IMAGE="${CARBONYL_RUNNER_IMAGE:-carbonyl-agent-qa-runner:local}"
RUN_MODE="${CARBONYL_RUN_MODE:-auto}"
GPU_MODE="${CARBONYL_GPU_MODE:-auto}"

# --- detect host /dev/uinput situation -------------------------------------

host_has_udev_rule() {
  # Heuristic: /dev/uinput group is 'input' means rule is live.
  [[ -e /dev/uinput ]] && [[ "$(stat -c '%G' /dev/uinput 2>/dev/null)" == "input" ]]
}

host_input_gid() {
  getent group input 2>/dev/null | cut -d: -f3
}

# --- pick mode --------------------------------------------------------------

case "$RUN_MODE" in
  auto)
    if host_has_udev_rule; then
      RUN_MODE=nonroot
      echo "[run] host 99-uinput.rules detected → non-root mode"
    else
      RUN_MODE=root
      echo "[run] host 99-uinput.rules NOT installed → root mode (simpler)"
      echo "[run]   to switch to non-root: sudo scripts/setup-uinput-host.sh"
    fi
    ;;
  nonroot)
    if ! host_has_udev_rule; then
      echo "[run] ERROR: CARBONYL_RUN_MODE=nonroot but /dev/uinput not group 'input'."
      echo "  Run: sudo scripts/setup-uinput-host.sh"
      exit 1
    fi
    ;;
  root)
    ;;
  *)
    echo "[run] ERROR: CARBONYL_RUN_MODE must be auto|root|nonroot (got: $RUN_MODE)"
    exit 1
    ;;
esac

# --- assemble docker flags --------------------------------------------------

DOCKER_FLAGS=(
  --rm
  --device=/dev/uinput
  -e "CARBONYL_GPU_MODE=$GPU_MODE"
)

# GPU device if present (auto-handled by entrypoint if absent)
if [[ -e /dev/dri/card0 ]]; then
  DOCKER_FLAGS+=(--device=/dev/dri:/dev/dri)
fi

# Interactive TTY if stdin is a terminal
if [[ -t 0 ]]; then
  DOCKER_FLAGS+=(-it)
fi

case "$RUN_MODE" in
  root)
    DOCKER_FLAGS+=(--user 0)
    ;;
  nonroot)
    GID="$(host_input_gid)"
    if [[ -z "$GID" ]]; then
      echo "[run] ERROR: host 'input' group not found. Run: sudo scripts/setup-uinput-host.sh"
      exit 1
    fi
    DOCKER_FLAGS+=(--group-add "$GID")
    ;;
esac

# --- execute ---------------------------------------------------------------

echo "[run] image=$IMAGE  run_mode=$RUN_MODE  gpu_mode=$GPU_MODE"
echo "[run] docker run ${DOCKER_FLAGS[*]} $IMAGE ${*:-<default CMD>}"

exec docker run "${DOCKER_FLAGS[@]}" "$IMAGE" "$@"
