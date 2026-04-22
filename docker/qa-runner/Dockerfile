# syntax=docker/dockerfile:1.7
#
# carbonyl-agent-qa-runner
#
# Runtime container for Phase 0+ Carbonyl automation QA. Hosts an Xorg
# server (switchable driver: dummy for CPU, modesetting for GPU) so
# Carbonyl with ozone_platform=x11 can render to a real X display while
# the agent SDK drives it via uinput from outside or inside the container.
# scrot/ffmpeg/x11vnc capture the rendered framebuffer alongside the
# terminal-mode text output.
#
# Reference: roctinam/carbonyl-agent#37
# Related: roctinam/carbonyl#58, #60 (Phase 0 tracker), #57 (x11 build)
# CI plan: roctinam/carbonyl/.aiwg/working/trusted-automation/09-ci-plan.md

FROM ubuntu:24.04 AS base

ARG DEBIAN_FRONTEND=noninteractive
ARG CARBONYL_RUNTIME_URL=""
# When a specific Carbonyl x11 runtime tarball URL is available
# (produced by roctinam/carbonyl#57 + build-runtime-x11.yml), pass it
# via --build-arg. Left empty during early W0.3 development so the image
# can be built and smoke-tested against a stand-in Carbonyl stub.

# --- System packages -------------------------------------------------------
# Split into focused groups for readability and layer caching.

# Xorg + drivers. dummy for CPU-only framebuffer, modesetting for GPU
# passthrough. Both installed; entrypoint picks one at runtime via
# CARBONYL_GPU_MODE.
RUN apt-get update && apt-get install -y --no-install-recommends \
      xserver-xorg-core \
      xserver-xorg-video-dummy \
      xserver-xorg-video-modesetting \
      xserver-xorg-input-evdev \
      xserver-xorg-input-libinput \
      xauth \
      xinit \
      x11-xserver-utils \
      && rm -rf /var/lib/apt/lists/*

# Capture tooling — scrot for single frames, ffmpeg for streams,
# x11vnc for remote operator display.
RUN apt-get update && apt-get install -y --no-install-recommends \
      scrot \
      ffmpeg \
      x11vnc \
      && rm -rf /var/lib/apt/lists/*

# Chromium runtime shared libraries. The Carbonyl binary is a Chromium
# headless_shell fork; it has the usual heavy runtime dep surface. These
# are runtime-only (no -dev packages); libnss3 pulls libnspr4 transitively.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 \
      libnspr4 \
      libasound2t64 \
      libexpat1 \
      libfontconfig1 \
      libdbus-1-3 \
      libglib2.0-0t64 \
      libxkbcommon0 \
      libxcomposite1 \
      libxdamage1 \
      libxfixes3 \
      libxrandr2 \
      libgbm1 \
      libegl1 \
      libgl1 \
      libpango-1.0-0 \
      libcairo2 \
      libatk1.0-0t64 \
      libatk-bridge2.0-0t64 \
      libcups2t64 \
      libdrm2 \
      libatspi2.0-0t64 \
      && rm -rf /var/lib/apt/lists/*

# Python runtime + dev tooling the agent SDK needs.
# gcc + libc-dev are needed to build python-uinput (C extension);
# we clean them up after install to keep the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 \
      python3-pip \
      python3-venv \
      python3-dev \
      gcc \
      libc-dev \
      linux-libc-dev \
      && rm -rf /var/lib/apt/lists/*

# uinput userspace support. The kernel module comes from the host via
# --device=/dev/uinput; the container only needs the Python bindings.
RUN pip3 install --break-system-packages --no-cache-dir \
      python-uinput

# Drop build tools now that python-uinput is compiled. Cuts ~120MB.
RUN apt-get purge -y --auto-remove gcc libc-dev python3-dev linux-libc-dev \
      && rm -rf /var/lib/apt/lists/*

# Misc utilities useful for CI debugging.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
      procps \
      psmisc \
      file \
      && rm -rf /var/lib/apt/lists/*

# --- Xorg configuration ----------------------------------------------------

COPY xorg.conf.d/ /etc/X11/xorg.conf.d/

# --- Entrypoint ------------------------------------------------------------

COPY entrypoint.sh /usr/local/bin/carbonyl-agent-qa-entrypoint
RUN chmod +x /usr/local/bin/carbonyl-agent-qa-entrypoint

# --- Carbonyl runtime ------------------------------------------------------
# Installed from a pre-built tarball when available. Until roctinam/
# carbonyl#57 produces an x11 runtime, a stub is left in place so the
# image builds and the entrypoint can be smoke-tested end-to-end.

RUN mkdir -p /opt/carbonyl
RUN if [ -n "$CARBONYL_RUNTIME_URL" ]; then \
      echo "Installing Carbonyl x11 runtime from $CARBONYL_RUNTIME_URL"; \
      curl -fsSL "$CARBONYL_RUNTIME_URL" -o /tmp/carbonyl.tar.gz; \
      # Tarball wraps files in a <triple>/ top-level dir; strip it so
      # /opt/carbonyl/carbonyl is the executable path without hardcoding
      # the triple in PATH.
      tar -xzf /tmp/carbonyl.tar.gz -C /opt/carbonyl --strip-components=1; \
      rm /tmp/carbonyl.tar.gz; \
      ls -lh /opt/carbonyl/carbonyl /opt/carbonyl/libcarbonyl.so 2>/dev/null || \
        { echo "ERROR: expected carbonyl binary + libcarbonyl.so missing after extract"; exit 1; }; \
    else \
      echo '#!/bin/sh' > /opt/carbonyl/carbonyl; \
      echo 'echo "carbonyl stub: no x11 runtime installed. Provide --build-arg CARBONYL_RUNTIME_URL or bind-mount a real runtime at /opt/carbonyl/"' >> /opt/carbonyl/carbonyl; \
      echo 'exit 0' >> /opt/carbonyl/carbonyl; \
      chmod +x /opt/carbonyl/carbonyl; \
    fi
ENV PATH="/opt/carbonyl:${PATH}"

# --- Non-root user ---------------------------------------------------------
# Automation runs as `agent`. Xorg requires access to /dev/input/*
# (via evdev) and /dev/uinput — we add the `input` group explicitly.

RUN groupadd -r agent && useradd -r -g agent -G input -m -s /bin/bash agent

USER agent
WORKDIR /home/agent

# --- Runtime metadata ------------------------------------------------------

ENV DISPLAY=:99
ENV CARBONYL_GPU_MODE=auto
# auto detects /dev/dri/card0; cpu forces dummy driver;
# gpu requires modesetting + /dev/dri passthrough

ENTRYPOINT ["/usr/local/bin/carbonyl-agent-qa-entrypoint"]
CMD ["bash"]

# --- Build instructions ----------------------------------------------------
#
#   docker build -t carbonyl-agent-qa-runner:local \
#     --build-arg CARBONYL_RUNTIME_URL=https://... \
#     docker/qa-runner/
#
# --- Run patterns ----------------------------------------------------------
#
# CPU mode (works anywhere):
#   docker run --rm --device=/dev/uinput --group-add input \
#     -e CARBONYL_GPU_MODE=cpu \
#     carbonyl-agent-qa-runner:local
#
# GPU mode (requires /dev/dri passthrough):
#   docker run --rm --device=/dev/uinput --group-add input \
#     --device=/dev/dri --gpus all \
#     -e CARBONYL_GPU_MODE=gpu \
#     carbonyl-agent-qa-runner:local
#
# Auto mode:
#   docker run --rm --device=/dev/uinput --group-add input \
#     --device=/dev/dri:/dev/dri \
#     carbonyl-agent-qa-runner:local
