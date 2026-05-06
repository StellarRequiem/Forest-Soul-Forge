#!/usr/bin/env bash
# Restart Forest daemon to pick up config/scheduled_tasks.yaml,
# then verify the scheduler picked up the tasks.
#
# This is the activation step for the ADR-0041 set-and-forget
# orchestrator after editing scheduled_tasks.yaml. The daemon reads
# the config file at startup; the file is hot-reloaded only on
# restart.

set -uo pipefail

# Finder launches .command files with cwd=$HOME, not the script's
# directory. cd to repo root so relative paths resolve correctly.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# B149 (T25 follow-on): load FSF_API_TOKEN from .env if not in shell env
if [[ -f dev-tools/_fsf-env.sh ]]; then source dev-tools/_fsf-env.sh; fi

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. preflight: scheduled_tasks.yaml exists?"
if [[ ! -f config/scheduled_tasks.yaml ]]; then
  echo "  ✗ config/scheduled_tasks.yaml not found"
  echo "    Copy from config/scheduled_tasks.yaml.example + substitute IDs first."
  echo ""
  echo "Press return to close."
  read -r _
  exit 1
fi
echo "  ✓ config/scheduled_tasks.yaml present ($(wc -l < config/scheduled_tasks.yaml | tr -d ' ') lines)"

bar "2. count enabled vs disabled tasks (preview)"
enabled=$(grep -c "enabled: true" config/scheduled_tasks.yaml || echo 0)
disabled=$(grep -c "enabled: false" config/scheduled_tasks.yaml || echo 0)
echo "  enabled: $enabled"
echo "  disabled: $disabled"

bar "3. restart Forest daemon via launchctl"
launchctl kickstart -k "gui/$(id -u)/dev.forest.daemon" \
  && echo "  ✓ kickstart -k sent" \
  || echo "  ✗ kickstart failed"

bar "4. wait up to 20s for /healthz"
for i in $(seq 1 20); do
  if curl -fsS --max-time 1 http://127.0.0.1:7423/healthz >/dev/null 2>&1; then
    echo "  ✓ daemon back up after ${i}s"
    break
  fi
  printf '.'
  sleep 1
done
echo

bar "5. verify scheduler picked up tasks"
echo "  /scheduler/status:"
curl -fsS http://127.0.0.1:7423/scheduler/status | python3 -m json.tool 2>/dev/null | sed 's/^/    /' \
  || echo "    (status fetch failed)"
echo ""
echo "  /scheduler/tasks (id / enabled / next_run_at):"
curl -fsS http://127.0.0.1:7423/scheduler/tasks 2>/dev/null \
  | jq -r '.tasks[] | "    \(.id | (. + "                                    ")[:36])  enabled=\(.enabled)  next_run=\(.state.next_run_at // "fire-on-first-tick")"' \
  2>/dev/null || echo "    (tasks fetch failed)"

bar "6. (optional) trigger status_reporter brief immediately"
echo "  Want to fire status_reporter_daily_brief once now to prove the wire?"
echo "  Run: curl -X POST http://127.0.0.1:7423/scheduler/tasks/status_reporter_daily_brief/trigger"
echo ""
echo "  (Uses qwen2.5-coder:7b via llm_think — ~10-30s response time)"

echo ""
echo "Done. Press return to close."
read -r _
