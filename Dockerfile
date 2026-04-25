# Forest Soul Forge — production daemon image.
#
# Separate from Dockerfile.test on purpose: test image is deps-only with source
# bind-mounted, this one bakes the source in and installs the package cleanly
# so the container is self-contained and reproducible.
#
# Design decisions (for audit):
#   * python:3.12-slim — same base as test image, predictable wheel availability
#     on linux/amd64 and linux/arm64, ~50MB smaller than the full python image.
#   * Non-root user (fsf, uid 1000) — matches common host UIDs so bind-mounted
#     ./data stays writable without chown gymnastics. If your host UID differs,
#     override via docker compose run --user "$(id -u):$(id -g)".
#   * Only [daemon] extras installed, not [dev]. pytest/mypy have no business
#     in a runtime image.
#   * PYTHONUNBUFFERED=1 so uvicorn's stdout reaches `docker logs` in real
#     time instead of buffering behind stdio.
#   * HEALTHCHECK hits /healthz so compose can gate downstream services and
#     `docker ps` shows liveness without ambiguity.
#   * Binds 0.0.0.0 INSIDE the container — the container's network namespace
#     is isolated; host exposure is controlled by compose's port mapping,
#     which we pin to 127.0.0.1 there. This preserves the local-first posture
#     while letting uvicorn actually receive traffic from the host.

FROM python:3.12-slim AS base

# curl is needed for HEALTHCHECK; nothing else system-level is required.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. uid 1000 matches the default host UID on Linux/macOS desktop
# setups. Bind-mounted ./data will be owned by this uid; override at runtime
# if your host uid differs.
RUN useradd --create-home --uid 1000 --shell /bin/bash fsf

WORKDIR /app

# Copy dependency metadata first so the pip layer caches cleanly across
# source-only edits. Standard Docker layer-caching pattern.
COPY pyproject.toml README.md LICENSE ./

# Install the package with [daemon] extras. --no-cache-dir keeps the image
# lean; we'll never run pip again inside the container.
#
# We do a two-step install: first metadata-only with fake src to lock deps,
# then the real source copy. Simpler approach — just copy source before
# pip install and eat the cache miss on source changes; pyproject.toml
# rarely changes anyway.
COPY src ./src
COPY config ./config

# Normalize permissions so the non-root `fsf` user can read the image's
# baked configs regardless of host umask. Without this, a host file
# created under umask 077 lands in the image as mode 600 and the trait
# engine 503s every write endpoint with the misleading "FSF_TRAIT_TREE_PATH"
# message. See live-fire-voice diagnostic 2026-04-25 that caught this.
# `a+rX` — read for all on every file, execute for directories only.
RUN chmod -R a+rX /app/src /app/config

RUN pip install --no-cache-dir ".[daemon]"

# Artifacts dir — the examples tree is the out-of-the-box canonical data.
# On first boot the entrypoint seeds /app/data/artifacts from this dir if
# the bind-mount is empty, so a fresh `docker compose up` has something
# to index without clobbering user data on subsequent starts.
COPY examples ./examples
RUN chmod -R a+rX /app/examples

# Entrypoint script: ensure /app/data subdirs exist and seed from examples
# on first boot. See scripts/docker-entrypoint.sh for the full rationale.
#
# chmod 0755 (not "+x") because the script is written to disk by the
# developer with their own umask, which may strip world-read. After COPY
# the file is owned by root in the container but executed by `fsf` (uid
# 1000), so we need group+world read+execute explicitly.
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# Drop privileges before setting env defaults so the user's home is correct.
USER fsf

# Runtime env. The compose file overrides these as needed; baked defaults
# here make `docker run` on the image alone produce a sensible daemon.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FSF_HOST=0.0.0.0 \
    FSF_PORT=7423 \
    FSF_REGISTRY_DB_PATH=/app/data/registry.sqlite \
    FSF_ARTIFACTS_DIR=/app/data/artifacts \
    FSF_AUDIT_CHAIN_PATH=/app/data/artifacts/audit_chain.jsonl \
    FSF_SOUL_OUTPUT_DIR=/app/data/soul_generated \
    FSF_LOCAL_BASE_URL=http://ollama:11434

EXPOSE 7423

# HEALTHCHECK: the daemon ships /healthz; hit it through curl so the
# container's health state is authoritative in `docker ps` and compose's
# depends_on: condition: service_healthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${FSF_PORT}/healthz" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn forest_soul_forge.daemon.app:app --host ${FSF_HOST} --port ${FSF_PORT} --log-level info"]
