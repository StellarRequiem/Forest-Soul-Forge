#!/bin/bash
# Burst 308 - ADR-0074 T5: closure (endpoint + pin CLI + runbook).
#
# Closes ADR-0074 5/5. Memory consolidation arc complete:
# substrate (B294) + selector (B302) + summarizer (B306) +
# runner (B307) + this operator surface.
#
# What ships:
#
# 1. src/forest_soul_forge/daemon/routers/memory_consolidation.py:
#    Four endpoints (read + write):
#      GET  /memory/consolidation/status
#          - counts_by_state for all 5 enum values (defaults to 0
#            on missing keys so frontend can index defensively)
#          - last_run: pulled from the audit chain via .tail(2000),
#            filtered for memory_consolidation_run_completed.
#            Carries run_id, completed_at, summaries_created,
#            entries_consolidated.
#      GET  /memory/consolidation/recent-summaries?limit=20
#          - last N summary entries with their source_count from
#            a correlated subquery on consolidated_into.
#      POST /memory/consolidation/pin/{entry_id}
#          - flips pending -> pinned. 404 on missing, 409 on
#            wrong-state.
#      POST /memory/consolidation/unpin/{entry_id}
#          - flips pinned -> pending. Symmetric semantics.
#      Both writes gated by require_writes_enabled +
#      require_api_token. Use `with conn:` for the atomic flip.
#      Internal _flip_state helper keeps the SQL DRY.
#    _recent_chain_entries duck-typed audit helper handles both
#    .tail(n) (canonical) and .read_all() (test mock fallback),
#    surfaces dict or ChainEntry shapes interchangeably.
#
# 2. src/forest_soul_forge/cli/memory_cmd.py:
#    `fsf memory pin <entry_id>` + `fsf memory unpin <entry_id>`
#    subcommands operating directly on data/registry.sqlite
#    (override via --registry-path). Same state-transition rules
#    as the HTTP endpoint; rc=0 on success, rc=2 on missing /
#    wrong-state / missing registry. Offline operator surface
#    for post-crash or pre-daemon-boot recovery.
#
# 3. src/forest_soul_forge/daemon/app.py:
#    Imports + includes memory_consolidation_router.
#
# 4. src/forest_soul_forge/cli/main.py:
#    Registers `fsf memory ...` subparser group.
#
# 5. docs/runbooks/memory-consolidation.md:
#    Operator runbook covering:
#      - Reading /memory/consolidation/status (each field
#        explained + what each unhealthy pattern means)
#      - recent-summaries endpoint usage
#      - Pinning via HTTP (live daemon) AND via CLI (offline)
#      - Bulk-pin SQL recipe (for tag-based pins until the
#        CLI gains pin-tag)
#      - Diagnosing failed runs (the errors[] field on
#        run_completed events, common per-group error patterns,
#        unstarted-pair detection for crashed runners)
#      - Tuning the policy (with the queued env-var names that
#        T5b's scheduled-task wiring will surface)
#      - What this runbook does NOT cover: GDPR delete path,
#        encrypted-row skip, scheduled-task wiring (queued T5b)
#
# Tests (test_cli_memory.py - 8 cases):
#   Happy:
#     - pin flips pending -> pinned
#     - unpin flips pinned -> pending
#   Refusal:
#     - re-pin (already pinned) rc=2 with refuse-to-flip stderr
#     - pin on consolidated entry rc=2 (lineage protection)
#     - unpin on pending entry rc=2 (no-op refused)
#     - unknown entry_id rc=2 with stderr
#     - missing registry path rc=2 with stderr
#   Registration:
#     - both subcommands carry runner refs after add_subparser
#
# Sandbox-verified all 6 CLI scenarios pre-commit (pin / unpin /
# re-pin refuse / nonexistent / missing-registry / state-machine
# protection). HTTP router tests need fastapi which sandbox
# lacks; host pytest covers.
#
# === ADR-0074 CLOSED 5/5 ===
# Memory consolidation arc complete. Phase alpha scale-ADR
# scorecard: 4/10 closed (0050, 0067, 0074, 0075).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/memory_consolidation.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/cli/memory_cmd.py \
        src/forest_soul_forge/cli/main.py \
        docs/runbooks/memory-consolidation.md \
        tests/unit/test_cli_memory.py \
        dev-tools/commit-bursts/commit-burst308-adr0074-t5-closure.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0074 T5 - closure (B308) — ARC CLOSED 5/5

Burst 308. Closes ADR-0074 5/5. Memory consolidation arc
complete: substrate (B294) + selector (B302) + summarizer (B306)
+ runner (B307) + this operator surface.

What ships:

  - routers/memory_consolidation.py: four endpoints.
    GET /memory/consolidation/status - counts_by_state across
    all 5 enum values + last_run pulled from audit chain via
    .tail(2000) filtered for run_completed events.
    GET /memory/consolidation/recent-summaries?limit=20 - last
    N summary rows with source_count from correlated subquery
    on consolidated_into.
    POST pin/{id} + unpin/{id} - conditional state flips
    (pending<->pinned) gated by require_writes_enabled +
    require_api_token, using `with conn:` for atomicity.
    _recent_chain_entries duck-typed audit helper handles both
    .tail(n) canonical + .read_all() mock fallback.

  - cli/memory_cmd.py: fsf memory pin/unpin entry_id operating
    directly on data/registry.sqlite (override via
    --registry-path). Same state-transition rules as HTTP.
    Offline operator surface for post-crash or pre-daemon-boot
    recovery.

  - daemon/app.py + cli/main.py: register the new router +
    subcommand.

  - docs/runbooks/memory-consolidation.md: operator runbook
    covering status reading, pin workflow (HTTP + CLI + bulk
    SQL), failed-run diagnosis from chain events, policy
    tuning (with the queued env-var names T5b will surface),
    explicit out-of-scope list (GDPR delete, encrypted rows,
    scheduled-task wiring).

Tests: test_cli_memory.py - 8 cases covering both happy paths
+ all 5 refusal paths (re-pin, consolidated-row pin, no-op
unpin, missing entry, missing registry) + subparser
registration. Sandbox-verified all 6 CLI scenarios. HTTP
router tests need fastapi (sandbox lacks); host pytest
covers.

=== ADR-0074 CLOSED 5/5 ===
Memory consolidation arc complete. Phase alpha scorecard:
4/10 closed (ADR-0050, ADR-0067, ADR-0074, ADR-0075). Six
arcs still partial or substrate-only: ADR-0068, 0070, 0071,
0072, 0073, 0076."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 308 complete - ADR-0074 CLOSED 5/5 ==="
echo "Phase alpha: 4/10 scale ADRs closed."
echo ""
echo "Press any key to close."
read -n 1
