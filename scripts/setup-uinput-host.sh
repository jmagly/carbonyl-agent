#!/bin/bash
#
# setup-uinput-host.sh
#
# One-command host setup for non-root uinput access inside containers.
# Idempotent — safe to re-run.
#
# What it does:
#   1. Verifies the kernel has uinput support (loads module if needed)
#   2. Ensures an `input` group exists on the host
#   3. Installs the 99-uinput.rules udev rule
#   4. Reloads udev and retriggers /dev/uinput
#   5. Prints the host `input` GID to pass to docker --group-add
#
# Only needed if you want to run `carbonyl-agent-qa-runner` as a non-root
# container user AND have uinput emission work inside the container.
# For CI or quick tests, `--user 0` on the container avoids all of this.
#
# Usage:
#   sudo ./scripts/setup-uinput-host.sh
#   OR
#   scripts/setup-uinput-host.sh   # prompts for sudo

set -euo pipefail

RULE_SRC="$(dirname "$0")/../docker/qa-runner/host-setup/99-uinput.rules"
RULE_DST="/etc/udev/rules.d/99-uinput.rules"

need_sudo() {
  if [[ $EUID -ne 0 ]]; then
    echo "[setup-uinput-host] re-exec with sudo (needs root for udev rules)"
    exec sudo -E "$0" "$@"
  fi
}

check_uinput_module() {
  if [[ ! -e /dev/uinput ]]; then
    echo "[setup-uinput-host] /dev/uinput not present — loading kernel module"
    modprobe uinput || {
      echo "[setup-uinput-host] ERROR: 'modprobe uinput' failed."
      echo "  Your kernel likely lacks uinput support."
      echo "  On Ubuntu/Debian: usually built-in. If missing, install a different kernel."
      echo "  On minimal distros: install linux-modules-extra or equivalent."
      exit 1
    }
  fi
  # Ensure it persists across reboots.
  if [[ ! -f /etc/modules-load.d/uinput.conf ]]; then
    echo "[setup-uinput-host] making uinput auto-load at boot (/etc/modules-load.d/uinput.conf)"
    echo "uinput" > /etc/modules-load.d/uinput.conf
  fi
}

ensure_input_group() {
  if ! getent group input > /dev/null; then
    echo "[setup-uinput-host] creating 'input' group (missing on this host)"
    groupadd -r input
  fi
  local gid
  gid=$(getent group input | cut -d: -f3)
  echo "[setup-uinput-host] host 'input' GID: $gid"
}

install_rule() {
  if [[ ! -f "$RULE_SRC" ]]; then
    echo "[setup-uinput-host] ERROR: expected $RULE_SRC not found."
    echo "  Run this script from the carbonyl-agent repo root."
    exit 1
  fi

  if [[ -f "$RULE_DST" ]] && cmp -s "$RULE_SRC" "$RULE_DST"; then
    echo "[setup-uinput-host] $RULE_DST already up-to-date"
    return 0
  fi

  echo "[setup-uinput-host] installing $RULE_DST"
  cp "$RULE_SRC" "$RULE_DST"
  chmod 644 "$RULE_DST"
}

reload_udev() {
  echo "[setup-uinput-host] reloading udev + retriggering /dev/uinput"
  udevadm control --reload
  udevadm trigger /dev/uinput
  sleep 0.5
}

verify() {
  local perms
  perms=$(ls -l /dev/uinput 2>/dev/null || echo "missing")
  if [[ "$perms" == "missing" ]]; then
    echo "[setup-uinput-host] ERROR: /dev/uinput disappeared after reload. Check 'dmesg | tail'."
    exit 1
  fi
  local group
  group=$(stat -c '%G' /dev/uinput)
  if [[ "$group" != "input" ]]; then
    echo "[setup-uinput-host] WARNING: /dev/uinput group is '$group', expected 'input'."
    echo "  ls -l /dev/uinput: $perms"
    echo "  The rule may have failed to apply. Try: sudo udevadm trigger --action=add /dev/uinput"
    exit 1
  fi
  echo "[setup-uinput-host] verified: $perms"
}

print_next_steps() {
  local gid
  gid=$(getent group input | cut -d: -f3)
  local me="${SUDO_USER:-$(whoami)}"

  echo
  echo "[setup-uinput-host] ✔ setup complete."
  echo
  echo "Next steps:"
  echo "  1. Add yourself to the 'input' group (for local non-Docker use):"
  echo "       sudo usermod -aG input $me"
  echo "       # re-login or 'newgrp input' to pick up the group"
  echo
  echo "  2. For Docker containers, pass the host input GID numerically:"
  echo "       docker run --device=/dev/uinput --group-add $gid ..."
  echo "     (This bypasses container vs host input-group GID mismatch.)"
  echo
  echo "  3. carbonyl-agent-qa-runner convenience:"
  echo "       cd docker/qa-runner && ./run.sh           # auto-picks best mode"
  echo "       cd docker/qa-runner && docker compose up  # compose variant"
  echo
}

main() {
  need_sudo "$@"
  check_uinput_module
  ensure_input_group
  install_rule
  reload_udev
  verify
  print_next_steps
}

main "$@"
