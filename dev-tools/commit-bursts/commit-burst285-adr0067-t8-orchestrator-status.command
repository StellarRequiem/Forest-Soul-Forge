#!/bin/bash
# Burst 285 — ADR-0067 T8: /orchestrator/* operator-facing endpoints.
#
# Mirrors the /reality-anchor/* pattern from ADR-0063 T7 (B256).
# Read-only endpoints + one /reload that hot-reloads the YAML
# configs. Drives the frontend Orchestrator pane (T7, queued).
#
# Endpoints:
#
#   GET  /orchestrator/status
#     Single combined status card: registry summary (total /
#     dispatchable / planned counts, domain_ids, errors), handoffs
#     summary (skill_mapping_count + cascade_rule_count + errors),
#     routing_activity_24h (total_routes + top 5 target_domains).
#
#   GET  /orchestrator/domains
#     Full domain manifest list with all fields. Drives the
#     operator's domain table.
#
#   GET  /orchestrator/handoffs
#     Loaded handoff config: skill mappings + cascade rules.
#     Read-only; operator edits config/handoffs.yaml directly.
#
#   GET  /orchestrator/recent-routes
#     Last N domain_routed audit events. Drives routing timeline.
#     Uses chain.tail() to avoid B256-style threadpool saturation.
#
#   POST /orchestrator/reload
#     Hot-reload domains + handoffs from disk. Returns the new
#     counts + any per-file errors. Mirrors /reality-anchor/reload.
#
# What ships:
#
# 1. daemon/routers/orchestrator.py: 5 endpoints. Helpers:
#    - _domain_to_dict: Domain → operator-readable dict
#    - _is_routing_event: matches "domain_routed"
#    - _read_recent_routes: chain.tail-based reader (B256 lesson:
#      no full-file scan; streaming + filter)
#    - _count_recent_routes: window-filtered total + by_domain breakdown
#    - _load_registry_or_502 / _load_handoffs_or_502: clean 502 on
#      DomainRegistryError / HandoffsError instead of 500
#
# 2. daemon/app.py: include the orchestrator router alongside
#    reality_anchor + security.
#
# Tests (test_daemon_orchestrator.py — 8 cases):
#   - _domain_to_dict marshals all fields including is_dispatchable
#   - _is_routing_event matches domain_routed, rejects others
#   - _read_recent_routes filters non-routing events out
#   - _read_recent_routes honors limit
#   - _read_recent_routes empty chain
#   - _count_recent_routes window filtering (24h cutoff)
#   - _count_recent_routes empty chain
#   - _count_recent_routes missing-timestamp entries skipped, no crash
#
# What's NOT in T8:
#   - Endpoint-level integration tests with TestClient — queued
#     for when D5 / D7 domain rollout starts and the orchestrator
#     pane sees real traffic. Module-level helper coverage is the
#     T8 commitment.
#
# After T8 ADR-0067 sits at 7/8 tranches shipped. Only T7 frontend
# pane + T4b learned routes remain.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/orchestrator.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_daemon_orchestrator.py \
        dev-tools/commit-bursts/commit-burst285-adr0067-t8-orchestrator-status.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T8 — /orchestrator/* endpoints (B285)

Burst 285. Operator-facing read surface for the cross-domain
orchestrator. Mirrors the /reality-anchor/* pattern from
ADR-0063 T7 (B256).

What ships:

  - daemon/routers/orchestrator.py: 5 endpoints under
    /orchestrator/* — status (combined card with registry +
    handoffs + 24h routing activity), domains (full manifest
    list), handoffs (loaded skill mappings + cascade rules),
    recent-routes (last N domain_routed events), reload
    (hot-reload domains + handoffs from disk).

    Helpers: _domain_to_dict marshals Domain dataclasses;
    _read_recent_routes uses chain.tail() to avoid the B256
    threadpool-saturation pattern from /reality-anchor;
    _count_recent_routes window-filters to last 24h + breaks
    down by target_domain; _load_*_or_502 turns config errors
    into clean 502s instead of 500s.

  - daemon/app.py: include the orchestrator router alongside
    reality_anchor + security.

Tests: test_daemon_orchestrator.py — 8 cases covering
_domain_to_dict marshaling, _is_routing_event matching,
_read_recent_routes filtering + limit + empty-chain handling,
_count_recent_routes window filtering + empty + missing-timestamp
safety.

ADR-0067 now at 7/8 tranches shipped. Only T7 frontend pane +
T4b learned routes remain — both can ship independently when
the domains start carrying real load."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 285 complete — ADR-0067 T8 status endpoints shipped ==="
echo "ADR-0067 at 7/8. Only T7 frontend pane + T4b learned routes remain."
echo ""
echo "Press any key to close."
read -n 1
