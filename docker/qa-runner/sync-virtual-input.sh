#!/bin/bash
#
# sync-virtual-input — populate /dev/input/event* nodes for container-created
# virtual (uinput) devices.
#
# Docker's container /dev is a regular tmpfs, not devtmpfs. When the kernel
# creates a device node for a uinput-registered device, the node appears in
# the HOST's devtmpfs but does NOT propagate into the container's /dev
# mount-namespace. /sys/class/input/eventN exists inside the container (sysfs
# is host-shared), but /dev/input/eventN does not — so udev/Xorg have nothing
# to bind to. (Closed #52 / #54 addressed udevd liveness; #120 addresses this
# mount-namespace gap.)
#
# This script mknod's the missing nodes by reading sysfs metadata. To avoid
# exposing host hardware (which is also visible in /sys/class/input/ because
# sysfs is host-shared), it only synthesizes nodes whose canonical sysfs path
# is under /sys/devices/virtual/input/ — i.e. uinput-backed devices created
# inside the container.
#
# Modes:
#   --once    populate once and exit (used by entrypoint at boot)
#   --watch   populate, then poll /sys/class/input/ at ~5Hz and mknod new
#             nodes as they appear. Used as a background daemon.
#
# Exit 0 on success. Errors logged to stderr.

set -euo pipefail

MODE="${1:-once}"
INTERVAL="${SYNC_VIRTUAL_INPUT_INTERVAL:-0.2}"
LOG_PREFIX="[sync-virtual-input]"

ensure_dir() {
  if [[ ! -d /dev/input ]]; then
    mkdir -p /dev/input
    chmod 755 /dev/input
  fi
}

# Returns 0 if the given /sys/class/input/eventN path is a virtual (uinput)
# device — by checking whether the canonical sysfs path is under
# /sys/devices/virtual/input/.
is_virtual() {
  local sysfs_path="$1"
  local real
  real="$(readlink -f "$sysfs_path" 2>/dev/null || true)"
  [[ "$real" == /sys/devices/virtual/input/* ]]
}

# mknod a single /dev/input/eventN from its sysfs major:minor metadata.
# Idempotent.
mknod_one() {
  local name="$1"
  local sysfs_path="/sys/class/input/$name"
  local target="/dev/input/$name"
  [[ -e "$target" ]] && return 0
  [[ -e "$sysfs_path/dev" ]] || return 0
  local devstr
  devstr="$(cat "$sysfs_path/dev" 2>/dev/null || true)"
  [[ -z "$devstr" ]] && return 0
  local major="${devstr%:*}"
  local minor="${devstr#*:}"
  if mknod "$target" c "$major" "$minor" 2>/dev/null; then
    chmod 660 "$target" 2>/dev/null || true
    chgrp input "$target" 2>/dev/null || true
    echo "$LOG_PREFIX created $target (c $major $minor)" >&2
    # Fire a udev add event for this specific node so Xorg's libudev
    # subscription picks it up and the InputClass matchers bind a driver.
    # Without this re-trigger, the node exists in /dev but Xorg never
    # learns about it because the kernel uevent for the device fired
    # before /dev/input/ existed inside the container.
    if command -v udevadm >/dev/null 2>&1; then
      udevadm trigger --action=add "$sysfs_path" 2>/dev/null || true
    fi
    return 0
  fi
  return 1
}

populate() {
  ensure_dir
  local count=0
  for sysfs_path in /sys/class/input/event*; do
    [[ -e "$sysfs_path" ]] || continue
    is_virtual "$sysfs_path" || continue
    local name
    name="$(basename "$sysfs_path")"
    if mknod_one "$name"; then
      count=$((count + 1))
    fi
  done
  if [[ "$count" -gt 0 ]]; then
    echo "$LOG_PREFIX synced $count virtual device node(s)" >&2
  fi
}

case "$MODE" in
  --once|once)
    populate
    ;;
  --watch|watch)
    populate
    echo "$LOG_PREFIX watching /sys/class/input/ for new virtual devices (interval=${INTERVAL}s)" >&2
    while true; do
      sleep "$INTERVAL"
      populate
    done
    ;;
  *)
    echo "$LOG_PREFIX usage: sync-virtual-input [--once|--watch]" >&2
    exit 2
    ;;
esac
