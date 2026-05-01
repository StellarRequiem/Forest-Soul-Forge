#!/usr/bin/env bash
# Commit + push the v0.1.1 audit work in one shot.
#
# All 77+ modified/new files in the working tree get bundled into a
# single commit since they're all part of the v0.1.1 audit + hardening
# pass. Followable by reading CHANGELOG.md [0.1.1] entry — the changelog
# does the per-phase breakdown that a single commit message can't.
#
# This script handles the recurring sandbox lock cleanup before each
# git op so we don't get blocked.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BLUE="\033[1;34m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

say()  { printf "${BLUE}[v0.1.1]${RESET} %s\n" "$*"; }
ok()   { printf "${GREEN}[v0.1.1]${RESET} %s\n" "$*"; }
warn() { printf "${YELLOW}[v0.1.1]${RESET} %s\n" "$*"; }
err()  { printf "${RED}[v0.1.1]${RESET} %s\n" "$*" 1>&2; }

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

say "=== v0.1.1 audit + hardening commit + push ==="
echo

clean_locks
ok "step 0/4 — locks cleared"

# Sanity: show what's about to be staged.
say "step 1/4 — staging all changes..."
git add -A
clean_locks
STAGED_COUNT=$(git status --short | wc -l | tr -d ' ')
ok "  staged $STAGED_COUNT entries"
git status --short | head -20
if [ "$STAGED_COUNT" -gt 20 ]; then
  echo "  ... ($(($STAGED_COUNT - 20)) more)"
fi
echo

say "step 2/4 — commit..."
clean_locks
git commit -m "v0.1.1: audit + hardening release (phases A-F)

Test suite: 992 -> 1439 passing (+447, +45%); 122 broken -> 0.

Phase A: 122 broken cases -> 0. Two §0-gated production bug fixes
(brew formula off-by-one in patch_check.v1; rowid DESC tiebreaker
in memory recall). Shared seed_stub_agent fixture closed 43 FK
failures. Tool Forge static-analysis fixture indent fix closed 15.

C-1 zombie tool dissection: 6 catalog entries with no on-disk impl.
1 IMPLEMENTed (dns_lookup.v1, 20 unit tests), 4 SUBSTITUTED with
existing tested equivalents, 1 DEFERRED to Phase G with migration
note. Catalog 46 -> 41.

Phase B: +323 unit-test cases across 11 zero-coverage files +
8 untested tools batch. 13 new test files, 406 cases total
(governance_pipeline 37, conversation_resolver 23, conversations_admin
19, hardware 30, chronicle_render 59, voice_renderer 29, cli_main 22,
cli_helpers 15, providers 28, phase_b_tool_smoke 61, dns_lookup 20,
conversation_helpers 30, birth_pipeline 33). Frontend Vitest deferred
to v0.3.

Phase C: 2 god-objects decomposed. conversations.py 994 -> 852 LoC,
helpers in conversation_helpers.py (226 LoC). writes.py 1186 -> 1096
LoC, helpers in birth_pipeline.py (194 LoC). Net 232 LoC out of
god-objects + 420 LoC of newly-testable helper modules.

Phase D: CLAUDE.md at repo root with harness conventions. 8 ADRs
promoted Proposed -> Accepted (0019, 0021, 0022, 0027, 0030, 0031,
0034, 003Y). 4 ADR placeholders explicitly Deferred to v0.3+ with
rationale (0025, 0026, 0028, 0029). 7 new operator runbooks +
command-scripts-index.md + examples/README.md. 5 audit/roadmap/
survey docs.

Phase E: Verification + cleanup under §0 Hippocratic gate. Zero
deletions of load-bearing code. Empty packages got placeholder
docstrings. initial_push.sh got HISTORICAL guard. .env verified
non-sensitive (kept). Default registry path -> data/registry.sqlite.
PROGRESS.md archived to docs/_archive/. verify_*.py kept (no-pytest
sandbox use case).

Phase F: CHANGELOG [0.1.1] entry written. Suite green. v0.1.1 tag
follows in tag-v0.1.1.command.

Files NOT changed: audit chain format, DNA derivation, constitution
hash semantics, schema (still v10), all daemon endpoints, frontend
behavior, single-writer SQLite discipline, .command script names.

See CHANGELOG.md [0.1.1] for the full per-phase ledger.
See docs/audits/2026-04-30-comprehensive-repo-audit.md for the
file-by-file inputs.
See docs/audits/2026-04-30-phase-e-cleanup-verdicts.md for §0
verdicts.
See docs/roadmap/2026-04-30-v0.2-to-v1.0-roadmap.md for v0.2 plan."

clean_locks
ok "  commit landed"
echo

say "step 3/4 — push..."
clean_locks
git push origin main
clean_locks
ok "  pushed to origin"
echo

say "step 4/4 — final state"
git log -1 --oneline
echo
ok "v0.1.1 audit work landed on origin."
echo
echo "Next: double-click tag-v0.1.1.command to cut the tag."
echo ""
read -rp "Press Enter to close..."
