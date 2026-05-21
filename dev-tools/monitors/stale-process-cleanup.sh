#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# stale-process-cleanup.sh — Detect stale/zombie processes (report only, no killing)
# Schedule: daily at noon via launchd
# Finds orphan uvicorn workers, stale node processes, stale ollama workers,
# and exited Docker containers. Logs findings but takes no action.
# Writes to monitor-logs/stale-processes.log; alerts if anything found.

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/stale-processes.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== stale-process-cleanup ${TIMESTAMP} ===" >> "${LOG_FILE}"

found_stale=false

# 1. Check for multiple uvicorn master processes
echo "  --- uvicorn masters ---" >> "${LOG_FILE}"
uvicorn_masters=$(pgrep -f 'uvicorn.*--host\|uvicorn.*main:app' 2>/dev/null || true)
uvicorn_count=$(echo "${uvicorn_masters}" | grep -c '[0-9]' 2>/dev/null || echo "0")

if [ "${uvicorn_count}" -gt 1 ]; then
    echo "  WARNING: ${uvicorn_count} uvicorn master processes found (expected 1)" >> "${LOG_FILE}"
    echo "  PIDs:" >> "${LOG_FILE}"
    for pid in ${uvicorn_masters}; do
        cmd=$(ps -p "${pid}" -o args= 2>/dev/null || echo "(process exited)")
        echo "    ${pid}: ${cmd}" >> "${LOG_FILE}"
    done
    found_stale=true
elif [ "${uvicorn_count}" -eq 1 ]; then
    echo "  1 uvicorn master — OK" >> "${LOG_FILE}"
else
    echo "  no uvicorn masters running" >> "${LOG_FILE}"
fi

# 2. Check for stale node processes (not vite dev server)
echo "  --- node processes ---" >> "${LOG_FILE}"
node_pids=$(pgrep -f 'node' 2>/dev/null || true)
stale_node_count=0

if [ -n "${node_pids}" ]; then
    for pid in ${node_pids}; do
        cmd=$(ps -p "${pid}" -o args= 2>/dev/null || echo "")
        if [ -z "${cmd}" ]; then
            continue
        fi
        # Skip if it's the vite dev server
        if echo "${cmd}" | grep -q 'vite' 2>/dev/null; then
            continue
        fi
        # Skip if it's a known legitimate node process (npm, npx, etc.)
        if echo "${cmd}" | grep -qE 'npm|npx|yarn' 2>/dev/null; then
            continue
        fi
        echo "    stale? PID ${pid}: ${cmd}" >> "${LOG_FILE}"
        stale_node_count=$((stale_node_count + 1))
    done
fi

if [ "${stale_node_count}" -gt 0 ]; then
    echo "  ${stale_node_count} potentially stale node process(es)" >> "${LOG_FILE}"
    found_stale=true
else
    echo "  no stale node processes" >> "${LOG_FILE}"
fi

# 3. Check for stale ollama_llama_server processes
echo "  --- ollama workers ---" >> "${LOG_FILE}"
ollama_pids=$(pgrep -f 'ollama_llama_server' 2>/dev/null || true)
ollama_count=$(echo "${ollama_pids}" | grep -c '[0-9]' 2>/dev/null || echo "0")

if [ "${ollama_count}" -gt 0 ]; then
    echo "  ${ollama_count} ollama_llama_server process(es):" >> "${LOG_FILE}"
    for pid in ${ollama_pids}; do
        elapsed=$(ps -p "${pid}" -o etime= 2>/dev/null | tr -d ' ' || echo "?")
        echo "    PID ${pid}: running for ${elapsed}" >> "${LOG_FILE}"
    done
    # Flag if any have been running for a very long time (informational)
    echo "  (review manually — long-running workers may be expected)" >> "${LOG_FILE}"
else
    echo "  no ollama_llama_server processes" >> "${LOG_FILE}"
fi

# 4. Exited Docker containers
echo "  --- exited docker containers ---" >> "${LOG_FILE}"
if command -v docker &>/dev/null; then
    exited=$(docker ps -a --filter status=exited --format '{{.ID}} {{.Names}} {{.Status}}' 2>/dev/null || echo "")
    if [ -n "${exited}" ]; then
        exited_count=$(echo "${exited}" | wc -l | tr -d ' ')
        echo "  ${exited_count} exited container(s):" >> "${LOG_FILE}"
        echo "${exited}" | while IFS= read -r line; do
            echo "    ${line}" >> "${LOG_FILE}"
        done
        found_stale=true
    else
        echo "  no exited containers" >> "${LOG_FILE}"
    fi
else
    echo "  docker not available" >> "${LOG_FILE}"
fi

# Summary
if [ "${found_stale}" = true ]; then
    echo "  STATUS: stale processes detected — review above for details" >> "${LOG_FILE}"
    echo "[ALERT] ${TIMESTAMP} stale-process-cleanup: stale processes or exited containers found — see stale-processes.log" >> "${ALERT_FILE}"
else
    echo "  STATUS: all clean" >> "${LOG_FILE}"
fi

echo "" >> "${LOG_FILE}"
