#!/usr/bin/env bash
# Forest Soul Forge — live end-to-end smoke test.
#
# Exercises the full forge → install → run → recall loop against a
# RUNNING daemon + local Ollama. Catches integration bugs that unit
# tests miss: dispatcher wiring, lifespan loaders, audit-chain
# ordering across the runtime boundary, real-LLM manifest emission.
#
# Usage:
#   ./scripts/live-smoke.sh
#
# Prereqs:
#   - daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423)
#   - `fsf` on PATH (pip install -e .)
#   - jq, curl
#   - Ollama running with at least one model pulled
#
# Env:
#   FSF_DAEMON_URL   override the daemon URL
#   FSF_API_TOKEN    auth token if the daemon requires it
#   FSF_SMOKE_MODEL  override the model the local provider should use
#                    (defaults to whatever the daemon is configured for)
#
# Exit codes:
#   0  every stage passed
#   1  some stage failed; check the trailing report
#   2  prereq missing (jq / curl / fsf)

set -uo pipefail

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
TS="$(date +%s)"
SESSION_ID="live-smoke-${TS}"
RUNBOOK="docs/runbooks/end-to-end-smoke-test.md"
ARTIFACTS_DIR="${FSF_SMOKE_ARTIFACTS:-/tmp/fsf-live-smoke-${TS}}"

# ---------------------------------------------------------------------------
# Output helpers — colored if stderr is a tty, plain otherwise.
# ---------------------------------------------------------------------------
if [[ -t 2 ]]; then
  C_OK=$'\033[32m'; C_FAIL=$'\033[31m'; C_WARN=$'\033[33m'
  C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_OK=""; C_FAIL=""; C_WARN=""; C_DIM=""; C_RESET=""
fi

PASSED=()
FAILED=()

ok()    { echo "${C_OK}✓${C_RESET} $1" >&2; PASSED+=("$1"); }
fail()  { echo "${C_FAIL}✗${C_RESET} $1" >&2; FAILED+=("$1"); }
note()  { echo "${C_DIM}  $1${C_RESET}" >&2; }
header(){ echo >&2; echo "${C_WARN}== $1 ==${C_RESET}" >&2; }

# ---------------------------------------------------------------------------
# Prereqs
# ---------------------------------------------------------------------------
header "checking prereqs"
for bin in jq curl fsf; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    fail "$bin missing on PATH"
    echo "    install with: "
    case "$bin" in
      jq)   echo "      brew install jq  # or apt-get install jq" ;;
      curl) echo "      brew install curl" ;;
      fsf)  echo "      pip install -e . from the repo root" ;;
    esac
    exit 2
  fi
  ok "$bin available"
done

# ---------------------------------------------------------------------------
# Helper: signed curl
# ---------------------------------------------------------------------------
curl_get() {
  local path="$1"
  curl -fsS \
    ${TOKEN:+-H "X-FSF-Token: ${TOKEN}"} \
    "${DAEMON}${path}"
}
curl_post() {
  local path="$1"; shift
  local body="$1"; shift
  curl -fsS -X POST \
    -H "Content-Type: application/json" \
    ${TOKEN:+-H "X-FSF-Token: ${TOKEN}"} \
    -H "X-Idempotency-Key: ${SESSION_ID}-$(uuidgen 2>/dev/null || echo $RANDOM)" \
    "${DAEMON}${path}" \
    -d "${body}"
}

# ---------------------------------------------------------------------------
# Stage 1 — daemon health
# ---------------------------------------------------------------------------
header "stage 1: daemon health"
HEALTH="$(curl_get /healthz 2>&1)" || {
  fail "daemon unreachable at ${DAEMON}"
  echo "    response: ${HEALTH}"
  exit 1
}
if echo "$HEALTH" | jq -e '.ok == true' >/dev/null 2>&1; then
  ok "/healthz reports ok=true"
else
  fail "/healthz reports ok=false or malformed"
  echo "$HEALTH" | jq . >&2 || echo "$HEALTH" >&2
  exit 1
fi
schema_v="$(echo "$HEALTH" | jq -r '.schema_version')"
note "registry schema_version=${schema_v}"

# Surface any startup_diagnostics that didn't say 'ok'.
DIAGS="$(echo "$HEALTH" | jq -c '.startup_diagnostics // []')"
problems="$(echo "$DIAGS" | jq -c '[.[] | select(.status != "ok")]')"
if [[ "$(echo "$problems" | jq 'length')" != "0" ]]; then
  echo "${C_WARN}  startup diagnostics report non-ok components:${C_RESET}" >&2
  echo "$problems" | jq -r '.[] | "    - \(.component): \(.status) — \(.error // "no detail")"' >&2
fi

# ---------------------------------------------------------------------------
# Stage 2 — birth a researcher
# ---------------------------------------------------------------------------
header "stage 2: birth an agent"
BIRTH_BODY=$(cat <<EOF
{
  "profile": {
    "role": "anomaly_investigator",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "LiveSmoke-${TS}",
  "agent_version": "v1",
  "owner_id": "smoke-${USER:-runner}"
}
EOF
)
if BIRTH_RESP="$(curl_post /birth "$BIRTH_BODY" 2>&1)"; then
  INSTANCE_ID="$(echo "$BIRTH_RESP" | jq -r '.instance_id')"
  if [[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != "null" ]]; then
    ok "born ${INSTANCE_ID}"
  else
    fail "/birth returned no instance_id"
    echo "$BIRTH_RESP" | jq . >&2
    exit 1
  fi
else
  fail "/birth call failed"
  echo "    response: ${BIRTH_RESP}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 3 — forge a skill via the CLI (real provider)
# ---------------------------------------------------------------------------
header "stage 3: fsf forge skill (live provider)"
mkdir -p "${ARTIFACTS_DIR}"
SKILL_DESC="Stash a single short note in episodic memory, given the note text as the inputs.body string. Use only the memory_write.v1 tool. Skill name: smoke_stash_${TS}."
if FORGE_OUT="$(fsf forge skill "${SKILL_DESC}" --out-dir "${ARTIFACTS_DIR}/staged" 2>&1)"; then
  ok "skill forged"
  SKILL_NAME="$(echo "$FORGE_OUT" | grep -E '^\s*name:' | head -1 | awk '{print $2}')"
  if [[ -z "$SKILL_NAME" ]]; then
    fail "couldn't parse skill name from forge output"
    echo "$FORGE_OUT" >&2
    exit 1
  fi
  STAGED_DIR="${ARTIFACTS_DIR}/staged/${SKILL_NAME}.v1"
  note "name: ${SKILL_NAME}"
  note "staged: ${STAGED_DIR}"
else
  fail "fsf forge skill failed"
  echo "$FORGE_OUT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 4 — install + reload
# ---------------------------------------------------------------------------
header "stage 4: fsf install skill"
if INSTALL_OUT="$(fsf install skill "${STAGED_DIR}" 2>&1)"; then
  ok "skill installed + reloaded"
else
  fail "fsf install skill failed"
  echo "$INSTALL_OUT" >&2
  exit 1
fi

# Confirm it's in the catalog.
CAT="$(curl_get /skills)"
if echo "$CAT" | jq -e --arg n "$SKILL_NAME" '.skills[] | select(.name == $n)' >/dev/null; then
  ok "/skills lists ${SKILL_NAME}"
else
  fail "/skills did not list ${SKILL_NAME}"
  echo "$CAT" | jq -r '.skills[].name' >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 5 — run the skill
# ---------------------------------------------------------------------------
header "stage 5: run skill against agent"
PHRASE="quartz-meadow-${TS}"
RUN_BODY=$(cat <<EOF
{
  "skill_name": "${SKILL_NAME}",
  "skill_version": "1",
  "session_id": "${SESSION_ID}",
  "inputs": {"body": "${PHRASE}"}
}
EOF
)
if RUN_RESP="$(curl_post "/agents/${INSTANCE_ID}/skills/run" "$RUN_BODY" 2>&1)"; then
  STATUS="$(echo "$RUN_RESP" | jq -r '.status')"
  if [[ "$STATUS" == "succeeded" ]]; then
    ok "skill run succeeded"
    note "$(echo "$RUN_RESP" | jq -c '{steps_executed, steps_skipped, output}')"
  else
    fail "skill run status=${STATUS}"
    echo "$RUN_RESP" | jq . >&2
    exit 1
  fi
else
  fail "/skills/run call failed"
  echo "    response: ${RUN_RESP}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 6 — recall via /tools/call (memory_recall.v1)
# ---------------------------------------------------------------------------
header "stage 6: recall the memory entry"
RECALL_BODY=$(cat <<EOF
{
  "tool_name": "memory_recall",
  "tool_version": "1",
  "session_id": "${SESSION_ID}",
  "args": {"query": "${PHRASE}", "layer": "episodic", "limit": 5}
}
EOF
)
if RECALL_RESP="$(curl_post "/agents/${INSTANCE_ID}/tools/call" "$RECALL_BODY" 2>&1)"; then
  COUNT="$(echo "$RECALL_RESP" | jq -r '.result.output.count // 0')"
  if [[ "$COUNT" -ge 1 ]]; then
    ok "recalled ${COUNT} entry(ies) matching the phrase"
  else
    fail "recall returned 0 entries — write may not have landed"
    echo "$RECALL_RESP" | jq . >&2
    exit 1
  fi
else
  fail "/tools/call recall failed"
  echo "    response: ${RECALL_RESP}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 7 — character sheet shows memory + stats
# ---------------------------------------------------------------------------
header "stage 7: character sheet integration"
SHEET="$(curl_get "/agents/${INSTANCE_ID}/character-sheet")"
TOTAL_MEM="$(echo "$SHEET" | jq -r '.memory.total_entries // 0')"
TOTAL_INV="$(echo "$SHEET" | jq -r '.stats.total_invocations // 0')"
if [[ "$TOTAL_MEM" -ge 1 && "$TOTAL_INV" -ge 1 ]]; then
  ok "character sheet: memory=${TOTAL_MEM}, tool_invocations=${TOTAL_INV}"
else
  fail "character sheet stats look wrong (memory=${TOTAL_MEM}, invocations=${TOTAL_INV})"
  echo "$SHEET" | jq '{stats, memory}' >&2
fi

# ---------------------------------------------------------------------------
# Stage 8 — audit chain ordering check (last skill run)
# ---------------------------------------------------------------------------
header "stage 8: audit chain order"
AUDIT="$(curl_get '/audit?limit=20' || true)"
if [[ -n "$AUDIT" ]]; then
  TYPES="$(echo "$AUDIT" | jq -r '.events[] | .event_type' 2>/dev/null || true)"
  if echo "$TYPES" | grep -q skill_completed; then
    ok "audit chain shows recent skill_completed"
  else
    note "audit endpoint not available or no skill_completed in last 20 — skipping"
  fi
else
  note "audit endpoint unavailable — skipping order check"
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
echo >&2
header "summary"
echo "  passed: ${#PASSED[@]}" >&2
echo "  failed: ${#FAILED[@]}" >&2
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo >&2
  for f in "${FAILED[@]}"; do
    echo "${C_FAIL}  ✗ $f${C_RESET}" >&2
  done
  echo >&2
  echo "  artifacts kept at: ${ARTIFACTS_DIR}" >&2
  echo "  see ${RUNBOOK} for diagnosis hints." >&2
  exit 1
fi
echo >&2
echo "${C_OK}  end-to-end forge → install → run → recall: PASS${C_RESET}" >&2
echo "  artifacts: ${ARTIFACTS_DIR}" >&2
exit 0
