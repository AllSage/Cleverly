#!/bin/sh
# Runtime entrypoint for both modes:
# - hardened Compose runs this as PUID:PGID on a read-only root filesystem
# - legacy/root runs can still repair bind-mount ownership before dropping
#   privileges with gosu
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

offline_value="$(printf '%s' "${CLEVERLY_OFFLINE:-}" | tr '[:upper:]' '[:lower:]')"
if [ "$offline_value" != "1" ] && [ "$offline_value" != "true" ] && [ "$offline_value" != "yes" ] && [ "$offline_value" != "on" ]; then
    if [ "${CLEVERLY_ALLOW_NETWORK:-}" != "I_ACCEPT_NETWORK_RISK" ]; then
        echo "ERROR: Cleverly Docker runtime is offline-only by default." >&2
        echo "Set CLEVERLY_OFFLINE=1, or set CLEVERLY_ALLOW_NETWORK=I_ACCEPT_NETWORK_RISK to bypass this guard intentionally." >&2
        exit 64
    fi
fi

if [ "$(id -u)" = "0" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" cleverly
    fi
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -M -s /bin/sh -d /app cleverly
    fi

    for dir in /app/data /app/logs /app/.ssh /app/.cache /app/.local /app/.npm; do
        if [ -d "$dir" ]; then
            find "$dir" -not -uid "$PUID" -print0 2>/dev/null \
                | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
        fi
    done
fi

for dir in /app/data /app/logs /app/.ssh /app/.cache /app/.local /app/.npm /tmp/cleverly-tmux; do
    mkdir -p "$dir" 2>/dev/null || true
done

# Cookbook installs vllm/etc. via `pip install --user`, which pulls
# nvidia-cuda-* wheels into /app/.local but does not set CUDA_HOME or symlink
# /usr/local/cuda. Auto-set CUDA_HOME if a pip-installed nvcc is present.
for cu in \
    /app/.local/lib/python*/site-packages/nvidia/cu13 \
    /app/.local/lib/python*/site-packages/nvidia/cu12 \
    /app/.local/lib/python*/site-packages/nvidia/cuda_nvcc; do
    if [ -x "$cu/bin/nvcc" ]; then
        export CUDA_HOME="$cu"
        break
    fi
done

# Disable only the FlashInfer JIT sampler. It requires nvcc + matching CUDA
# headers at startup and does not affect the attention path.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

if [ "$(id -u)" = "0" ]; then
    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
