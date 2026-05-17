#!/bin/bash
# Burst 368 - section-04 + section-05: forged-tool-aware probes.
#
# Bug shape (surfaced by diagnostic-all on 2026-05-17):
#   section-04-tool-registration FAIL:
#     "no orphan registrations (1 extra) - translate_to_french.v1"
#     "catalog count == registered count - catalog=61, registered=62"
#   section-05-agent-inventory FAIL:
#     "Translator Sandbox - role=translator; tools not in catalog:
#      ['translate_to_french.v1']"
#
# Both fail for the same reason: translate_to_french.v1 is a forged
# tool installed via the ADR-0058 forge pipeline. Forged tools live
# at data/forge/tools/installed/<name>.v<ver>.yaml and are runtime-
# loaded by the daemon - they intentionally don't round-trip through
# the checked-in config/tool_catalog.yaml (operators install them
# without a code change). The two probes compared against the
# static catalog only, so the forged tool looked like drift.
#
# This is probe-design drift, not substrate drift. The fix is to
# teach the probes about the forged-tools bucket.
#
# Two-file fix:
#
#   section-04-tool-registration.command:
#     Loads data/forge/tools/installed/*.yaml into a forged_keys
#     set. known_keys = catalog ∪ forged. Orphan check uses
#     known_keys (so forged tools no longer look orphan). Count
#     check becomes "catalog + forged count == registered count"
#     to reflect both buckets. Adds a "forged tools catalogued"
#     PASS line that surfaces what runtime-installed tools are
#     live - operator gets one place to read what the forge
#     pipeline added.
#
#   section-05-agent-inventory.command:
#     Loads the same forged-tools bucket and merges into tool_keys
#     + tool_side_effects so the per-agent kit check accepts forged
#     entries. The ceiling check still applies (a forged tool's
#     side_effects must still respect the agent's genre ceiling).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: section-04 + section-05 FAIL today on intended
#     ADR-0058 pipeline output. Polluted summary.
#   Prove non-load-bearing: only relaxes a false-positive class;
#     genuine drift (registered tool that's not in catalog AND not
#     in forged installed dir) still surfaces as FAIL. Genre
#     ceiling check still applies.
#   Prove alternative is strictly better: leaving in place
#     punishes every operator who installs a forged tool with
#     two daily FAILs - that's exactly the regression the forge
#     pipeline was designed not to cause.
#
# Verification after this commit lands:
#   1. Re-run section-04-tool-registration.command - both translate_
#      to_french FAILs become PASS; new "forged tools catalogued"
#      PASS line surfaces translate_to_french.v1.
#   2. Re-run section-05-agent-inventory.command - Translator
#      Sandbox FAIL becomes PASS (the three constitution-parse FAILs
#      remain - those are B369 territory).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-04-tool-registration.command \
        dev-tools/diagnostic/section-05-agent-inventory.command \
        dev-tools/commit-bursts/commit-burst368-forged-tool-aware-sections-04-05.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(harness): forged-tool-aware sections 04 + 05 (B368)

Burst 368. Close translate_to_french false-positives across
section-04 + section-05.

ADR-0058 forge pipeline installs tools to
data/forge/tools/installed/<name>.v<ver>.yaml without round-
tripping through config/tool_catalog.yaml. Pre-B368 the harness
treated forged tools as catalog drift, FAILing on every install.

Two-file fix:

section-04-tool-registration.command:
  Loads forged bucket into forged_keys. known_keys = catalog ∪
  forged. Orphan check uses known_keys. Count check becomes
  'catalog + forged count == registered count'. New 'forged
  tools catalogued' PASS line surfaces runtime-installed tools.

section-05-agent-inventory.command:
  Merges forged-tool definitions into tool_keys + tool_side_
  effects so per-agent kit checks accept forged entries. Genre
  ceiling check still applies (forged tool's side_effects must
  still respect the agent's genre ceiling).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 3 FAILs today on intended pipeline output.
  Prove non-load-bearing: only relaxes a false-positive class;
    genuine drift still surfaces; ceiling check intact.
  Prove alternative is better: leaving in place punishes every
    operator who installs a forged tool.

After this lands:
  - section-04 drops 2 FAILs (orphan + count mismatch).
  - section-05 drops 1 FAIL (Translator Sandbox).
  - section-05 still flags the 3 constitution-parse failures
    (Kraine/Victor/chaz) which are B369 territory."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 368 complete - forged-tool awareness ==="
echo "=========================================================="
echo "Re-test:"
echo "  dev-tools/diagnostic/section-04-tool-registration.command"
echo "  dev-tools/diagnostic/section-05-agent-inventory.command"
echo "Expected:"
echo "  section-04: translate_to_french FAILs gone; new 'forged"
echo "  tools catalogued' PASS line; remaining FAILs (none)."
echo "  section-05: Translator Sandbox PASS; 3 constitution-parse"
echo "  FAILs remain (B369 territory)."
echo ""
echo "Press any key to close."
read -n 1 || true
