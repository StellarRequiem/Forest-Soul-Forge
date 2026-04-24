#!/usr/bin/env bash
# Run the test suite inside Docker — deps prebuilt, source bind-mounted.
#
# Usage:
#   ./scripts/docker_test.sh                       # full suite, short tb
#   ./scripts/docker_test.sh tests/unit/test_x.py  # specific file
#   ./scripts/docker_test.sh -k "idempotency"      # pytest -k filter
#
# The image is tagged `fsf-test` and auto-rebuilds when Dockerfile.test
# or pyproject.toml changes — we compare mtimes against the image's
# creation time to decide. If Docker isn't installed, the script says so
# and exits 1 rather than failing with a cryptic error.
set -euo pipefail

IMAGE_TAG="fsf-test"
DOCKERFILE="Dockerfile.test"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker not found on PATH" >&2
    echo "install Docker Desktop or use .venv/bin/python -m pytest directly" >&2
    exit 1
fi

# Decide whether to rebuild. Rebuild when:
#   - image doesn't exist
#   - Dockerfile.test is newer than the image
#   - pyproject.toml is newer than the image (deps may have changed)
needs_rebuild=false
image_created_ts="$(docker image inspect "$IMAGE_TAG" --format '{{.Created}}' 2>/dev/null || true)"
if [[ -z "$image_created_ts" ]]; then
    needs_rebuild=true
else
    # GNU date vs BSD date compat — use python for reliable ISO parsing.
    image_epoch=$(python3 -c "
import datetime, sys
ts = '$image_created_ts'.replace('Z', '+00:00')
# Docker's format can have nanoseconds; trim to microseconds.
if '.' in ts:
    head, rest = ts.split('.', 1)
    frac, tz = rest[:-6], rest[-6:]
    ts = f'{head}.{frac[:6]}{tz}'
print(int(datetime.datetime.fromisoformat(ts).timestamp()))
")
    dockerfile_epoch=$(stat -f %m "$DOCKERFILE" 2>/dev/null || stat -c %Y "$DOCKERFILE")
    pyproject_epoch=$(stat -f %m "pyproject.toml" 2>/dev/null || stat -c %Y "pyproject.toml")
    if [[ "$dockerfile_epoch" -gt "$image_epoch" ]] \
        || [[ "$pyproject_epoch" -gt "$image_epoch" ]]; then
        needs_rebuild=true
    fi
fi

if $needs_rebuild; then
    echo ">>> Building $IMAGE_TAG (first run or deps changed)..."
    docker build -f "$DOCKERFILE" -t "$IMAGE_TAG" .
fi

# Pass-through: if the user passed no args, run the image's default CMD.
# If they passed args, forward them to pytest.
if [[ $# -eq 0 ]]; then
    exec docker run --rm -v "$REPO_ROOT":/app "$IMAGE_TAG"
else
    exec docker run --rm -v "$REPO_ROOT":/app "$IMAGE_TAG" pytest "$@"
fi
