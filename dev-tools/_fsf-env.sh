#!/usr/bin/env bash
# Forest Soul Forge — operator-script env loader.
#
# Source this from any *.command script that needs FSF_API_TOKEN. It
# reads the token from .env if not already in the shell environment.
# Caller should have cd'd to repo root (every operator script does
# via HERE=$(dirname "$0"); cd "$HERE").
#
# Why this exists: B148 (T25 security hardening) made the API token
# required for all write endpoints. Pre-B148 scripts had a
# ${FSF_API_TOKEN:-} fallback that returned empty, which was fine
# when token was optional. After B148, missing token = 401 Unauthorized
# on every write. Sourcing this helper loads the token from .env
# transparently so scripts keep working without manual export.
#
# Re-source-safe: if FSF_API_TOKEN is already in the environment,
# this no-ops (shell-set value wins, e.g., for testing override).
#
# Quiet on success: only logs if the token couldn't be resolved.

if [[ -z "${FSF_API_TOKEN:-}" ]]; then
    if [[ -f .env ]]; then
        # tail -1 in case the operator added a manual override below
        # the auto-generated line. cut + tr strip quotes if present.
        FSF_API_TOKEN=$(grep -E '^FSF_API_TOKEN=' .env \
            | tail -1 \
            | cut -d= -f2- \
            | tr -d '"' \
            | tr -d "'")
        if [[ -n "${FSF_API_TOKEN:-}" ]]; then
            export FSF_API_TOKEN
        else
            # .env exists but no FSF_API_TOKEN line found
            echo "[_fsf-env.sh] WARN: FSF_API_TOKEN not in .env. Daemon may auto-generate on first start." >&2
        fi
    else
        echo "[_fsf-env.sh] WARN: .env not found in $(pwd). Caller should cd to repo root before sourcing." >&2
    fi
fi
