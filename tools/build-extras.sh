#!/bin/bash
# Builds what PipeDream can't download on Linux, into its data folder, where it finds them:
#   - stable-diffusion.cpp's sd-server with CUDA, for this machine's NVIDIA GPU. Mage-Flow, the default painter, only
#     renders on CUDA for now, and upstream publishes no Linux CUDA build. Needs the CUDA toolkit (nvcc).
#   - dfrotz, for `pipedream-gui zork`, unless Frotz is already installed.
# Also needs git, make, a C/C++ compiler, and cmake (or uv, to fetch it). The CUDA build takes 10-30 minutes.
# Usage: tools/build-extras.sh
set -euo pipefail

SD_COMMIT=a1ded76  # keep in step with _SD in src/pipedream/runtime.py
FROTZ_COMMIT=042d7bc

data="${XDG_DATA_HOME:-$HOME/.local/share}/pipedream"
log="$data/build-extras.log"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$data"
: > "$log"

nvcc=$(command -v nvcc || ls /opt/cuda/bin/nvcc /usr/local/cuda/bin/nvcc 2>/dev/null | head -1 || true)
if [ -z "$nvcc" ]; then
  echo "nvcc not found: install the CUDA toolkit (cuda on Arch, nvidia-cuda-toolkit on Debian/Ubuntu)" >&2
  exit 1
fi
cmake=(cmake)
command -v cmake > /dev/null || cmake=(uvx --from cmake cmake)

echo "Building sd-server with CUDA for $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1) (10-30 minutes)..."
git clone -q https://github.com/leejet/stable-diffusion.cpp "$work/sd"
git -C "$work/sd" checkout -q "$SD_COMMIT"
git -C "$work/sd" submodule -q update --init --recursive
if ! { "${cmake[@]}" -S "$work/sd" -B "$work/sd/build" -DCMAKE_BUILD_TYPE=Release -DSD_CUDA=ON \
         -DCMAKE_CUDA_COMPILER="$nvcc" -DCMAKE_CUDA_ARCHITECTURES=native -DGGML_CUDA_NCCL=OFF &&
       "${cmake[@]}" --build "$work/sd/build" -j "$(nproc)"; } >> "$log" 2>&1; then
  tail -25 "$log"
  echo "The CUDA build failed; the full log is in $log" >&2
  exit 1
fi
mkdir -p "$data/runtimes/sd-cuda"
cp "$work/sd/build/bin/sd-server" "$work/sd/build/bin/sd-cli" "$data/runtimes/sd-cuda/"
if ! "$data/runtimes/sd-cuda/sd-server" --list-devices 2>/dev/null | grep -q '^CUDA'; then
  rm -rf "$data/runtimes/sd-cuda"
  echo "The CUDA build can't see the GPU, so PipeDream will keep using Vulkan" >&2
  exit 1
fi
echo "Installed $data/runtimes/sd-cuda"

if command -v dfrotz > /dev/null; then
  echo "Frotz is already installed ($(command -v dfrotz))"
else
  echo "Building dfrotz..."
  git clone -q https://gitlab.com/DavidGriffith/frotz.git "$work/frotz"
  git -C "$work/frotz" checkout -q "$FROTZ_COMMIT"
  make -s -C "$work/frotz" dumb -j"$(nproc)" >> "$log" 2>&1 || { tail -25 "$log"; exit 1; }
  install -D -m 755 "$work/frotz/dfrotz" "$data/bin/dfrotz"
  echo "Installed $data/bin/dfrotz"
fi
