#!/usr/bin/env bash
# Burst 425 — fix wrong archive endpoint in rebirth-reviewer-main.command.
#
# Context (this is the hotfix to B420)
# ------------------------------------
# B420 shipped `dev-tools/rebirth-reviewer-main.command` to enable
# Option C (Reviewer-Main weekly code_review_quick scheduled task)
# by archiving the existing Reviewer-Main + re-running birth-triune-
# main so the new template's `allowed_paths` defaults (added in
# B416) would land in the rebirth constitution.
#
# Live verification 2026-05-19 14:48 surfaced two issues:
#
#   Bug 1 (THIS BURST FIXES): the rebirth script's step [2/3]
#     POSTed to `${DAEMON}/agents/${EXISTING}/archive` — a path that
#     does not exist. The daemon returned `{"detail":"Not Found"}`
#     (HTTP 404). The script logged the response but did NOT exit;
#     it continued blindly to step [3/3] (birth-triune-main), which
#     found Reviewer-Main still alive in the registry and skipped
#     birth. Net effect of the original run: zero state change.
#
#   Bug 2 (DEEPER, NOT FIXED BY THIS BURST): even after Bug 1 is
#     fixed and archive succeeds, the rebirth cannot pick up B416's
#     allowed_paths defaults. `instance_id = role + dna_short`;
#     DNA is derived from trait_profile (role + trait values +
#     domain weights). Same trait_profile -> same DNA -> same
#     instance_id, which is the agents-table PRIMARY KEY. A fresh
#     birth with unchanged trait profile produces the same
#     instance_id, conflicts with the PK, and the idempotency
#     cache from the original 07:31 birth replays. This is now a
#     documented architectural-bug-discovery trigger per ADR-0082's
#     exception path. Resolution is a separate ADR.
#
# What this burst does
# --------------------
# Patches the archive call in rebirth-reviewer-main.command:
#
#   Endpoint: /agents/{id}/archive  ->  /archive
#   Body:     {"reason": "..."}      ->  {"instance_id": "...",
#                                         "reason": "...",
#                                         "archived_by": "alex"}
#   Failure handling: log + continue  ->  exit 1 if archive fails
#
# Verification path cited inline in the patched script:
#   - src/forest_soul_forge/daemon/routers/writes/archive.py:80
#     (@router.post("/archive"))
#   - src/forest_soul_forge/daemon/app.py:1226 (no prefix mount)
#   - src/forest_soul_forge/daemon/schemas/agents.py:196
#     (ArchiveRequest schema)
#   - docs/audits/2026-05-17-quarantine-rebirth.md (B376 lineage
#     record explicitly says "POST /archive with instance_id=<old>")
#
# Re-run verification at 2026-05-19 18:48 with the patched script:
#   - Archive call succeeded (Status: archived)
#   - audit chain seq=19177 agent_archived event emitted
#   - Bug 2 surfaced (rebirth skipped on DNA collision) — now an
#     ADR-0082 documented trigger for a future kernel-extension ADR
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: B420 helper is broken on the archive call path;
#    every future operator running it sees the same silent no-op.
#    The bug also caused real damage in this session — Reviewer-
#    Main got archived (when retried after the patch) without a
#    valid replacement. The script needs the path fix regardless
#    of the deeper rebirth-impossibility issue, so future operators
#    at least see a clean failure mode instead of silent no-op.
# 2. Prove non-load-bearing: this is a script-level patch. No
#    kernel code touched. The script lives in dev-tools/, used
#    only via operator opt-in. The exit-1-on-failure adds a safety
#    rail.
# 3. Prove alternative: leaving the script broken is the status
#    quo and dangerous (silent failure mode). Revert the script
#    entirely — rejected because B416 + Option C still need a
#    working rebirth path eventually, even if Bug 2 means today's
#    rebirth-of-Reviewer-Main fails differently.
#
# This burst does NOT fix the broken Triune-Main state. Reviewer-
# Main remains archived; Engineer-Main + Architect-Main remain
# alive. Resolution path is documented in ADR-0082's exception
# path (architectural bug discovery trigger).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 425 — rebirth helper endpoint fix (B420 hotfix)"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add dev-tools/rebirth-reviewer-main.command
git add dev-tools/commit-bursts/commit-burst425-rebirth-helper-endpoint-fix.command

echo "Pre-commit status:"
git status -s | head -10
echo

git commit -m "fix(triune): rebirth helper archive endpoint path (B425 / B420 hotfix)

B420's dev-tools/rebirth-reviewer-main.command archive step posted to
\${DAEMON}/agents/{id}/archive — a path that doesn't exist. Daemon
returned {\"detail\":\"Not Found\"}; script continued blindly to step
[3/3] birth-triune-main which found agent still alive and skipped.
Silent no-op.

Real endpoint: POST /archive (writes router, root-mounted) with
instance_id IN BODY per ArchiveRequest schema (instance_id +
reason required, archived_by optional).

Patch:
  Endpoint: /agents/{id}/archive  ->  /archive
  Body:     adds instance_id, archived_by: 'alex'
  Failure: log + continue  ->  exit 1 if archive fails (no more
           silent no-ops; future operator gets clean failure)

Re-run with patched script (2026-05-19 18:48): archive succeeded,
audit chain seq=19177 agent_archived event emitted. Surfaced a
DEEPER bug (Bug 2 in the inline comment) where same-DNA agents
can't pick up template-default changes via rebirth because
instance_id is the agents-table PRIMARY KEY and DNA derivation
is purely from trait_profile. That deeper bug is now documented
as an ADR-0082 architectural-bug-discovery trigger; resolution
is a separate ADR.

Triune-Main state: Engineer-Main + Architect-Main alive,
Reviewer-Main archived (no live replacement yet). Daily 7am
wiring_audit_triage scheduled task will run with two of three
agents until the deeper bug gets its own ADR.

Verification path cited inline:
  src/forest_soul_forge/daemon/routers/writes/archive.py:80
  src/forest_soul_forge/daemon/app.py:1226
  src/forest_soul_forge/daemon/schemas/agents.py:196
  docs/audits/2026-05-17-quarantine-rebirth.md

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: silent no-op + observed real damage this session.
  Prove non-load-bearing: script-only patch; no kernel touched.
  Prove alternative: leaving broken = future operators repeat
    the same trap. Revert entirely rejected — B416/Option C
    still need a working rebirth path eventually.

Closes B420 archive-path bug. Does NOT close B416 fully — that
remains gated on Bug 2 resolution (separate ADR)." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done."
echo
echo "Press any key to close."
read -n 1 || true
