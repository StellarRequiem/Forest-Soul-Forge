#!/bin/bash
# Burst 251 — ADR-0063 Reality Anchor T1+T2.
#
# Persistent operator-asserted ground truth + a lightweight
# pattern-match verifier that any agent can call (and that
# the governance pipeline will gate on in B252's T3).
#
# Decision posture (Alex confirmed 2026-05-12):
#   D1 — refuse on direct contradiction, warn on drift
#   D2 — on by default, per-agent constitutional opt-out
#   D3 — layered: operator-global canonical, per-agent ADD-only
#   D4 — bootstrap with obvious facts, expand as needed
#
# Files:
#
# 1. docs/decisions/ADR-0063-reality-anchor.md (NEW)
#    7 tranches mapped. T1+T2 shipping now. T3 (governance
#    pipeline RealityAnchorStep), T4 (reality_anchor agent
#    role), T5 (conversation runtime pre-turn hook), T6
#    (correction memory + recurrence), T7 (SoulUX pane) queued.
#
# 2. config/ground_truth.yaml (NEW)
#    14-fact bootstrap catalog:
#      operator identity (id + email)
#      license (ELv2 + Apache historical)
#      repo url + path
#      daemon + frontend URLs
#      platform (macOS, Mac mini M4)
#      python version (3.11+)
#      schema version (v19)
#      audit chain canonical path (examples/, not data/)
#      write_lock pattern (RLock)
#      dna identity (content-addressed; CRITICAL)
#      constitution hash immutability (CRITICAL)
#    Each fact: domain_keywords + canonical_terms +
#    forbidden_terms + severity. Catalog tuned across 15
#    smoke cases to fire correctly on the realistic
#    paraphrases an LLM would emit.
#
# 3. src/forest_soul_forge/core/ground_truth.py (NEW)
#    Fact dataclass + load_ground_truth + merge_agent_additions.
#    Per-agent collisions REJECTED (logged + dropped) per
#    ADR-0063 D3 — operator-global wins.
#
# 4. src/forest_soul_forge/tools/builtin/verify_claim.py (NEW)
#    VerifyClaimTool. Args: claim + optional fact_ids filter
#    + agent_constitution + catalog_path. Output: aggregate
#    verdict ∈ {confirmed, contradicted, unknown, not_in_scope}
#    + per-fact citations + highest_severity. Pattern-match
#    only (no LLM in v1 per ADR-0063 D5). side_effects=read_only.
#
# 5. src/forest_soul_forge/tools/builtin/__init__.py
#    Import + register VerifyClaimTool.
#
# 6. config/tool_catalog.yaml
#    verify_claim.v1 entry with archetype_tags
#    [verifier, guardian, observer, security_low].
#
# 7. tests/unit/test_ground_truth.py (NEW)
#    Loader tests: real catalog smoke, missing/malformed
#    files, missing required fields, invalid severity,
#    duplicate id, keyword case-normalization.
#    merge_agent_additions tests: layering, collision rejection,
#    invalid additions, non-list additions.
#
# 8. tests/unit/test_verify_claim.py (NEW)
#    Verdict matrix tests (each branch covered) + filter +
#    agent additions + real catalog smoke (license,
#    schema_version, audit_chain_path).
#
# Smoke results (sandbox, 15 claims against real catalog):
#   15/15 verdicts correct; severities propagated correctly
#   (CRITICAL for DNA/constitution; HIGH for license/email/
#   python/schema; MEDIUM for platform; none for confirms).
#
# Per ADR-0063 D5: pattern matching only in v1; LLM-grade
#   deep pass is a v2 ADR (queued).
# Per CLAUDE.md §0 Hippocratic gate: T1+T2 are READ-ONLY.
#   Refusal (T3) wires in next burst once we've operated
#   the verifier against real claims for a session or two.
# Per ADR-0001 D2: identity surface untouched — verifier
#   reads ground truth + claims; never mutates either.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0063-reality-anchor.md \
        config/ground_truth.yaml \
        config/tool_catalog.yaml \
        src/forest_soul_forge/core/ground_truth.py \
        src/forest_soul_forge/tools/builtin/verify_claim.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        tests/unit/test_ground_truth.py \
        tests/unit/test_verify_claim.py \
        dev-tools/commit-bursts/commit-burst251-adr0063-reality-anchor.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 T1+T2 ground truth + verify_claim.v1 (B251)

Burst 251. Foundation for the Reality Anchor — persistent
operator-asserted ground truth + a lightweight pattern-match
verifier any agent can call. T3-T7 (governance pipeline gate,
reality_anchor role, conversation hook, correction memory,
SoulUX pane) queued.

Decision posture (Alex confirmed 2026-05-12):
- D1: refuse on direct contradiction, warn on drift
- D2: on by default, per-agent constitutional opt-out
- D3: layered — operator-global canonical, per-agent ADD-only
- D4: bootstrap with obvious facts, expand as needed
- D5: pattern-match in v1, LLM deep-pass deferred to v2

config/ground_truth.yaml ships with 14 bootstrap facts:
operator identity, license (ELv2), repo URL/path, daemon/
frontend URLs, platform (macOS/M4), python (3.11+), schema
version (v19), audit chain canonical path, write_lock
pattern, plus two CRITICAL invariants (dna content-addressed,
constitution hash immutable).

core/ground_truth.py — Fact dataclass + load_ground_truth +
merge_agent_additions. Per-agent collisions REJECTED so a
compromised agent can't rewrite its own reality.

verify_claim.v1 — pattern-match verifier. Output: verdict
in {confirmed, contradicted, unknown, not_in_scope} +
per-fact citations + highest_severity. side_effects=read_only.

Sandbox smoke: 15/15 verdicts correct across realistic
LLM paraphrases (ELv2/MIT, content-addressed/random DNA,
macOS/Windows, etc.). Severities propagate correctly.

ADR-0063 status: T1+T2 shipped. T3 governance pipeline gate
queued for B252. Per CLAUDE.md §0 Hippocratic gate, the
substrate is read-only this burst — we operate it against
real claims before wiring the refusal path."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 251 complete ==="
echo "=== ADR-0063 T1+T2 live. Reality Anchor substrate ready. ==="
echo "Press any key to close."
read -n 1
