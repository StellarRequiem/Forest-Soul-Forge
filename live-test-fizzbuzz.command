#!/usr/bin/env bash
# live-test-fizzbuzz.command — autonomous coding-loop smoke test.
#
# Goal: test whether a fresh Forest software_engineer agent + local Ollama
# can iteratively complete a stubbed FizzBuzz implementation by
# (a) reading failing pytest output, (b) generating Python via llm_think,
# (c) writing the result, (d) re-testing, until pytest passes OR a stop
# condition fires.
#
# This is the FIRST autonomous build-loop run. Expect rough edges:
#   - local model output may be malformed Python (logged + counted as turn)
#   - same edit produced repeatedly = livelock (auto-stop)
#   - pytest output unchanged across 5 turns = stuck (auto-stop)
#   - max 50 turns wall (auto-stop)
#
# Outputs land in data/test-runs/fizzbuzz-001/:
#   run.log         per-turn diary
#   audit-tail.log  audit chain entries for this Engineer
#   fizzbuzz.py     final state (whatever the agent left)
#
# Author intent: pragmatic. The script writes fizzbuzz.py directly
# rather than routing through code_edit.v1 — code_edit was already
# proved by live-test-sw-coding-tools.command. What's unproven is the
# LLM-driven loop, and that's what this exercises.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# ---- config ---------------------------------------------------------------
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
RUN_ID="fizzbuzz-001"
TARGET_DIR="$HERE/data/test-runs/$RUN_ID"
RUN_LOG="$TARGET_DIR/run.log"
AUDIT_TAIL="$TARGET_DIR/audit-tail.log"
LLM_DUMP="$TARGET_DIR/llm-output-dump.log"
MAX_TURNS=50
LIVELOCK_IDENTICAL_EDITS=3
LIVELOCK_UNCHANGED_OUTPUT=5
TURN_PAUSE_SECONDS=2

# ---- helpers --------------------------------------------------------------
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }
log()     { local msg="$1"; printf "[%s] %s\n" "$(date -u +%H:%M:%S)" "$msg" | tee -a "$RUN_LOG"; }
section() { printf "\n=========== %s ===========\n" "$1" | tee -a "$RUN_LOG"; }
die()     { log "ABORT: $1"; echo ""; echo "Press return to close."; read -r _; exit 1; }

mkdir -p "$TARGET_DIR"
: > "$RUN_LOG"
: > "$AUDIT_TAIL"
: > "$LLM_DUMP"

# ---- 0: preflight ---------------------------------------------------------
section "0. preflight"
log "RUN_ID=$RUN_ID"
log "DAEMON=$DAEMON"
log "TARGET_DIR=$TARGET_DIR"
log "MAX_TURNS=$MAX_TURNS"

curl -sf --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1 \
  || die "daemon $DAEMON not reachable. Start it via start.command first."
log "✓ daemon reachable"

if command -v ollama >/dev/null 2>&1; then
  ollama_models=$(ollama list 2>&1 | tail -n +2 | awk '{print $1}' | tr '\n' ',' | sed 's/,$//')
  log "ollama models loaded: ${ollama_models:-<none>}"
  case "$ollama_models" in
    *coder*|*qwen2.5*|*deepseek*) log "✓ a code-capable model appears loaded" ;;
    *) log "WARN: no code-flavored model in ollama list. Run may produce nonsense Python." ;;
  esac
else
  log "WARN: ollama CLI not in PATH — model availability unknown"
fi

tools_json=$(curl -sf --max-time 5 "$DAEMON/tools/registered" || echo '{"tools":[]}')
for t in code_read code_edit pytest_run llm_think; do
  has=$(echo "$tools_json" | jq --arg n "$t" '[.tools[]? | select(.name == $n)] | length' 2>/dev/null || echo 0)
  if [[ "$has" -ge 1 ]]; then
    log "✓ tool $t.v1 registered"
  else
    die "tool $t not registered (got tools_json: $(echo "$tools_json" | head -c 200)). Restart daemon."
  fi
done

# ---- 1: birth Engineer ----------------------------------------------------
section "1. birth Engineer"
ENG_NAME="Forge_FB001_$(date +%s)"
payload=$(jq -n --arg name "$ENG_NAME" '{
  profile: {role: "software_engineer", trait_values: {}, domain_weight_overrides: {}},
  agent_name: $name,
  agent_version: "v1",
  enrich_narrative: false
}')
body=$(curl -sf -X POST "$DAEMON/birth" -H "Content-Type: application/json" $(auth_header) -d "$payload" 2>&1) \
  || die "birth failed: $body"
ENG_ID=$(echo "$body" | jq -r '.instance_id')
ENG_CONST=$(echo "$body" | jq -r '.constitution_path')
log "✓ Engineer  id=$ENG_ID"
log "  constitution=$ENG_CONST"

# ---- 2: patch constitution ------------------------------------------------
section "2. patch Engineer constitution (allowed_paths + relax approval)"
"$HERE/.venv/bin/python3" - "$ENG_CONST" "$TARGET_DIR" <<'PYEOF'
import sys, yaml
from pathlib import Path
const_path = Path(sys.argv[1])
target_dir = sys.argv[2]
const = yaml.safe_load(const_path.read_text())
allowed = [target_dir]
patched = []
for tool in const.get("tools", []):
    name = tool.get("name", "")
    constraints = tool.setdefault("constraints", {})
    if name in ("code_read", "code_edit", "pytest_run"):
        constraints["allowed_paths"] = allowed
        constraints["requires_human_approval"] = False
        patched.append(name)
    if name == "llm_think":
        constraints["requires_human_approval"] = False
        patched.append(name)
const_path.write_text(yaml.safe_dump(const, sort_keys=False))
print("patched tools:", patched)
PYEOF
log "✓ constitution patched"

# ---- 3: seed target dir ---------------------------------------------------
section "3. seed target dir"
cat > "$TARGET_DIR/fizzbuzz.py" <<'PY'
def fizzbuzz(n: int) -> list[str]:
    """Return ['1','2','Fizz','4','Buzz',...] up to n.

    - Multiples of 3 -> 'Fizz'
    - Multiples of 5 -> 'Buzz'
    - Multiples of 15 -> 'FizzBuzz'
    - Otherwise -> str(i)
    """
    raise NotImplementedError
PY

cat > "$TARGET_DIR/test_fizzbuzz.py" <<'PY'
from fizzbuzz import fizzbuzz

def test_basic():
    assert fizzbuzz(5) == ["1", "2", "Fizz", "4", "Buzz"]

def test_fifteen_hits_fizzbuzz():
    out = fizzbuzz(15)
    assert out[14] == "FizzBuzz"
    assert out[2] == "Fizz"
    assert out[4] == "Buzz"

def test_zero():
    assert fizzbuzz(0) == []

def test_one():
    assert fizzbuzz(1) == ["1"]
PY

cat > "$TARGET_DIR/README.md" <<'MD'
# Run 001 — FizzBuzz

Goal: implement `fizzbuzz(n)` in `fizzbuzz.py` so that
`pytest test_fizzbuzz.py` passes.

Function must RETURN a list of strings, not print.
Multiples-of-15 must take priority over multiples-of-3 and -5.
MD
log "✓ seeded fizzbuzz.py + test_fizzbuzz.py + README.md"

# ---- helpers for the loop -------------------------------------------------
PY_BIN="$HERE/.venv/bin/python3"
SESSION_ID="fizzbuzz-001-$(date +%s)"

run_pytest() {
  cd "$TARGET_DIR"
  local out
  out=$("$PY_BIN" -m pytest test_fizzbuzz.py 2>&1 || true)
  cd "$HERE"
  echo "$out"
}

pytest_passed() {
  # Match ONLY the all-passed summary line: "===== N passed in X.XXs ====="
  # Mixed-result line "===== N failed, M passed in X.XXs =====" must NOT match.
  # The regex requires "passed in" to come directly after digits + leading equals,
  # which fails when "failed," intervenes.
  echo "$1" | grep -qE "^=+ [0-9]+ passed in"
}

tool_call() {
  # tool_call <agent_id> <tool_name> <args_json>
  # Includes tool_version + unique session_id (per-turn) per ToolCallRequest schema.
  # Drops -f so HTTP error bodies surface in run.log for debugging.
  local agent="$1" tool="$2" args="$3"
  local sid="${SESSION_ID}-$(date +%s%N)"
  curl -s --max-time 90 -X POST "$DAEMON/agents/$agent/tools/call" \
    -H "Content-Type: application/json" $(auth_header) \
    -d "$(jq -n --arg t "$tool" --arg v "1" --arg s "$sid" --argjson a "$args" \
        '{tool_name: $t, tool_version: $v, session_id: $s, args: $a}')" \
    2>&1
}

extract_python_block() {
  # Strip markdown fences if present; otherwise return as-is.
  # MUST use python3 -c (not heredoc with python3 -) because heredoc
  # becomes BOTH the script body AND stdin, leaving sys.stdin.read()
  # empty. The -c form takes script as argv, keeping stdin free for
  # the piped LLM output. Single-quoted script disables bash expansion
  # of triple-backticks.
  "$PY_BIN" -c 'import sys, re
src = sys.stdin.read()
m = re.search(r"```(?:python)?\s*\n(.*?)\n```", src, re.DOTALL)
if m:
    print(m.group(1).strip())
else:
    print(src.strip())
'
}

# ---- 4: the loop ----------------------------------------------------------
section "4. iterate"
turn=0
last_pytest=""
unchanged_count=0
last_code_hash=""
identical_count=0
exit_reason="max_turns_hit"

while [[ $turn -lt $MAX_TURNS ]]; do
  turn=$((turn+1))
  printf "\n----- TURN %02d -----\n" "$turn" | tee -a "$RUN_LOG"

  # Test current state
  pytest_out=$(run_pytest)
  if pytest_passed "$pytest_out"; then
    log "✓ TESTS PASS — exiting (turn $turn)"
    exit_reason="success"
    break
  fi

  # Stuck-output detection
  if [[ "$pytest_out" == "$last_pytest" ]]; then
    unchanged_count=$((unchanged_count+1))
    if [[ $unchanged_count -ge $LIVELOCK_UNCHANGED_OUTPUT ]]; then
      log "ABORT: pytest output unchanged for $LIVELOCK_UNCHANGED_OUTPUT turns"
      exit_reason="stuck_output"
      break
    fi
  else
    unchanged_count=0
    last_pytest="$pytest_out"
  fi

  # Read current code
  current_code=$(cat "$TARGET_DIR/fizzbuzz.py")
  test_code=$(cat "$TARGET_DIR/test_fizzbuzz.py")

  # Build prompt
  prompt_text=$(printf '%s\n\n%s\n%s\n\n%s\n%s\n\n%s\n%s\n\n%s' \
    "You are completing a Python file so its tests pass. Write ONLY the complete contents of fizzbuzz.py — no markdown, no explanation, no other files. The function must RETURN a list of strings, not print." \
    "CURRENT fizzbuzz.py:" \
    "$current_code" \
    "test_fizzbuzz.py (do not modify, just satisfy):" \
    "$test_code" \
    "Pytest output (failing):" \
    "$pytest_out" \
    "Output the complete new fizzbuzz.py:")

  think_args=$(jq -n --arg p "$prompt_text" '{prompt: $p, max_tokens: 600, temperature: 0.2}')
  think_resp=$(tool_call "$ENG_ID" "llm_think" "$think_args" || echo '{}')
  echo "===== TURN $turn LLM RESP =====" >> "$LLM_DUMP"
  echo "$think_resp" >> "$LLM_DUMP"

  # Extract output text — llm_think returns ToolCallResponse{result.output.response}
  raw=$(echo "$think_resp" | jq -r '.result.output.response // empty' 2>/dev/null)
  status_field=$(echo "$think_resp" | jq -r '.status // empty' 2>/dev/null)

  if [[ -z "$raw" ]] || [[ "$raw" == "null" ]]; then
    log "TURN $turn: no .result.output.response. status=$status_field"
    log "  raw response (truncated): $(echo "$think_resp" | head -c 400)"
    sleep "$TURN_PAUSE_SECONDS"
    continue
  fi

  # Extract Python code
  new_code=$(echo "$raw" | extract_python_block)
  if [[ -z "$new_code" ]] || [[ ${#new_code} -lt 20 ]]; then
    log "TURN $turn: extracted code too short (${#new_code} chars). Skipping."
    sleep "$TURN_PAUSE_SECONDS"
    continue
  fi

  # Livelock: identical edit
  code_hash=$(echo "$new_code" | sha256sum | cut -c1-16)
  if [[ "$code_hash" == "$last_code_hash" ]]; then
    identical_count=$((identical_count+1))
    if [[ $identical_count -ge $LIVELOCK_IDENTICAL_EDITS ]]; then
      log "ABORT: same code proposed $LIVELOCK_IDENTICAL_EDITS times in a row"
      exit_reason="livelock_identical_edits"
      break
    fi
  else
    identical_count=0
    last_code_hash="$code_hash"
  fi

  # Write new code (direct write — first-run pragmatism)
  echo "$new_code" > "$TARGET_DIR/fizzbuzz.py"
  log "TURN $turn: wrote new fizzbuzz.py (${#new_code} chars, hash=$code_hash)"

  sleep "$TURN_PAUSE_SECONDS"
done

# ---- 5: post-mortem -------------------------------------------------------
section "5. post-mortem"
log "exit_reason=$exit_reason"
log "turns_used=$turn / $MAX_TURNS"

log "---- final fizzbuzz.py ----"
cat "$TARGET_DIR/fizzbuzz.py" | tee -a "$RUN_LOG"

log "---- final pytest output ----"
run_pytest | tee -a "$RUN_LOG"

log "---- audit chain entries for $ENG_ID ----"
"$PY_BIN" - "$ENG_ID" <<'PYEOF' | tee -a "$AUDIT_TAIL"
import sys, json
from pathlib import Path
agent_id = sys.argv[1]
chain = Path("data/audit_chain.jsonl")
if not chain.exists():
    print("no audit chain found")
else:
    count = 0
    for line in chain.read_text().splitlines():
        try:
            e = json.loads(line)
            if agent_id in json.dumps(e):
                count += 1
                print(f"seq={e.get('seq')}  type={e.get('event_type')}  ts={e.get('timestamp')}")
        except Exception:
            pass
    print(f"total entries for this agent: {count}")
PYEOF

echo ""
echo "Run complete. Inspect:"
echo "  $RUN_LOG"
echo "  $TARGET_DIR/fizzbuzz.py"
echo "  $LLM_DUMP"
echo "  $AUDIT_TAIL"
echo ""
echo "Press return to close."
read -r _
