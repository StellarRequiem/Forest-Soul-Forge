#!/bin/bash
# Burst 256.1 — HOTFIX: reality_anchor trait weight floor.
#
# B253 added reality_anchor to trait_tree.yaml with
# emotional=0.3 and embodiment=0.3 — both BELOW the trait-tree
# validator's [0.4, 3.0] range. The validator silently failed
# to load the engine at lifespan, leaving app.state.trait_engine
# = None, which made every /birth call return 503.
#
# Symptom: 11 of this session's pytest cases failed with
# "503 Service Unavailable" — all in test_reality_anchor_role.py
# (the only file that births a reality_anchor) + the
# downstream test_install_scanner cases (which depend on the
# write_env fixture).
#
# Diagnosed via standalone driver invoking TraitEngine()
# directly:
#   SchemaError: Role 'reality_anchor' weight for 'emotional'
#                (0.3) outside [0.4, 3.0]
#
# Fix: clamp both weights to 0.4 (the validator floor). The
# semantic intent — reality_anchor is a cold-logic verifier
# with minimal emotional/embodiment surface — is preserved.
# Comment added in-file pointing at the validator constraint
# so future operators don't repeat the slip.
#
# Why this didn't surface earlier in the session: the
# verify_claim.v1 substrate (B251) + RealityAnchorStep (B252)
# + reality_anchor_corrections (B255) work without ever needing
# to /birth a reality_anchor — they're substrate-layer. T4's
# birth path is the FIRST code path that loads the trait
# engine with the new role definition. The host's running
# daemon either started before B253's catalog landed (no
# trait engine load) or restarted under the broken catalog
# and silently downgraded — operator wouldn't notice until a
# reality_anchor birth was attempted.
#
# Validation:
#   - TraitEngine('config/trait_tree.yaml') loads cleanly with
#     45 roles; reality_anchor + verifier_loop both present.
#   - diag-anchor-birth.command's /birth call should now
#     return 201 instead of 503.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        dev-tools/commit-bursts/commit-burst256-1-trait-weight-floor.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(reality-anchor): clamp trait weights to validator floor (B256.1 hotfix)

Burst 256.1 hotfix. B253 added reality_anchor to trait_tree
.yaml with emotional=0.3 + embodiment=0.3 — both below the
validator's [0.4, 3.0] range. The trait engine silently
failed to load at lifespan; every /birth returned 503; 11
pytest cases broke; the running host daemon kept serving
because it had loaded the pre-B253 catalog and never re-
checked.

Diagnosed via standalone TraitEngine() driver:
  SchemaError: Role 'reality_anchor' weight for 'emotional'
               (0.3) outside [0.4, 3.0]

Fix: clamp both weights to 0.4 (the floor). Semantic intent
preserved — reality_anchor is a cold-logic verifier with
minimal emotional/embodiment surface. Added an inline
comment pointing at the validator constraint to prevent
future drift.

Post-fix: TraitEngine() loads 45 roles cleanly; reality_anchor
+ verifier_loop both present. /birth role=reality_anchor will
return 201 once the daemon picks up the new catalog
(force-restart-daemon.command).

Per CLAUDE.md verification discipline: the bug only surfaced
because we ran the full pytest suite after the session — the
substrate code (B251-B252-B255) worked end-to-end via standalone
drivers since it never needed to birth a reality_anchor agent.
B253 was the first tranche that exercised that path, and
its standalone smoke (commit-burst253) didn't include a /birth
call against a fresh trait engine."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 256.1 hotfix complete ==="
echo "=== After this, run force-restart-daemon.command to reload the catalog. ==="
echo "Press any key to close."
read -n 1
