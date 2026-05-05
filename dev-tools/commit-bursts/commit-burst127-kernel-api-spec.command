#!/bin/bash
# Burst 127 — ADR-0044 P2: formal kernel API spec.
#
# The next major milestone after the v0.5.0 close. ADR-0044 Decision
# 3 enumerated seven kernel ABI surfaces; KERNEL.md (Burst 119) gave
# the elevator-pitch overview. This burst delivers the contract-
# grade specification: field-by-field schemas, per-endpoint error
# envelopes, governance pipeline step-by-step semantics, the full
# 70-event-type audit chain catalog organized by subsystem, and an
# explicit ABI-compatibility commitment table separating pre-v1.0
# discipline from post-v1.0 freeze.
#
# Stands on:
#   - ADR-0044 Phase 1 (boundary doc + KERNEL.md + sentinel,
#     Bursts 118-120)
#   - ADR-0044 Phase 5 (license + governance, Burst 121)
#   - ADR-0044 Phase 5.1 (CONTRIBUTING + CoC, Burst 122)
#   - Burst 124 role inventory expansion (18 -> 42 roles)
#   - Burst 125 STATE.md refresh
#   - Burst 126 housekeeping (verifier_loop archetype + Phase G
#     ownership clarification + audit chain sync)
#
# What ships:
#
#   docs/spec/kernel-api-v0.6.md (new, 1042 lines) — the formal
#     spec. Ten sections:
#
#     §0 Scope and conventions
#         §0.1 What this document covers (the seven ABI surfaces)
#         §0.2 What this document does NOT cover (internals,
#              userspace, performance characteristics)
#         §0.3 Status (pre-v1.0 draft, will freeze at Phase 6
#              external integrator validation per ADR-0044
#              Decision 4)
#         §0.4 Versioning policy (kernel impl / spec / tool catalog /
#              registry schema / plugin manifest / audit chain
#              versions all version independently)
#         §0.5 Error envelope discipline (HTTP envelope / CLI exit
#              codes / DispatchOutcome dataclass) — three uniform
#              shapes, kebab-case codes never renamed without major
#              bump
#
#     §1 Tool dispatch protocol
#         §1.1 ToolDispatcher.dispatch() signature (11 kwargs)
#         §1.2 DispatchOutcome dataclass shape (4 variants)
#         §1.3 Governance pipeline step ordering (12 steps; adding
#              non-breaking, reordering breaking)
#         §1.4 mcp_call.v1 contract (input/output/registry shapes)
#
#     §2 Audit chain schema
#         §2.1 JSONL line shape (7 top-level fields, ordered)
#         §2.2 Hash discipline (canonical-JSON sha256 chain)
#         §2.3 Append-only contract (no rewrites; fsync on append)
#         §2.4 Event type catalog — full 70-event-type list
#              organized by subsystem (lifecycle / dispatch / skill /
#              memory / cross-agent / conversation / verification /
#              scheduler / plugin / posture+grants / open-web /
#              hardware / triune / misc)
#         §2.5 Audit chain versioning (no version field; format
#              hasn't changed since v0.1; future bump = major)
#
#     §3 Plugin manifest schema v1
#         §3.1 plugin.yaml top-level shape
#         §3.2 Field-level rules (regex for name, semver for
#              version, side_effects worst-case posture, trust_tier
#              0-5, required_secrets gating)
#         §3.3 entry_point sub-shape (stdio | http; sha256 trust
#              boundary)
#         §3.4 Validation error envelope
#
#     §4 Constitution.yaml schema
#         §4.1 Top-level fields (schema v2 since ADR-0042)
#         §4.2 Constitution hash invariant (immutable per agent;
#              mutable state lives on agents SQL row, not in
#              constitution)
#         §4.3 Schema versioning
#
#     §5 HTTP API contract
#         §5.1 Auth model (X-FSF-Token + writes-enabled gate +
#              CORS)
#         §5.2 Idempotency (X-Idempotency-Key per-endpoint)
#         §5.3 Endpoint catalog (read) — 27 endpoints
#         §5.4 Endpoint catalog (write, gated) — 25 endpoints
#         §5.5 OpenAPI (auto-generated at /openapi.json,
#              normative)
#         §5.6 HTTP error envelope (status + code mapping table)
#
#     §6 CLI surface
#         §6.1 Subcommand tree (forge / install / triune /
#              chronicle / plugin / agent posture)
#         §6.2 Common flags (--daemon-url, --api-token, --json)
#         §6.3 Exit codes (0/4/5/6/7 per plugins/errors.py)
#         §6.4 Auth fallback chain (--api-token → $FSF_API_TOKEN →
#              anon)
#
#     §7 Schema migrations
#         §7.1 Strict-additive forward migration policy
#         §7.2 Current schema version (v15) + per-version landing
#              table (v1 through v15 with ADR refs)
#         §7.3 Migration file format (idempotent DDL,
#              schema_meta update, audit-chain agnostic)
#         §7.4 Audit chain interaction (chain canonical, registry
#              rebuildable; rebuild_from_artifacts escape hatch)
#
#     §8 ABI compatibility commitments
#         §8.1 Pre-v1.0 (now): ADR + bump signal + spec update for
#              breaking; ADR + CHANGELOG for non-breaking
#         §8.2 v1.0 (future): external integrator validation
#              triggers freeze; major bump for breaking; one minor
#              cycle of deprecation
#         §8.3 Post-v1.0 (theoretical v2): coexistence window
#
#     §9 Conformance
#         Pointer to ADR-0044 Phase 4 (conformance test suite);
#         current closest enforceable check is the unit suite
#         (2,386 tests at v0.5.0)
#
#     §10 Open questions for v1.0 freeze
#         6 specific decisions to resolve during the runway: HTTP
#         API path versioning (versioned vs unversioned), CLI
#         subcommand stability vs extension, MCP plugin types
#         beyond mcp_server, event-type catalog ossification, schema
#         version vs spec version coupling, constitution
#         schema_version v3 or stay at v2.
#
#   docs/architecture/kernel-userspace-boundary.md — one row update:
#     `docs/spec/` row no longer says "Empty at v0.6"; now points
#     at kernel-api-v0.6.md and references the future-versioned-
#     file convention.
#
#   KERNEL.md — References section gets a new top entry pointing
#     at the spec, framed as "read this when you need contract-
#     grade detail beyond KERNEL.md's overview."
#
# Verification:
#   - Full unit suite: 2,386 passing, 3 skipped (sandbox-only),
#     1 xfail (v6→v7 SQLite migration, pre-existing per Phase A
#     audit finding F-7). Pure docs commit; zero code touched, zero
#     regressions expected.
#   - Spec is 1,042 lines; covers all seven surfaces enumerated in
#     ADR-0044 Decision 3 + KERNEL.md.
#   - Audit chain hashes still link cleanly through entry 1121.
#
# What this closes:
#   ADR-0044 P2 work item lands. The kernel API now has a contract-
#   grade specification document; future surface changes are
#   audited against this spec.
#
# What this opens:
#   - P3 headless + SoulUX split (separate kernel package from
#     reference distribution).
#   - P4 conformance test suite (the test pack that operationalizes
#     this spec).
#   - P6 first external integrator validation (the v1.0 freeze
#     gate per ADR-0044 Decision 4).
#   - 6 open questions in §10 that need resolution during the
#     runway.
#
# Untracked .command scripts at repo root remain for Alex's
# archival decision (commit under dev-tools/commit-bursts/,
# .gitignore, or delete); none of them are load-bearing for v0.6
# kernel arc work.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/spec/kernel-api-v0.6.md \
        docs/architecture/kernel-userspace-boundary.md \
        KERNEL.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(spec): formal kernel API spec v0.6 (ADR-0044 P2, B127)

Burst 127. Closes ADR-0044 Phase 2: contract-grade specification of
all seven kernel ABI surfaces. Stands on Phase 1 (boundary doc +
KERNEL.md + sentinel, Bursts 118-120) and Phase 5 (license + CoC,
Bursts 121-122).

Ships docs/spec/kernel-api-v0.6.md (1,042 lines) with ten sections:

  §0  Scope, versioning policy, error envelope discipline (3
      uniform envelope shapes; kebab-case codes never renamed
      without major bump)
  §1  Tool dispatch protocol — ToolDispatcher.dispatch() 11-kwarg
      signature, DispatchOutcome 4-variant dataclass, 12-step
      governance pipeline ordering (adding non-breaking,
      reordering breaking), mcp_call.v1 input/output/registry
      contracts
  §2  Audit chain schema — JSONL 7-field line shape, hash
      discipline (canonical-JSON sha256 chain), append-only
      contract, full 70-event-type catalog organized by 14
      subsystems
  §3  Plugin manifest schema v1 — plugin.yaml shape, field rules,
      entry_point sub-shape with sha256 trust boundary
  §4  Constitution.yaml schema v2 — top-level fields, hash
      invariant (mutable state lives on agents SQL row, not in
      constitution)
  §5  HTTP API contract — auth model (X-FSF-Token + writes-
      enabled), idempotency, 27 read + 25 write endpoints,
      OpenAPI normativity, status + code mapping table
  §6  CLI surface — subcommand tree, common flags, exit codes
      (0/4/5/6/7 per plugins/errors.py), auth fallback chain
  §7  Schema migrations — strict-additive policy, v1→v15 landing
      table with ADR refs, audit-chain-as-source-of-truth
      invariant
  §8  ABI compatibility commitments — pre-v1.0 (ADR + bump
      signal) vs. v1.0 (major bump + deprecation cycle) vs.
      post-v1.0 v2 coexistence
  §9  Conformance — pointer to ADR-0044 Phase 4 (conformance
      test suite); current closest check is unit suite
  §10 Six open questions for v1.0 freeze — HTTP API path
      versioning, CLI subcommand stability vs extension, MCP
      plugin types beyond mcp_server, event-type catalog
      ossification, schema/spec version coupling, constitution
      schema_version v3 decision

Also updates:

- docs/architecture/kernel-userspace-boundary.md — docs/spec/ row
  no longer says 'Empty at v0.6'; points at kernel-api-v0.6.md
  and references the future version-tagged-file convention.
- KERNEL.md — References section gains a top entry pointing at
  the spec, framed as 'read this when you need contract-grade
  detail beyond KERNEL.md's overview.'

Verification:
- Full unit suite: 2,386 passing, 3 skipped (sandbox-only), 1
  xfail (v6→v7 SQLite migration, pre-existing). Pure docs commit;
  zero code touched.
- Spec covers all seven surfaces enumerated in ADR-0044
  Decision 3 + KERNEL.md (tool dispatch / audit chain / plugin
  manifest / constitution / HTTP / CLI / schema migrations).
- Audit chain hashes link cleanly through entry 1121.

Closes ADR-0044 P2. Opens P3 (headless + SoulUX split), P4
(conformance test suite), P6 (first external integrator
validation — the v1.0 freeze gate per ADR-0044 Decision 4) plus
the 6 open questions in §10."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 127 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
