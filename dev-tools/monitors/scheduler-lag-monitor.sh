#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# scheduler-lag-monitor.sh — audit-chain scheduler-lag checker
# Schedule: every hour via launchd
# Scans the live audit chain for scheduler_lag events in the last
# hour and flags any whose tick_duration_ms exceeded 2000ms.
# Writes to monitor-logs/scheduler-lag.log
#
# The daemon emits one scheduler_lag event per over-budget tick
# (event_data carries tick_duration_ms + tick_budget_ms). The live
# chain is a single append-only JSONL file — per daemon/config.py
# the default audit_chain_path points at examples/ (CLAUDE.md:
# "Live audit chain is at examples/audit_chain.jsonl"). The old
# data/audit_chain_segments/ directory never existed.

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/scheduler-lag.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
CHAIN_FILE="/Users/llm01/Forest-Soul-Forge/examples/audit_chain.jsonl"
SLOW_TICK_MS=2000
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")
# Chain timestamps are ISO-8601 UTC ("...Z"), which sort lexically,
# so the cutoff is computed in the same shape — a string compare is
# then a chronological one.
CUTOFF_UTC=$(date -u -v-1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
    || date -u -d '1 hour ago' +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "")

mkdir -p "${LOG_DIR}"

echo "=== scheduler-lag ${TIMESTAMP} ===" >> "${LOG_FILE}"

if [ ! -f "${CHAIN_FILE}" ]; then
    echo "  audit chain not found at ${CHAIN_FILE}" >> "${LOG_FILE}"
    echo "  skipping — no data to analyze" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

# Grep the scheduler_lag event type, then let python apply the
# one-hour timestamp window and tally slow ticks. grep is a cheap
# prefilter; python re-checks event_type so a stray substring match
# can't skew the count. `|| true` keeps a no-match (grep exit 1)
# from aborting the script under `set -o pipefail`.
stats=$(grep -F '"event_type":"scheduler_lag"' "${CHAIN_FILE}" 2>/dev/null \
  | CUTOFF="${CUTOFF_UTC}" SLOW_MS="${SLOW_TICK_MS}" python3 -c '
import sys, os, json
cutoff = os.environ.get("CUTOFF", "")
slow_ms = float(os.environ.get("SLOW_MS", "2000"))
total = slow = 0
worst = 0.0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get("event_type") != "scheduler_lag":
        continue
    if cutoff and obj.get("timestamp", "") < cutoff:
        continue
    total += 1
    dur = (obj.get("event_data") or {}).get("tick_duration_ms")
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        continue
    worst = max(worst, dur)
    if dur > slow_ms:
        slow += 1
print(total, slow, round(worst, 2))
' || true)

total=$(printf '%s\n' "${stats}" | cut -d' ' -f1)
slow=$(printf '%s\n' "${stats}" | cut -d' ' -f2)
worst=$(printf '%s\n' "${stats}" | cut -d' ' -f3)
total="${total:-0}"
slow="${slow:-0}"
worst="${worst:-0}"

echo "  window: events at or after ${CUTOFF_UTC:-<all>} (UTC)" >> "${LOG_FILE}"
echo "  scheduler_lag events in window: ${total}" >> "${LOG_FILE}"
echo "  slow ticks (>${SLOW_TICK_MS}ms): ${slow} (worst ${worst}ms)" >> "${LOG_FILE}"

if [ "${slow}" -gt 0 ]; then
    echo "[ALERT] ${TIMESTAMP} scheduler-lag: ${slow} scheduler tick(s) exceeded ${SLOW_TICK_MS}ms (worst ${worst}ms)" >> "${ALERT_FILE}"
fi

echo "" >> "${LOG_FILE}"
