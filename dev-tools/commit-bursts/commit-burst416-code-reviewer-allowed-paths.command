#!/bin/bash
# Burst 416 - code_reviewer template gets allowed_paths for code_read.v1.
#
# Option C (Reviewer-Main weekly code_review_quick.v1) failed on
# first verify with code_read.v1 raising CodeReadError because the
# constitution had no allowed_paths constraint. code_read enforces
# the allowlist at dispatch time — empty allowlist = refuse to
# touch any path.
#
# Fix: add tool_constraints.code_read.v1.allowed_paths default to
# the code_reviewer role_base template. Every future code_reviewer
# birth gets the allowlist baked in. The list covers the production
# code surface a reviewer needs to walk:
#   src/             — Forest's own Python
#   frontend/js/ + css/  — operator-facing UI
#   dev-tools/       — wrapper scripts + diagnostic + commit bursts
#   docs/            — ADRs + runbooks + audits
#   tests/           — pytest tree
#   examples/skills/ — skill manifests
#   config/          — substrate config (catalog, trait_tree, etc.)
#
# Explicitly EXCLUDED:
#   secrets/, .env*           — credentials
#   soul_generated/           — lineage memory bodies
#   data/registry.sqlite*     — single source of truth for agents
#   examples/audit_chain.jsonl — tamper-evidence chain (read via
#                                audit_chain_verify tool, not raw)
#
# The forbid_implementation policy at the top of the template
# still prevents writes; this commit only adds READ surface.
#
# Operator next step: existing Reviewer-Main was born BEFORE this
# template change. Per the constitution-immutability invariant
# (CLAUDE.md sec0), Reviewer-Main's constitution is bound to its
# identity hash and won't pick up the new allowed_paths. To enable
# Option C scheduled cadence:
#
#   curl -X POST http://127.0.0.1:7423/agents/<reviewer_main_id>/archive \
#     -H "X-FSF-Token: $FSF_API_TOKEN"
#   # then re-birth via dev-tools/birth-triune-main.command (idempotent;
#   # only the archived Reviewer-Main rebirths)
#
# Or — operator-driven posture patch to inject allowed_paths into
# the existing constitution (avoids re-birth). That's per-operator
# preference. The clean rebirth aligns with the same-identity-hash
# discipline used in B376 for chaz/Kraine/Victor.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: Option C scheduled task DEAD-ON-ARRIVAL. Reviewer-
#     Main was born but its code_read can't touch any file.
#   Prove non-load-bearing: template ADDITION only. Existing
#     Reviewer-Main unaffected (immutability invariant); future
#     code_reviewer births get the allowlist.
#   Prove alternative: per-instance constraint patch — works but
#     needs a custom endpoint; not the long-term shape. Add to
#     template = single source of truth for all future code_reviewer
#     births including any rebirth of Reviewer-Main.
#
# Verification after this commit lands:
#   1. python3 -c "import yaml; d=yaml.safe_load(open('config/constitution_templates.yaml'));
#                  print(d['role_base']['code_reviewer']['tool_constraints'])"
#      Expected: dict with code_read.v1.allowed_paths populated.
#   2. (Operator) archive + re-birth Reviewer-Main.
#   3. bash dev-tools/run-reviewer-review.command
#      Expected: status=succeeded; review of one production .py file
#      lands in Reviewer-Main lineage.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/constitution_templates.yaml \
        dev-tools/commit-bursts/commit-burst416-code-reviewer-allowed-paths.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(role): code_reviewer allowed_paths for code_read (B416)

Burst 416. Option C (Reviewer-Main weekly code_review_quick.v1)
failed on first verify — code_read.v1 raised CodeReadError because
the constitution had no allowed_paths constraint. code_read enforces
allowlist at dispatch; empty = refuse.

Fix: add tool_constraints.code_read.v1.allowed_paths default to
code_reviewer role_base template. Production-code surface:
  src/ frontend/js/ frontend/css/ dev-tools/ docs/ tests/
  examples/skills/ config/

Excluded: secrets/, .env*, soul_generated/, data/registry.sqlite*,
examples/audit_chain.jsonl. The forbid_implementation policy still
prevents writes; this only adds READ access.

Operator next step: existing Reviewer-Main was born before this
change. Per constitution-immutability invariant, it doesn't pick
up new template defaults. To enable Option C:
  archive + re-birth Reviewer-Main via birth-triune-main.command
(B376 pattern for chaz/Kraine/Victor).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: Option C dead-on-arrival without allowed_paths.
  Prove non-load-bearing: template ADDITION; existing Reviewer-Main
    unaffected. Future births get the allowlist.
  Prove alternative: per-instance constraint patch needs custom
    endpoint; template = single source of truth for rebirths."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 416 complete - code_reviewer allowed_paths ==="
echo "=========================================================="
echo "Next: operator archive + re-birth Reviewer-Main to enable Option C."
echo "Press any key to close."
read -n 1 || true
