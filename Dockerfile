FROM python:3.12-slim

ARG PUID=1000
ARG PGID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app \
    DATA_DIR=/app/data \
    XDG_CACHE_HOME=/app/data/cache \
    HF_HOME=/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/app/data/cache/fastembed \
    npm_config_cache=/app/.npm

# System deps. tmux is required by Cookbook for background downloads/serves.
# openssh-client is required for Cookbook remote server tests and setup.
# git/cmake are required when Cookbook builds llama.cpp inside Docker.
# nodejs/npm provide npx for optional built-in MCP servers.
# gosu supports legacy root startup that repairs bind-mount ownership.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    curl \
    git \
    nodejs \
    npm \
    tmux \
    openssh-client \
    gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN if ! getent group "${PGID}" >/dev/null 2>&1; then groupadd -g "${PGID}" cleverly; fi \
    && if ! getent passwd "${PUID}" >/dev/null 2>&1; then useradd -u "${PUID}" -g "${PGID}" -M -s /usr/sbin/nologin -d /app cleverly; fi

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code.
COPY . .

# Create runtime directories. These are owned by the app UID so rootless,
# read-only Compose runs can start without a privileged chown pass.
RUN mkdir -p \
        data \
        logs \
        .ssh \
        .cache/huggingface \
        .local \
        .npm \
        services/cache/search \
        services/cache/content \
    && chown -R "${PUID}:${PGID}" /app

# Supports both hardened rootless Compose runs and legacy root runs that need a
# one-time bind-mount ownership repair before dropping privileges.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]
