#!/bin/bash
# fsf-scheduled-checks.command — host-side replacement for the failing
# scheduled Claude tasks (B452).
#
# WHY THIS EXISTS
# ---------------
# Eleven scheduled Claude tasks used to run daemon-health / Ollama /
# pytest / chain-integrity checks. They ran inside the Claude harness,
# which is a Linux sandbox — and a Linux sandbox:
#   * cannot reach the host daemon on 127.0.0.1:7423 (different netns),
#   * cannot talk to the host's Ollama,
#   * cannot use launchctl,
#   * runs Python 3.10, not the project's 3.11+.
# So every one of those tasks failed every run. The fix is not to keep
# retrying them in the sandbox — it is to run the checks where they can
# actually succeed: a plain shell script on the macOS host, fired by
# launchd. That is this file.
#
# WHAT IT CHECKS
# --------------
#   1. Daemon health      — GET /healthz on the local daemon
#   2. Ollama health      — `ollama --version`, `ollama ps`, API /api/tags
#   3. pytest regression  — full suite; alert if failures exceed baseline
#   4. Chain integrity    — AuditChain.verify() on the live audit chain
#   5. Log rotation       — rotate oversized logs, prune stale ones
#
# IDEMPOTENT + CRON-SAFE
# ----------------------
#   * No `set -e`: a single failing check never aborts the rest.
#   * A mkdir-based run lock prevents overlapping runs (launchd already
#     serializes a job, but a manual double-click during a launchd run
#     would otherwise collide).
#   * Every run is self-contained: no global state mutated, only the
#     gitignored log directory written.
#   * Logs append with size-based rotation so they never grow unbounded.
#
# INSTALL (run once, on the host, from the repo root)
# ---------------------------------------------------
#   chmod +x fsf-scheduled-checks.command
#   mkdir -p data/scheduled-checks-logs   # launchd needs the dir to exist
#   ln -sf "$(pwd)/dev-tools/launchd/dev.forest.scheduled-checks.plist" \
#          ~/Library/LaunchAgents/dev.forest.scheduled-checks.plist
#   launchctl load ~/Library/LaunchAgents/dev.forest.scheduled-checks.plist
#   launchctl list | grep dev.forest.scheduled-checks   # verify
#
# The job runs every 6 hours. To run pytest less often, raise
# StartInterval in the plist (the other four checks are cheap).
#
# Run on demand: double-click in Finder, or `bash fsf-scheduled-checks.command`.
# Uninstall: launchctl unload ~/Library/LaunchAgents/dev.forest.scheduled-checks.plist
#
# Output: data/scheduled-checks-logs/ (gitignored).

set -uo pipefail   # deliberately NOT -e — see "CRON-SAFE" above
export TZ="America/Los_Angeles"

# --- repo root: derived from this script's own location, so the script
#     works identically from the deployed main checkout, a clone, or a
#     git worktree without editing a hardcoded path.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON_URL="http://127.0.0.1:7423"
OLLAMA_URL="http://localhost:11434"
# The project venv. Defaults to <repo>/.venv (where `uv sync` puts it);
# FSF_CHECKS_PYTHON overrides for non-standard layouts. When neither
# resolves to an executable, the pytest + chain checks skip cleanly.
PYTHON="${FSF_CHECKS_PYTHON:-${HERE}/.venv/bin/python}"

LOG_DIR="${HERE}/data/scheduled-checks-logs"
LOG_FILE="${LOG_DIR}/scheduled-checks.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
PYTEST_LOG="${LOG_DIR}/pytest-latest.log"
BASELINE_FILE="${LOG_DIR}/.pytest-baseline"
LOCK_DIR="${LOG_DIR}/.run-lock"

ROTATE_MAX_BYTES=$(( 5 * 1024 * 1024 ))   # rotate a log once it passes 5 MB
RUN_ID="$(date +"%Y-%m-%d %I:%M:%S %p %Z")"

mkdir -p "${LOG_DIR}"

# --- run lock (atomic mkdir). If another run holds it, skip this tick
#     unless the lock is stale (>45 min — longer than a worst-case
#     pytest run), in which case steal it.
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    if [ -n "$(find "${LOCK_DIR}" -maxdepth 0 -mmin +45 2>/dev/null)" ]; then
        rmdir "${LOCK_DIR}" 2>/dev/null || true
        mkdir "${LOCK_DIR}" 2>/dev/null || { echo "fsf-scheduled-checks: lock contended — skipping"; exit 0; }
    else
        echo "fsf-scheduled-checks: another run is active — skipping this tick"
        exit 0
    fi
fi
trap 'rmdir "${LOCK_DIR}" 2>/dev/null || true' EXIT

alert_fired=false

# emit: write to the rolling log AND stdout (so a Finder double-click
# shows progress; under launchd stdout is captured to the .out.log).
emit()  { printf '%s\n' "$*" | tee -a "${LOG_FILE}"; }
alert() {
    printf '%s\n' "[ALERT] ${RUN_ID} $*" >> "${ALERT_FILE}"
    emit "  *** ALERT: $*"
    alert_fired=true
}
section() { emit ""; emit "--- $* ---"; }

# rotate: if $1 exceeds the size cap, move it aside to <name>.1 (one
# generation kept). Done BEFORE this run writes its header so the run's
# output lands in the fresh file.
rotate() {
    local f="$1" size
    [ -f "$f" ] || return 0
    size="$(stat -f%z "$f" 2>/dev/null || echo 0)"
    if [ "${size:-0}" -gt "${ROTATE_MAX_BYTES}" ]; then
        mv -f "$f" "${f}.1"
    fi
}
rotate "${LOG_FILE}"
rotate "${ALERT_FILE}"

emit "=========================================================="
emit "=== fsf-scheduled-checks  ${RUN_ID} ==="
emit "repo: ${HERE}"

# ---------------------------------------------------------------------
# 1. Daemon health — GET /healthz
# ---------------------------------------------------------------------
section "1. daemon health"
http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${DAEMON_URL}/healthz" 2>/dev/null || echo 000)"
if [ "${http_code}" = "200" ]; then
    body="$(curl -s --max-time 10 "${DAEMON_URL}/healthz" 2>/dev/null | head -c 200)"
    emit "  daemon: HTTP 200 — ${DAEMON_URL}/healthz"
    emit "  body:   ${body}"
else
    alert "daemon health: HTTP ${http_code} at ${DAEMON_URL}/healthz (daemon down or unreachable)"
fi

# ---------------------------------------------------------------------
# 2. Ollama model health — version, loaded models, API reachability
# ---------------------------------------------------------------------
section "2. ollama health"
if command -v ollama >/dev/null 2>&1; then
    ollama_ver="$(ollama --version 2>/dev/null | head -1 || echo 'unknown')"
    emit "  ollama version: ${ollama_ver}"

    # `ollama ps` lists currently-loaded models. Non-zero exit means the
    # ollama server isn't reachable from the CLI.
    if ps_out="$(ollama ps 2>&1)"; then
        # First line is the header; count data rows.
        loaded="$(printf '%s\n' "${ps_out}" | tail -n +2 | grep -c . || true)"
        emit "  ollama ps: ${loaded:-0} model(s) loaded"
        printf '%s\n' "${ps_out}" | sed 's/^/    /' >> "${LOG_FILE}"
    else
        alert "ollama ps failed — server not reachable: ${ps_out}"
    fi

    api_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${OLLAMA_URL}/api/tags" 2>/dev/null || echo 000)"
    if [ "${api_code}" = "200" ]; then
        emit "  ollama API: HTTP 200 — ${OLLAMA_URL}/api/tags"
    else
        alert "ollama API: HTTP ${api_code} at ${OLLAMA_URL}/api/tags"
    fi
else
    alert "ollama: command not found on PATH (${PATH})"
fi

# ---------------------------------------------------------------------
# 3. Test-suite regression run — pytest, compared against a baseline
#    failure count. The baseline ratchets: an improvement (fewer
#    failures) is locked in; a regression (more failures) alerts.
# ---------------------------------------------------------------------
section "3. pytest regression"
if [ -x "${PYTHON}" ]; then
    emit "  running: ${PYTHON} -m pytest tests/ -q --tb=line"
    "${PYTHON}" -m pytest tests/ -q --tb=line -p no:cacheprovider \
        > "${PYTEST_LOG}" 2>&1
    pytest_rc=$?

    # pytest summary line looks like: "12 failed, 4223 passed, 15 skipped ..."
    # The regression metric is failed + error counts summed (awk avoids a
    # bc dependency); passed is informational.
    summary="$(grep -E '[0-9]+ (passed|failed|error)' "${PYTEST_LOG}" | tail -1)"
    failed="$(printf '%s\n' "${summary}" | grep -oE '[0-9]+ (failed|error)' | grep -oE '^[0-9]+' | awk '{s+=$1} END{print s+0}')"
    passed="$(printf '%s\n' "${summary}" | grep -oE '[0-9]+ passed' | grep -oE '^[0-9]+' | awk '{s+=$1} END{print s+0}')"
    failed="${failed:-0}"
    passed="${passed:-0}"

    if [ -z "${summary}" ]; then
        # No recognizable summary — pytest itself broke (collection error,
        # crashed interpreter). Do NOT touch the baseline on a non-result.
        alert "pytest produced no summary line (exit=${pytest_rc}) — see ${PYTEST_LOG}"
    else
        emit "  pytest: ${passed} passed, ${failed} failed  (exit=${pytest_rc})"
        emit "  summary: ${summary}"
        if [ "${pytest_rc}" -gt 1 ]; then
            # 0 = all pass, 1 = tests failed; >1 = interrupted / internal
            # error / usage error / no tests collected.
            alert "pytest exited abnormally (code ${pytest_rc}) — see ${PYTEST_LOG}"
        fi
        if [ -f "${BASELINE_FILE}" ]; then
            baseline="$(cat "${BASELINE_FILE}" 2>/dev/null || echo 0)"
            baseline="${baseline:-0}"
            if [ "${failed}" -gt "${baseline}" ]; then
                alert "pytest REGRESSION: ${failed} failures vs baseline ${baseline} (+$(( failed - baseline )))"
            elif [ "${failed}" -lt "${baseline}" ]; then
                printf '%s\n' "${failed}" > "${BASELINE_FILE}"
                emit "  baseline ratcheted DOWN: ${baseline} → ${failed} (improvement locked in)"
            else
                emit "  baseline: ${baseline} failures — no regression"
            fi
        else
            printf '%s\n' "${failed}" > "${BASELINE_FILE}"
            emit "  baseline initialized at ${failed} failures (${BASELINE_FILE})"
        fi
    fi
else
    emit "  pytest: SKIP — venv python not found at ${PYTHON}"
    emit "          (run 'uv sync' on the host to create the venv)"
fi

# ---------------------------------------------------------------------
# 4. Audit-chain integrity quick-check — AuditChain.verify() walks the
#    live chain checking seq monotonicity + prev_hash linkage.
# ---------------------------------------------------------------------
section "4. audit chain integrity"
if [ -x "${PYTHON}" ]; then
    chain_result="$(
        "${PYTHON}" - <<'PYEOF' 2>&1
import os, sys
from pathlib import Path
try:
    from forest_soul_forge.core.audit_chain import AuditChain
except Exception as e:  # noqa: BLE001
    print(f"IMPORT_FAIL {type(e).__name__}: {e}")
    sys.exit(0)
# Default per daemon/config.py: examples/audit_chain.jsonl. Honors the
# same FSF_AUDIT_CHAIN_PATH override the daemon reads.
p = Path(os.environ.get("FSF_AUDIT_CHAIN_PATH") or "examples/audit_chain.jsonl")
if not p.exists():
    print(f"MISSING {p}")
    sys.exit(0)
try:
    r = AuditChain(p).verify()
except Exception as e:  # noqa: BLE001
    print(f"VERIFY_RAISED {type(e).__name__}: {e}")
    sys.exit(0)
if r.ok:
    print(f"OK {r.entries_verified} entries verified")
else:
    print(f"BROKEN seq={r.broken_at_seq} reason={r.reason}")
PYEOF
    )"
    case "${chain_result}" in
        OK\ *)       emit "  chain: ${chain_result}" ;;
        MISSING\ *)  emit "  chain: ${chain_result} — no live chain yet, skipping" ;;
        *)           alert "audit chain integrity: ${chain_result}" ;;
    esac
else
    emit "  chain: SKIP — venv python not found at ${PYTHON}"
fi

# ---------------------------------------------------------------------
# 5. Log rotation / cleanup — prune stale rotated + launchd-capture logs
#    so the gitignored log dir stays bounded. (Active logs were already
#    size-rotated at the top of this run.)
# ---------------------------------------------------------------------
section "5. log rotation / cleanup"
pruned="$(find "${LOG_DIR}" -type f \( -name '*.log.1' -o -name 'launchd-*.log' \) -mtime +30 -print 2>/dev/null | wc -l | tr -d ' ')"
find "${LOG_DIR}" -type f \( -name '*.log.1' -o -name 'launchd-*.log' \) -mtime +30 -delete 2>/dev/null || true
emit "  pruned ${pruned:-0} rotated/launchd log file(s) older than 30 days"
dir_size="$(du -sh "${LOG_DIR}" 2>/dev/null | cut -f1 || echo '?')"
emit "  log dir: ${LOG_DIR} (${dir_size})"

# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
section "summary"
if [ "${alert_fired}" = true ]; then
    emit "  RESULT: ATTENTION NEEDED — see ${ALERT_FILE}"
else
    emit "  RESULT: all checks OK"
fi
emit ""

# Interactive convenience: pause only when launched with a TTY (Finder
# double-click). Under launchd there is no TTY, so the script just exits.
if [ -t 0 ]; then
    echo ""
    echo "Press return to close."
    read -r _
fi

# Always exit 0 — this is a report-only job. Problems are surfaced via
# ALERTS.log, not via a non-zero exit that would clutter launchd state.
exit 0
