#!/usr/bin/env sh
# Docker entrypoint for the FSF daemon container.
#
# Job: make the bind-mounted /app/data dir a viable registry+artifacts
# home on first boot, then exec the daemon.
#
# Idempotent by construction:
#   * mkdir -p is safe to re-run
#   * the examples seed only runs when the target is empty, so user
#     edits are never clobbered
#   * no attempt to manage the registry DB — the daemon handles schema
#     create/migrate itself on open
#
# Why an entrypoint instead of baking this into the Dockerfile RUN?
# Because ./data is a bind-mount — it's empty at build time, and even
# if it weren't, the container's view of it only materializes at
# runtime. Seeding has to happen after mount, not during build.

set -eu

DATA_DIR="${FSF_DATA_DIR:-/app/data}"
SEED_DIR="${FSF_SEED_DIR:-/app/examples}"

# Ensure the subdirs the daemon writes into exist. These are the paths
# the Dockerfile's env defaults point at.
mkdir -p \
  "${DATA_DIR}" \
  "${DATA_DIR}/artifacts" \
  "${DATA_DIR}/soul_generated"

# First-boot seed: if ./data/artifacts has no soul.md files, copy the
# bundled examples in so the daemon has something to index and the UI
# shows the example agents instead of a blank slate.
#
# We use `find -quit` to bail on the first hit — fast and avoids
# enumerating a populated tree on every container start.
if [ -d "${SEED_DIR}" ] \
   && [ -z "$(find "${DATA_DIR}/artifacts" -mindepth 1 -print -quit 2>/dev/null || true)" ]; then
  echo "[fsf-entrypoint] ${DATA_DIR}/artifacts is empty — seeding from ${SEED_DIR}"
  # -a preserves timestamps + perms; /. makes the target get the
  # directory contents, not a nested copy.
  cp -a "${SEED_DIR}/." "${DATA_DIR}/artifacts/"
fi

# Hand off to whatever CMD was set in the Dockerfile / compose.
exec "$@"
