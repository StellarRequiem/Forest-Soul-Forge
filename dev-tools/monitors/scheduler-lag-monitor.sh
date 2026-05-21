#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# scheduler-lag-monitor.sh — Audit chain scheduler event lag checker
# Schedule: every hour via launchd
# Reads recent audit chain segments for scheduler events with high tick durations.
# Falls back to counting events in the last hour if no timing data found.
# Writes to monitor-logs/scheduler-lag.log

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/scheduler-lag.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
CHAIN_DIR="/Users/llm01/Forest-Soul-Forge/data/audit_chain_segments"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")
ONE_HOUR_AGO=$(date -v-1H +"%Y-%m-%d %I:%M:%S %p %Z" 2>/dev/null || date -d '1 hour ago' +"%Y-%m-%d %I:%M:%S %p %Z" 2>/dev/null || echo "")

mkdir -p "${LOG_DIR}"

echo "=== scheduler-lag ${TIMESTAMP} ===" >> "${LOG_FILE}"

if [ ! -d "${CHAIN_DIR}" ]; then
    echo "  audit_chain_segments directory not found at ${CHAIN_DIR}" >> "${LOG_FILE}"
    echo "  skipping — no data to analyze" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

# Find the most recent segment files (last 5 by modification time)
recent_files=$(find "${CHAIN_DIR}" -name '*.jsonl' -o -name '*.json' 2>/dev/null | xargs ls -t 2>/dev/null | head -5)

if [ -z "${recent_files}" ]; then
    echo "  no audit chain segment files found" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

# Look for scheduler events with high tick durations
slow_ticks=0
total_scheduler_events=0
has_timing_data=false

for f in ${recent_files}; do
    while IFS= read -r line; do
        # Check if line contains scheduler event
        if echo "${line}" | grep -q '"scheduler"' 2>/dev/null; then
            total_scheduler_events=$((total_scheduler_events + 1))

            # Check for tick_duration_ms > 2000
            duration=$(echo "${line}" | python3 -c "
import sys, json
try:
    obj = json.loads(sys.stdin.read())
    for key in ('tick_duration_ms', 'duration_ms', 'elapsed_ms'):
        if key in obj:
            print(obj[key])
            break
        if 'data' in obj and isinstance(obj['data'], dict) and key in obj['data']:
            print(obj['data'][key])
            break
    else:
        print('')
except:
    print('')
" 2>/dev/null)

            if [ -n "${duration}" ] && [ "${duration}" != "" ]; then
                has_timing_data=true
                if [ "$(echo "${duration} > 2000" | bc 2>/dev/null || python3 -c "print(1 if float('${duration}') > 2000 else 0)")" = "1" ]; then
                    slow_ticks=$((slow_ticks + 1))
                fi
            fi
        fi
    done < "${f}"
done

if [ "${has_timing_data}" = true ]; then
    echo "  scheduler events found: ${total_scheduler_events}" >> "${LOG_FILE}"
    echo "  slow ticks (>2000ms): ${slow_ticks}" >> "${LOG_FILE}"
    if [ "${slow_ticks}" -gt 0 ]; then
        echo "[ALERT] ${TIMESTAMP} scheduler-lag: ${slow_ticks} scheduler tick(s) exceeded 2000ms" >> "${ALERT_FILE}"
    fi
else
    echo "  scheduler events in recent segments: ${total_scheduler_events}" >> "${LOG_FILE}"
    echo "  (no structured timing data found — event count only)" >> "${LOG_FILE}"
fi

echo "" >> "${LOG_FILE}"
