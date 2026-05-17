#!/bin/bash
# ADR-0078 Phase A T2b (B344) — birth ForensicArchivist-D3
# (forensic_archivist role).
#
# Mirrors birth-test-author.command's 4-phase shape: daemon
# kickstart → birth POST → constitution patch (per-tool
# constraints) → posture set → summary. Idempotent: re-runs
# skip the birth if ForensicArchivist-D3 already exists.
#
# Per-tool constraint patches (ADR-0078 §forensic_archivist kit):
#   code_read:        allowed_paths to data/forensics/ + the
#                     audit chain + custody metadata sidecars
#   file_integrity:   allowed_paths to data/forensics/ + audit
#                     chain (for the chain-of-custody log integrity
#                     check itself)
#   memory_write:     no path constraint (writes to registry SQLite)
#   memory_recall:    no path constraint
#   audit_chain_verify: no constraints
#   delegate / llm_think: no constraints
#
# Posture: GREEN per ADR-0078 Decision 5 — chain-of-custody
# verification is non-acting; the gate is the operator's later
# USE of the artifact, not the archivist's attestation.
#
# Artifact storage path resolution: ADR-0078 §Open questions left
# the choice between "bundle into audit chain segment archive
# (ADR-0073)" and "separate data/forensics/ tree" to this burst.
# Decision: data/forensics/. Rationale:
#   1. audit chain ENTRIES live under examples/audit_chain.jsonl
#      per CLAUDE.md — that's the attestation stream, not the
#      artifact bytes. Bundling MB-scale evidence into the chain
#      would inflate it and break the lazy-summarization assumption.
#   2. data/forensics/ matches the existing data/ convention for
#      runtime-mutable storage; the audit chain's segments under
#      examples/ are append-only attestations ABOUT data/.
#   3. Keeps the boundary clean for later — operators can mount
#      data/forensics/ to encrypted external storage without
#      touching the audit chain's locality.

set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "ADR-0078 Phase A T2b — Birth ForensicArchivist-D3"
echo "=========================================================="

# ---------------------------------------------------------------------------
# 1. Restart daemon so it picks up the new forensic_archivist role.
# ---------------------------------------------------------------------------
echo
echo "[1/4] Restarting daemon to load forensic_archivist role"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered — restart by hand if needed"
fi

# ---------------------------------------------------------------------------
# 2. Check existence; birth if absent.
# ---------------------------------------------------------------------------
echo
echo "[2/4] Checking for existing ForensicArchivist-D3"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=200" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); agents=data.get('agents',[]); [print(a.get('instance_id')) for a in agents if a.get('agent_name')=='ForensicArchivist-D3']" 2>/dev/null \
  || echo "")

if [ -n "$EXISTING" ]; then
  INSTANCE_ID="$EXISTING"
  echo "      ForensicArchivist-D3 already exists: ${INSTANCE_ID} — skipping birth"
else
  echo "      No existing ForensicArchivist-D3 — issuing /birth POST"
  BIRTH_PAYLOAD=$(cat <<'JSON'
{
  "profile": {
    "role": "forensic_archivist",
    "trait_values": {},
    "domain_weight_overrides": {}
  },
  "agent_name": "ForensicArchivist-D3",
  "agent_version": "v1",
  "owner_id": "alex"
}
JSON
)
  BIRTH_RESP=$(curl -s --max-time 30 "${DAEMON}/birth" \
    -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$BIRTH_PAYLOAD" 2>&1)
  echo "      Birth response (truncated):"
  echo "$BIRTH_RESP" | python3 -m json.tool 2>/dev/null | head -30 || echo "$BIRTH_RESP" | head -30

  INSTANCE_ID=$(echo "$BIRTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instance_id',''))" 2>/dev/null)
  if [ -z "$INSTANCE_ID" ]; then
    echo "      ERROR: birth did not return an instance_id. Aborting."
    echo "      Likely causes:"
    echo "        - trait_engine hasn't picked up forensic_archivist"
    echo "          (kickstart timing — wait 10s and retry)"
    echo "        - constitution_templates.yaml typo blocking role_base"
    echo "          resolution (re-read B343's template entry)"
    echo "        - keychain still rejecting :  (B335 should have fixed)"
    echo "      Check daemon logs."
    exit 2
  fi
  echo "      ForensicArchivist-D3 born: instance_id=${INSTANCE_ID}"
fi

# Ensure the forensics tree exists. The archivist itself doesn't
# create directories (its kit is read_only). The operator (us, at
# birth time) creates the canonical root once; subsequent custody
# transitions write per-incident subtrees.
mkdir -p "$(pwd)/data/forensics"

# ---------------------------------------------------------------------------
# 3. Patch constitution with per-tool constraints.
# ---------------------------------------------------------------------------
echo
echo "[3/4] Patching ForensicArchivist-D3's constitution with per-tool constraints"
CONST_PATH=$(curl -s --max-time 5 "${DAEMON}/agents/${INSTANCE_ID}" \
  -H "X-FSF-Token: $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('constitution_path',''))" 2>/dev/null)

if [ -z "$CONST_PATH" ] || [ ! -f "$CONST_PATH" ]; then
  echo "      WARN: could not resolve constitution_path for ${INSTANCE_ID}"
  echo "      Skipping constraint patch — ForensicArchivist-D3 will run"
  echo "      with the guardian-genre defaults until manually patched."
else
  echo "      Constitution at: $CONST_PATH"
  VENV_PY="$(pwd)/.venv/bin/python3"
  "$VENV_PY" - "$CONST_PATH" <<'PY'
import sys, yaml
path = sys.argv[1]
with open(path) as f:
    doc = yaml.safe_load(f)
patched = []
for entry in doc.get("tools") or []:
    if not isinstance(entry, dict):
        continue
    name = entry.get("name", "")
    constraints = entry.setdefault("constraints", {}) or {}
    if name == "code_read":
        # Read access to:
        #   - the forensic artifact tree (custody-managed files)
        #   - the audit chain (verify prior_hash before attesting)
        #   - per-artifact metadata sidecars (.sha256, .manifest)
        # Explicitly NOT src/ or config/ — the archivist has no
        # business reading code or constitutional state.
        constraints["allowed_paths"] = [
            "data/forensics/",
            "examples/audit_chain.jsonl",
            "examples/segments/",
        ]
        constraints["forbidden_paths"] = [
            "src/",
            "config/",
            "data/registry.sqlite",
            ".env",
            "~/.fsf/secrets",
        ]
        patched.append("code_read")
    elif name == "file_integrity":
        # Same scope as code_read: the artifact tree + chain
        # itself. file_integrity is the load-bearing tool for the
        # archivist; its scope is what defines what the archivist
        # CAN attest about.
        constraints["allowed_paths"] = [
            "data/forensics/",
            "examples/audit_chain.jsonl",
            "examples/segments/",
        ]
        constraints["forbidden_paths"] = [
            "src/",
            "config/",
            "data/registry.sqlite",
            ".env",
            "~/.fsf/secrets",
        ]
        patched.append("file_integrity")
    elif name == "audit_chain_verify":
        # No path constraint — audit_chain_verify is hard-wired to
        # the daemon's configured chain path via the catalog tool
        # definition. Listing it here is just inventory.
        patched.append("audit_chain_verify (no constraints — daemon-routed)")
if patched:
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print(f"      Constraints patched: {', '.join(patched)}")
else:
    print("      (no matching tools found for constraint patch — "
          "kit may be narrower than expected; check tool_catalog.yaml "
          "archetype entry for forensic_archivist)")
PY
fi

# ---------------------------------------------------------------------------
# 4. Set posture GREEN.
# ---------------------------------------------------------------------------
echo
echo "[4/4] Setting ForensicArchivist-D3's posture to GREEN"
# Per ADR-0078 Decision 5: forensic_archivist defaults to GREEN.
# Chain-of-custody verification is non-acting; the gate is the
# operator's later USE of the artifact, not the archivist's
# attestation. This is distinct from TestAuthor-D4 (YELLOW, every
# code_edit gated) — the archivist has nothing to gate.
POSTURE_RESP=$(curl -s --max-time 5 -X POST \
  "${DAEMON}/agents/${INSTANCE_ID}/posture" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"posture": "green", "reason": "ADR-0078 Decision 5 — chain-of-custody verification is non-acting; the gate is the operators later use of the artifact, not the archivists attestation"}' 2>&1)
echo "      Posture response:"
echo "$POSTURE_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$POSTURE_RESP" | head -5

echo
echo "=========================================================="
echo "ForensicArchivist-D3 ready. Summary:"
echo "  instance_id:    ${INSTANCE_ID}"
echo "  role:           forensic_archivist"
echo "  genre:          guardian"
echo "  posture:        green"
echo "  artifact root:  data/forensics/ (created if absent)"
echo "  next steps:     B345 handoffs.yaml wiring + integration tests"
echo "                  B346 forensic_archive skill"
echo "                  B347 umbrella + runbook (closes Phase A)"
echo "=========================================================="
echo
echo "Press any key to close this window."
# `|| true` per B341 — EOF tolerance when invoked from the
# umbrella with stdin redirected to /dev/null.
read -n 1 || true
