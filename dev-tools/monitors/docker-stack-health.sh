#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# docker-stack-health.sh — Docker Compose stack status and memory check
# Schedule: every 4 hours via launchd
# Checks container running status and memory usage against limits.
# Writes to monitor-logs/docker-health.log; alerts on failures.

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/docker-health.log"
ALERT_FILE="${LOG_DIR}/ALERTS.log"
COMPOSE_FILE="/Users/llm01/Forest-Soul-Forge/docker-compose.yml"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== docker-stack-health ${TIMESTAMP} ===" >> "${LOG_FILE}"

# Check if docker is available
if ! command -v docker &>/dev/null; then
    echo "  docker command not found — skipping" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

# Check if compose file exists
if [ ! -f "${COMPOSE_FILE}" ]; then
    echo "  compose file not found at ${COMPOSE_FILE}" >> "${LOG_FILE}"
    echo "" >> "${LOG_FILE}"
    exit 0
fi

alert_fired=false

# Container status check
echo "  --- container status ---" >> "${LOG_FILE}"
container_json=$(docker compose -f "${COMPOSE_FILE}" ps --format json 2>/dev/null || echo "")

if [ -z "${container_json}" ]; then
    echo "  no containers found or docker compose failed" >> "${LOG_FILE}"
    echo "[ALERT] ${TIMESTAMP} docker-stack-health: docker compose ps returned empty — stack may be down" >> "${ALERT_FILE}"
    alert_fired=true
else
    echo "${container_json}" | python3 -c "
import sys, json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
        name = c.get('Name', c.get('name', 'unknown'))
        state = c.get('State', c.get('state', 'unknown'))
        status = c.get('Status', c.get('status', ''))
        print(f'  {name}: {state} ({status})')
        if state.lower() not in ('running', 'up'):
            print(f'  [NOT RUNNING] {name}')
    except json.JSONDecodeError:
        pass
" >> "${LOG_FILE}" 2>/dev/null

    # Check for non-running containers
    not_running=$(echo "${container_json}" | python3 -c "
import sys, json
count = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        c = json.loads(line)
        state = c.get('State', c.get('state', '')).lower()
        if state not in ('running', 'up'):
            count += 1
    except:
        pass
print(count)
" 2>/dev/null || echo "0")

    if [ "${not_running}" -gt 0 ]; then
        echo "[ALERT] ${TIMESTAMP} docker-stack-health: ${not_running} container(s) not running" >> "${ALERT_FILE}"
        alert_fired=true
    fi
fi

# Memory usage check
echo "  --- memory usage ---" >> "${LOG_FILE}"
stats_json=$(docker stats --no-stream --format '{"name":"{{.Name}}","mem_usage":"{{.MemUsage}}","mem_pct":"{{.MemPerc}}"}' 2>/dev/null || echo "")

if [ -n "${stats_json}" ]; then
    echo "${stats_json}" | while IFS= read -r line; do
        if [ -z "${line}" ]; then continue; fi
        name=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('name','?'))" 2>/dev/null || echo "?")
        mem_usage=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('mem_usage','?'))" 2>/dev/null || echo "?")
        mem_pct=$(echo "${line}" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('mem_pct','?'))" 2>/dev/null || echo "?")
        echo "  ${name}: ${mem_usage} (${mem_pct})" >> "${LOG_FILE}"

        # Alert if memory percentage > 80%
        pct_num=$(echo "${mem_pct}" | tr -d '%' | tr -d ' ')
        if [ -n "${pct_num}" ] && [ "${pct_num}" != "?" ]; then
            over_80=$(python3 -c "print(1 if float('${pct_num}') > 80 else 0)" 2>/dev/null || echo "0")
            if [ "${over_80}" = "1" ]; then
                echo "[ALERT] ${TIMESTAMP} docker-stack-health: ${name} memory at ${mem_pct} (>80%)" >> "${ALERT_FILE}"
            fi
        fi
    done
else
    echo "  no container stats available" >> "${LOG_FILE}"
fi

if [ "${alert_fired}" = false ]; then
    echo "  status: ALL OK" >> "${LOG_FILE}"
fi

echo "" >> "${LOG_FILE}"
