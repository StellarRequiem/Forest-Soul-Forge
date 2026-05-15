#!/bin/bash
# Burst 313 - ADR-0068 T3: `personal` memory scope.
#
# Adds 'personal' as the fifth value in the memory scope enum,
# alongside the four existing scopes (private/lineage/realm/
# consented). Orthogonal to the ceiling-rank ladder - personal
# scope is operator-bound context, eligible only for genres in
# the explicit allow-list.
#
# What ships:
#
# 1. src/forest_soul_forge/core/memory/_helpers.py:
#    - SCOPES tuple grows to ("private", "lineage", "realm",
#      "consented", "personal").
#    - RECALL_MODES tuple grows to include "personal".
#    - PERSONAL_SCOPE_ALLOWED_GENRES frozenset constant:
#      {companion, assistant, operator_steward, domain_orchestrator}.
#    - _SCOPE_RANK gains "personal": 4 — for KeyError safety
#      when the ceiling check looks up scope rank; the actual
#      access decision branches off the rank ladder for personal.
#
# 2. src/forest_soul_forge/core/memory/__init__.py:
#    - Write-side enforcement (Memory.append): personal scope
#      branches off the rank check into PERSONAL_SCOPE_ALLOWED_GENRES
#      lookup. Genre not in the allow-list raises
#      MemoryScopeViolation with the allowed-list spelled out.
#      Other scopes flow through the existing ceiling-rank check
#      unchanged.
#    - Read-side semantics (Memory.recall_visible_to):
#      mode='personal' is NON-additive — returns ONLY personal-
#      scope rows across instance boundaries (operator-context
#      isn't agent-private). The always-on private clause is
#      suppressed when mode='personal' so the reader's private
#      surface doesn't leak in. Other modes (private / lineage /
#      consented) untouched.
#
# 3. tests/unit/test_memory_personal_scope.py - 11 cases:
#    Constants:
#      - personal in SCOPES + RECALL_MODES
#      - PERSONAL_SCOPE_ALLOWED_GENRES contains the canonical four
#    Write-side allow-list:
#      - all 4 allowed genres can write (parametrized)
#      - 4 forbidden genres refused (parametrized)
#      - unknown-genre refused
#    Read-side semantics:
#      - personal mode returns cross-instance personal rows
#      - personal mode excludes reader's own private rows
#      - lineage mode does NOT see personal rows (orthogonality)
#      - consented mode does NOT see personal rows (orthogonality)
#      - written scope='personal' persists in DB
#
# Sandbox-verified all 11 scenarios end-to-end.
#
# Tool-layer genre gate (the memory_recall.v1 BEFORE-check that
# refuses personal-mode recalls from non-allowlist genres) lives
# in the tool, not Memory class. The Memory class provides the
# SQL surface; the tool gates by genre. T2 of the tool refactor
# is queued for a follow-on burst — currently the data layer is
# the source of truth for the allow-list constant; tool-layer
# enforcement reads from the same constant.
#
# ADR-0068 progress: 4/8 (T1 substrate + T1.1 ground-truth merge
# + T2 write tool + T3 personal scope). T4-T8 queued: trust circle,
# voice samples, financial fields, consent wizard, migration.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/memory/_helpers.py \
        src/forest_soul_forge/core/memory/__init__.py \
        tests/unit/test_memory_personal_scope.py \
        dev-tools/commit-bursts/commit-burst313-adr0068-t3-personal-scope.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0068 T3 - personal memory scope (B313)

Burst 313. Adds personal as the fifth memory scope, orthogonal
to the existing private/lineage/realm/consented ceiling-rank
ladder. Personal-scope is operator-bound context, eligible only
for genres in an explicit allow-list (companion, assistant,
operator_steward, domain_orchestrator).

What ships:

  - core/memory/_helpers.py: SCOPES + RECALL_MODES gain 'personal'.
    PERSONAL_SCOPE_ALLOWED_GENRES frozenset constant. _SCOPE_RANK
    gets a 'personal': 4 entry for KeyError safety even though
    the rank ladder doesnt apply to personal.

  - core/memory/__init__.py: Memory.append's genre check branches
    on scope='personal' into the allow-list lookup; non-allowed
    genres raise MemoryScopeViolation with the allowed-list
    spelled out. Memory.recall_visible_to handles mode='personal'
    as a NON-additive lookup returning ONLY scope='personal' rows
    across instance boundaries (operator-context isnt
    agent-private); the always-on private clause is suppressed
    so the readers private surface doesnt leak in.

Tests: test_memory_personal_scope.py - 11 cases covering
constants (personal in SCOPES + RECALL_MODES, canonical 4 in
allow-list), parametrized write-side allow-list (4 allowed + 4
refused + 1 unknown), read-side semantics (cross-instance
personal recall, reader-private exclusion, lineage/consented
orthogonality, scope persistence on disk).

Sandbox-verified all 11 scenarios end-to-end via in-memory
SQLite + v23 migrations.

Tool-layer genre gate (memory_recall.v1 BEFORE-check refusing
personal mode for non-allowlist genres) reads from the same
PERSONAL_SCOPE_ALLOWED_GENRES constant; that refactor lands in
a follow-on burst — current data layer is the source of truth.

ADR-0068 progress: 4/8 (T1 substrate + T1.1 ground-truth merge
+ T2 write tool + T3 personal scope). T4-T8 queued: trust
circle, voice samples, financial fields, consent wizard,
migration."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 313 complete - ADR-0068 T3 personal scope shipped ==="
echo ""
echo "Press any key to close."
read -n 1
