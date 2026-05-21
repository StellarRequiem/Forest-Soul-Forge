#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# reality-anchor-check.sh — Integrity check for reality anchor files
# Schedule: daily at 10:30 AM via launchd
# Computes SHA256 hashes of all .yaml/.json files in the reality_anchors
# directory and compares against a stored manifest. Reports new, removed,
# or changed files.
# Writes to monitor-logs/reality-anchor.log

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/reality-anchor.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
ANCHOR_DIR="/Users/llm01/Forest-Soul-Forge/data/reality_anchors"
MANIFEST="${LOG_DIR}/.reality-anchor-hashes"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== reality-anchor-check ${TIMESTAMP} ===" >> "${LOG_FILE}"

if [ ! -d "${ANCHOR_DIR}" ]; then
    echo "  reality_anchors directory not found at ${ANCHOR_DIR}" >> "${LOG_FILE}"
    echo "  skipping — nothing to check" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

# Build current hash map: "hash  relative_path"
current_hashes=$(mktemp)
find "${ANCHOR_DIR}" \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' \) -type f 2>/dev/null | sort | while IFS= read -r filepath; do
    relpath="${filepath#${ANCHOR_DIR}/}"
    hash=$(shasum -a 256 "${filepath}" 2>/dev/null | cut -d' ' -f1)
    echo "${hash}  ${relpath}"
done > "${current_hashes}"

file_count=$(wc -l < "${current_hashes}" | tr -d ' ')
echo "  files scanned: ${file_count}" >> "${LOG_FILE}"

if [ ! -f "${MANIFEST}" ]; then
    # First run — create manifest
    cp "${current_hashes}" "${MANIFEST}"
    echo "  first run — manifest created with ${file_count} file(s)" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    rm -f "${current_hashes}"
    exit 0
fi

# Compare against stored manifest
changes_found=false

# Check for changed or new files
while IFS= read -r line; do
    hash=$(echo "${line}" | cut -d' ' -f1)
    relpath=$(echo "${line}" | cut -d' ' -f3-)
    old_hash=$(grep "  ${relpath}$" "${MANIFEST}" 2>/dev/null | cut -d' ' -f1 || echo "")

    if [ -z "${old_hash}" ]; then
        echo "  NEW: ${relpath}" >> "${LOG_FILE}"
        changes_found=true
    elif [ "${hash}" != "${old_hash}" ]; then
        echo "  CHANGED: ${relpath}" >> "${LOG_FILE}"
        echo "    was: ${old_hash}" >> "${LOG_FILE}"
        echo "    now: ${hash}" >> "${LOG_FILE}"
        changes_found=true
    fi
done < "${current_hashes}"

# Check for removed files
while IFS= read -r line; do
    relpath=$(echo "${line}" | cut -d' ' -f3-)
    if ! grep -q "  ${relpath}$" "${current_hashes}" 2>/dev/null; then
        echo "  REMOVED: ${relpath}" >> "${LOG_FILE}"
        changes_found=true
    fi
done < "${MANIFEST}"

if [ "${changes_found}" = true ]; then
    echo "  STATUS: changes detected — updating manifest" >> "${LOG_FILE}"
    echo "[ALERT] ${TIMESTAMP} reality-anchor-check: reality anchor files changed — see reality-anchor.log" >> "${ALERT_FILE}"
    cp "${current_hashes}" "${MANIFEST}"
else
    echo "  STATUS: no changes — all anchors intact" >> "${LOG_FILE}"
fi

rm -f "${current_hashes}"
echo "" >> "${LOG_FILE}"
