#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# reality-anchor-check.sh — audit-chain reality-anchor event monitor
# Schedule: daily at 10:30 AM via launchd
# Scans the live audit chain for reality_anchor_flagged and
# reality_anchor_repeat_offender events, and alerts on any that are
# new since the last run (tracked by audit-chain seq in a state file).
# Writes to monitor-logs/reality-anchor.log
#
# The Reality Anchor (ADR-0063) records contradictions of operator
# ground truth as chain events — there is no data/reality_anchors/
# directory of files to hash. The live chain is a single append-only
# JSONL file; per daemon/config.py the default audit_chain_path
# points at examples/ (CLAUDE.md: "Live audit chain is at
# examples/audit_chain.jsonl").

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/reality-anchor.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
CHAIN_FILE="/Users/llm01/Forest-Soul-Forge/examples/audit_chain.jsonl"
# Highest reality-anchor seq seen on the previous run. Diffing on
# seq (append-only, monotonic) means each flag alerts exactly once.
STATE_FILE="${LOG_DIR}/.reality-anchor-seq"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== reality-anchor-check ${TIMESTAMP} ===" >> "${LOG_FILE}"

if [ ! -f "${CHAIN_FILE}" ]; then
    echo "  audit chain not found at ${CHAIN_FILE}" >> "${LOG_FILE}"
    echo "  skipping — nothing to check" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

last_seq=0
if [ -f "${STATE_FILE}" ]; then
    last_seq=$(tr -dc '0-9' < "${STATE_FILE}" 2>/dev/null || echo "")
    last_seq="${last_seq:-0}"
fi

# Grep the two reality-anchor event types, then let python count
# them, list the events newer than last_seq, and report the highest
# seq seen. `|| true` keeps a no-match (grep exit 1) from aborting
# the script under `set -o pipefail`.
report=$(grep -E '"event_type":"reality_anchor_(flagged|repeat_offender)"' "${CHAIN_FILE}" 2>/dev/null \
  | LAST_SEQ="${last_seq}" python3 -c '
import sys, os, json
last_seq = int(os.environ.get("LAST_SEQ", "0") or "0")
flagged = repeat = 0
max_seq = last_seq
new_lines = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    et = obj.get("event_type")
    if et == "reality_anchor_flagged":
        flagged += 1
    elif et == "reality_anchor_repeat_offender":
        repeat += 1
    else:
        continue
    try:
        seq = int(obj.get("seq", 0))
    except (TypeError, ValueError):
        seq = 0
    max_seq = max(max_seq, seq)
    if seq > last_seq:
        d = obj.get("event_data") or {}
        extra = ""
        if et == "reality_anchor_repeat_offender":
            extra = " repetition=%s decision=%s" % (
                d.get("repetition_count", "?"), d.get("decision", "?"))
        new_lines.append(
            "  NEW %s seq=%s fact=%s%s"
            % (et, seq, d.get("fact_id", "?"), extra)
        )
print("COUNTS %d %d" % (flagged, repeat))
for ln in new_lines:
    print(ln)
print("SUMMARY %d %d" % (max_seq, len(new_lines)))
' || true)

counts_line=$(printf '%s\n' "${report}" | grep '^COUNTS ' || true)
summary_line=$(printf '%s\n' "${report}" | grep '^SUMMARY ' || true)
flagged=$(printf '%s\n' "${counts_line}" | cut -d' ' -f2)
repeat=$(printf '%s\n' "${counts_line}" | cut -d' ' -f3)
max_seq=$(printf '%s\n' "${summary_line}" | cut -d' ' -f2)
new_count=$(printf '%s\n' "${summary_line}" | cut -d' ' -f3)
flagged="${flagged:-0}"
repeat="${repeat:-0}"
max_seq="${max_seq:-${last_seq}}"
new_count="${new_count:-0}"

echo "  reality_anchor_flagged events:         ${flagged}" >> "${LOG_FILE}"
echo "  reality_anchor_repeat_offender events: ${repeat}" >> "${LOG_FILE}"

if [ ! -f "${STATE_FILE}" ]; then
    # First run — record the baseline seq, don't alert on history.
    echo "${max_seq}" > "${STATE_FILE}"
    echo "  first run — baseline recorded at seq ${max_seq}" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

if [ "${new_count}" -gt 0 ]; then
    echo "  ${new_count} new reality-anchor event(s) since seq ${last_seq}:" >> "${LOG_FILE}"
    printf '%s\n' "${report}" | grep '^  NEW ' >> "${LOG_FILE}" || true
    echo "[ALERT] ${TIMESTAMP} reality-anchor-check: ${new_count} new reality-anchor event(s) — see reality-anchor.log" >> "${ALERT_FILE}"
    echo "${max_seq}" > "${STATE_FILE}"
else
    echo "  STATUS: no new reality-anchor events since seq ${last_seq}" >> "${LOG_FILE}"
fi

echo "" >> "${LOG_FILE}"
