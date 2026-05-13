#!/bin/bash
# Burst 263 — ADR-0051 T3: annotate non-sandbox-eligible tools.
#
# T1 added the sandbox_eligible field with default True. T3 marks
# the 5 tools that structurally cannot run in a subprocess sandbox
# (per ADR-0051 Decision 3) as ``sandbox_eligible: false``:
#
#   memory_recall.v1   — needs ctx.memory + ctx.agent_registry
#   memory_write.v1    — needs ctx.memory + the write_lock
#   memory_disclose.v1 — needs ctx.memory + audit chain emit
#   delegate.v1        — needs ctx.delegate closure (registry +
#                         audit + write_lock baked in)
#   llm_think.v1       — needs ctx.provider (HTTP client + creds
#                         + token accounting)
#
# Each entry also gets a brief in-line YAML comment explaining
# WHY for operators reading the catalog.
#
# Plus two new pytest drift-detectors in test_tool_catalog.py:
#   - test_real_catalog_marks_memory_delegate_llm_think_ineligible
#     — catches accidental removal of the annotation on any of the 5
#   - test_real_catalog_default_eligibility_for_canonical_sandboxable_tools
#     — catches accidental opt-out of tools that SHOULD be sandboxed
#
# Substrate is still inert at the dispatcher (T4 wires it in). T3 is
# data-only: catalog YAML edits + test drift-detectors.
#
# Expected test count: 62 → 64 (+2 new) in diag-b261 collection.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/tool_catalog.yaml \
        tests/unit/test_tool_catalog.py \
        dev-tools/commit-bursts/commit-burst263-adr0051-t3.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0051 T3 — mark non-eligible tools (B263)

Burst 263. Annotates the 5 tools that structurally cannot run
in a subprocess sandbox per ADR-0051 Decision 3:

  - memory_recall.v1   needs ctx.memory + ctx.agent_registry
  - memory_write.v1    needs ctx.memory + write_lock
  - memory_disclose.v1 needs ctx.memory + audit chain emit
  - delegate.v1        needs ctx.delegate closure
  - llm_think.v1       needs ctx.provider

Each gets sandbox_eligible: false plus an in-line YAML comment
explaining the rationale so operators reading the catalog
understand the opt-out.

Plus two pytest drift-detectors in test_tool_catalog.py:

  - test_real_catalog_marks_memory_delegate_llm_think_ineligible
    Fails CI if a future edit accidentally drops the annotation
    on any of the 5 — operator finds out at CI time rather than
    when strict mode tries to subprocess-pickle a live registry
    handle at runtime.

  - test_real_catalog_default_eligibility_for_canonical_sandboxable_tools
    Inverse check: audit_chain_verify, security_scan, code_read
    must NOT be opted out — they're exactly the kind of read-only
    tool that benefits most from sandboxing under T4.

Substrate still inert at the dispatcher (T4 wires it in).
T3 is data-only: catalog YAML + drift tests.

Expected test count: 62 → 64 in diag-b261 collection."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 263 complete — ADR-0051 T3 (catalog annotations) shipped ==="
echo "Press any key to close."
read -n 1
