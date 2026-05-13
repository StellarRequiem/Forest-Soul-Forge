#!/bin/bash
# Burst 247 — ADR-0061 T4: passport bypasses K6 quarantine.
#
# The substrate from B246 (mint + verify + operator master)
# becomes operator-usable end-to-end: a passport.json next to
# constitution.yaml authorizes an agent to run on a non-home
# machine. K6 hardware-binding stays as the default protection;
# passport is the explicit-roaming escape hatch.
#
# Files:
#
# 1. src/forest_soul_forge/security/trust_list.py (NEW)
#    load_trusted_operator_pubkeys(): reads
#    FSF_TRUSTED_OPERATOR_KEYS env var (default
#    data/trusted_operators.txt). One b64 pubkey per line;
#    # comments + blank lines skipped; dedup; missing file is
#    fine (just returns local-only). Always includes the local
#    operator master from resolve_operator_keypair() unless
#    include_local=False (test scaffolding path).
#
# 2. src/forest_soul_forge/tools/dispatcher.py
#    _hardware_quarantine_reason: on binding mismatch, look for
#    passport.json next to constitution. If valid → return None
#    (bypass). If invalid → quarantine descriptor gains
#    passport_path + passport_reason fields so the operator
#    can fix.
#    New helper _check_passport_override walks: parse →
#    load_trusted_operator_pubkeys → verify_passport. All
#    failures yield a human-readable reason rolled up to the
#    quarantine descriptor.
#
# 3. tests/unit/test_passport_quarantine.py (NEW)
#    11 tests covering: valid passport bypasses K6;
#    no-passport keeps quarantine; tampered passport refused
#    (reason surfaced); passport-for-other-machine refused;
#    untrusted-issuer refused; expired refused; trust list
#    loads + dedupes + handles missing file + honors
#    include_local=False.
#
# 4. docs/runbooks/agent-passport.md (NEW)
#    Operator runbook: when to mint, the substrate explanation,
#    today's programmatic mint workflow (CLI/HTTP queued for
#    B248), trust establishment, recovery scenarios, failure-
#    mode → action mapping.
#
# 5. docs/decisions/ADR-0061-agent-passport.md
#    Status: T1-T5 shipped; T6 HTTP endpoint + T7 CLI queued.
#    Tranche table marks T4 + T5 DONE B247 with detail.
#
# Test verification (sandbox):
#   passport_quarantine + passport + operator_key: 36 passed
#   + agent_key_store + audit_chain_sigs + daemon_writes
#   + integration: 143 passed total. Zero B247-caused
#   failures.
#
# Per ADR-0001 D2: identity surface untouched. Passport is an
#   AUTHORIZATION artifact, not an identity artifact.
# Per ADR-0044 D3: additive. Legacy agents without
#   hardware_binding pass through unchanged. Agents with
#   hardware_binding but no passport on a non-home machine
#   still get quarantined (no behavior regression). Only
#   passport.json's PRESENCE changes outcome.
# Per CLAUDE.md Hippocratic gate: no removals; the original
#   K6 quarantine path is preserved as the default-deny when
#   no passport is present.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/trust_list.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_passport_quarantine.py \
        docs/runbooks/agent-passport.md \
        docs/decisions/ADR-0061-agent-passport.md \
        dev-tools/commit-bursts/commit-burst247-adr0061-t4-quarantine.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0061 T4 passport bypasses K6 quarantine (B247)

Burst 247. ADR-0061 T4 — the passport substrate from B246
becomes operator-usable end-to-end. An agent with a valid
passport.json next to its constitution.yaml authorizes itself to
run on machines other than its birth machine. K6 hardware-
binding stays the default protection; passport is the explicit-
roaming escape hatch.

trust_list.py loads operator-supplied trusted pubkeys from
FSF_TRUSTED_OPERATOR_KEYS env var (default
data/trusted_operators.txt). Local operator master auto-included.

_hardware_quarantine_reason: on binding mismatch, looks for
passport.json next to constitution. Valid passport → bypass
quarantine. Invalid → quarantine descriptor carries
passport_reason so the operator can fix.

Tests: 11 new in test_passport_quarantine.py covering all
quarantine outcomes. Runbook ships with workflow + recovery +
failure-mode → action mapping.

ADR-0061 status: T1-T5 shipped. T6 HTTP mint + T7 CLI queued.

Per ADR-0001 D2: identity unchanged.
Per ADR-0044 D3: additive — legacy agents unaffected.
Per CLAUDE.md Hippocratic gate: K6 default-deny preserved."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 247 complete ==="
echo "=== ADR-0061 T4 live. Passports now bypass K6 on valid auth. ==="
echo "Press any key to close."
read -n 1
