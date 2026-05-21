#!/bin/bash
set -euo pipefail
export TZ="America/Los_Angeles"
# disk-memory-audit.sh — Disk and memory usage report
# Schedule: daily at 11:00 AM via launchd
# Reports disk usage, Ollama model sizes, Docker disk, audit chain size,
# system memory, and top memory consumers.
# Writes to monitor-logs/disk-memory.log

LOG_DIR="/Users/llm01/Forest-Soul-Forge/data/monitor-logs"
LOG_FILE="${LOG_DIR}/disk-memory.log"
CHAIN_DIR="/Users/llm01/Forest-Soul-Forge/data/audit_chain_segments"
TIMESTAMP=$(date +"%Y-%m-%d %I:%M:%S %p %Z")

mkdir -p "${LOG_DIR}"

echo "=== disk-memory-audit ${TIMESTAMP} ===" >> "${LOG_FILE}"

# 1. Root disk usage
echo "  --- disk usage ---" >> "${LOG_FILE}"
df -h / 2>/dev/null | while IFS= read -r line; do
    echo "  ${line}" >> "${LOG_FILE}"
done

# 2. Ollama models
echo "  --- ollama models ---" >> "${LOG_FILE}"
if [ -d "${HOME}/.ollama/models" ]; then
    ollama_size=$(du -sh "${HOME}/.ollama/models" 2>/dev/null | cut -f1)
    echo "  ${HOME}/.ollama/models: ${ollama_size}" >> "${LOG_FILE}"
else
    echo "  ollama models directory not found" >> "${LOG_FILE}"
fi

# 3. Docker disk
echo "  --- docker disk ---" >> "${LOG_FILE}"
if command -v docker &>/dev/null; then
    docker system df 2>/dev/null | while IFS= read -r line; do
        echo "  ${line}" >> "${LOG_FILE}"
    done
else
    echo "  docker not available" >> "${LOG_FILE}"
fi

# 4. Audit chain size
echo "  --- audit chain segments ---" >> "${LOG_FILE}"
if [ -d "${CHAIN_DIR}" ]; then
    chain_size=$(du -sh "${CHAIN_DIR}" 2>/dev/null | cut -f1)
    chain_count=$(find "${CHAIN_DIR}" -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  ${CHAIN_DIR}: ${chain_size} (${chain_count} files)" >> "${LOG_FILE}"
else
    echo "  audit chain segments directory not found" >> "${LOG_FILE}"
fi

# 5. System memory (vm_stat parsed)
echo "  --- system memory ---" >> "${LOG_FILE}"
vm_stat_output=$(vm_stat 2>/dev/null || echo "")
if [ -n "${vm_stat_output}" ]; then
    python3 -c "
import sys

lines = '''${vm_stat_output}'''.strip().split('\n')
page_size = 16384  # default on Apple Silicon; first line may override
for line in lines:
    if 'page size of' in line:
        page_size = int(line.split('page size of')[1].strip().split()[0])
        break

stats = {}
for line in lines[1:]:
    if ':' not in line:
        continue
    key, val = line.split(':', 1)
    val = val.strip().rstrip('.')
    try:
        stats[key.strip()] = int(val)
    except ValueError:
        pass

def pages_to_gb(pages):
    return pages * page_size / (1024**3)

free = stats.get('Pages free', 0)
active = stats.get('Pages active', 0)
inactive = stats.get('Pages inactive', 0)
speculative = stats.get('Pages speculative', 0)
wired = stats.get('Pages wired down', 0)
compressed = stats.get('Pages stored in compressor', 0)

print(f'  free:        {pages_to_gb(free):.2f} GB')
print(f'  active:      {pages_to_gb(active):.2f} GB')
print(f'  inactive:    {pages_to_gb(inactive):.2f} GB')
print(f'  speculative: {pages_to_gb(speculative):.2f} GB')
print(f'  wired:       {pages_to_gb(wired):.2f} GB')
print(f'  compressed:  {pages_to_gb(compressed):.2f} GB')
" >> "${LOG_FILE}" 2>/dev/null
else
    echo "  vm_stat not available (not macOS?)" >> "${LOG_FILE}"
fi

# 6. Top 5 memory consumers
echo "  --- top 5 memory consumers ---" >> "${LOG_FILE}"
# macOS ps doesn't support --sort, use sort command instead
ps aux 2>/dev/null | sort -nrk 4 | head -5 | while IFS= read -r line; do
    echo "  ${line}" >> "${LOG_FILE}"
done

echo "" >> "${LOG_FILE}"
