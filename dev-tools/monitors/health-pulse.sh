#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# health-pulse.sh — Forest Soul Forge endpoint & process health check
# Schedule: every 2 hours via launchd
# Checks daemon API, frontend UI, and Ollama endpoints, plus process liveness.
# Writes to monitor-logs/health-pulse.log; alerts to monitor-logs/ALERTS.log

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/health-pulse.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== health-pulse ${TIMESTAMP} ===" >> "${LOG_FILE}"

alert_fired=false

check_endpoint() {
    local name="$1"
    local url="$2"
    local start_ms end_ms elapsed_ms http_code

    start_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${url}" 2>/dev/null || echo "000")
    end_ms=$(python3 -c 'import time; print(int(time.time()*1000))')
    elapsed_ms=$(( end_ms - start_ms ))

    echo "  ${name}: HTTP ${http_code} (${elapsed_ms}ms) — ${url}" >> "${LOG_FILE}"

    if [ "${http_code}" = "000" ] || [ "${http_code}" -ge 500 ] 2>/dev/null; then
        echo "[ALERT] ${TIMESTAMP} health-pulse: ${name} DOWN — HTTP ${http_code} at ${url}" >> "${ALERT_FILE}"
        alert_fired=true
    fi
}

check_process() {
    local name="$1"
    local pattern="$2"
    local count
    count=$(pgrep -f "${pattern}" 2>/dev/null | wc -l | tr -d ' ')

    if [ "${count}" -gt 0 ]; then
        echo "  process ${name}: ${count} instance(s) running" >> "${LOG_FILE}"
    else
        echo "  process ${name}: NOT RUNNING" >> "${LOG_FILE}"
        echo "[ALERT] ${TIMESTAMP} health-pulse: process ${name} not found" >> "${ALERT_FILE}"
        alert_fired=true
    fi
}

# Endpoint checks
check_endpoint "daemon-api" "http://127.0.0.1:7423/healthz"
check_endpoint "frontend-ui" "http://localhost:5173"
check_endpoint "ollama"      "http://localhost:11434/api/tags"

# Process checks
check_process "uvicorn" "uvicorn"
check_process "vite"    "node.*vite"

if [ "${alert_fired}" = false ]; then
    echo "  status: ALL OK" >> "${LOG_FILE}"
fi

echo "" >> "${LOG_FILE}"
