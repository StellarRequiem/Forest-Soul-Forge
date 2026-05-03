#!/usr/bin/env bash
# Burst 84: tag v0.3.0 — annotated release tag.
#
# Locks in the v0.3 arc (ADR-0036 + ADR-0040 + audit + remediation +
# Run 001) before v0.4 work begins. CHANGELOG.md [0.3.0] section was
# filed in Burst 83.
#
# Why a separate burst from Burst 83: tag commits are different concern
# from doc-refresh commits. Mixing them obscures the change history.
# Per project conventions: one burst, one coherent change.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 84 — tag v0.3.0 (annotated release) ==="
echo

# Pre-flight: confirm CHANGELOG has the [0.3.0] section
if ! grep -q "^## \[0.3.0\]" CHANGELOG.md; then
  echo "ABORT: CHANGELOG.md does not have a [0.3.0] section."
  echo "  Expected from Burst 83. Run that first."
  echo ""; echo "Press return to close."; read -r _
  exit 1
fi
echo "✓ CHANGELOG [0.3.0] section present"

# Pre-flight: confirm no tag named v0.3.0 already
if git rev-parse v0.3.0 >/dev/null 2>&1; then
  echo "ABORT: tag v0.3.0 already exists."
  echo ""; echo "Press return to close."; read -r _
  exit 1
fi
echo "✓ tag v0.3.0 does not yet exist"

# Pre-flight: clean working tree (no uncommitted changes that should be in the release)
DIRTY=$(git status --porcelain | grep -vE "examples/audit_chain.jsonl" | grep -v "tag-v0.3.0.command" || true)
if [[ -n "$DIRTY" ]]; then
  echo "ABORT: working tree has uncommitted changes:"
  echo "$DIRTY"
  echo ""; echo "Press return to close."; read -r _
  exit 1
fi
echo "✓ working tree clean (besides live audit chain + this script)"

clean_locks
git add tag-v0.3.0.command
clean_locks
git commit -m "release: add tag-v0.3.0.command release script"
clean_locks

echo
echo "Creating annotated tag v0.3.0..."
clean_locks
git tag -a v0.3.0 -m "v0.3.0 — ADR-0036 Verifier Loop + ADR-0040 Trust-Surface Decomposition

The v0.3 arc shipped two distinct ADRs end-to-end across 18 commits.

ADR-0036 Verifier Loop (Bursts 65-70):
Verifier-class agent for scanning agent memory and flagging
contradictions for operator review. T1-T7 implemented (T4 scheduled-
task substrate deferred to v0.4). Test count grew 1968 -> 2072
(+104). Schema v11 -> v12 with flagged_state column on
memory_contradictions for ADR-0036 T6 ratification dial.

ADR-0040 Trust-Surface Decomposition Rule (Bursts 71-81):
New project discipline — count trust surfaces, not LoC. A file with
multiple governance surfaces MUST decompose so allowed_paths can
scope grants. Proven by decomposing both non-cohesive god objects:
- memory.py -> memory/ package (5 mixins + helpers + facade)
- writes.py -> writes/ package (3 sub-routers + shared + facade)
The rule pattern-matched cleanly across class-based AND router-based
decomposition shapes. Anchored in CLAUDE.md as §1 operating
principle (peer to §0 Hippocratic gate).

Audit + remediation (Bursts 82-83):
docs/audits/2026-05-03-full-audit.md — full sweep triggered by Run
001's audit-chain path mystery. Found and remediated: README/STATE
numeric drift across the entire v0.3 arc (test count, LoC, commits,
.command count, ADR count, trait roles, audit events all stale or
wrong); CHANGELOG missing entire arc; audit chain default path
(examples/audit_chain.jsonl) never documented in published docs;
13 zombie test agents accumulated in registry. All P0/P1 findings
remediated before this tag. dev-tools/check-drift.sh committed as
the future-proofing sentinel.

Run 001 — first autonomous coding-loop test:
Forest tool dispatch infrastructure successfully drove a local
Ollama (qwen2.5-coder:7b) in an iterative build loop end-to-end
on FizzBuzz (4/4 tests pass in 2 turns, 15 sec wall). Captured 5
driver bugs in live-test-fizzbuzz.command for future scenario runs.
This validates the runtime substrate before v0.4 app/orchestrator
work begins.

Test suite: 2072 passing, 3 skipped, 1 xfailed. Zero regressions.

What's NOT in v0.3.0 (deferred to v0.4):
- ADR-0036 T4 — set-and-forget orchestrator (scheduled-task substrate)
- ADR-0041 — agent self-timing tool family (drafted post-v0.3.0)
- v0.4 app platform (Tauri desktop + harness-bridge)"

echo
clean_locks
git push origin main
clean_locks
git push origin v0.3.0
clean_locks
git log -1 --oneline
echo
echo "v0.3.0 tagged + pushed."
git tag --sort=-version:refname | head -5
echo ""
echo "Burst 84 complete. Release locked in."
echo "Next: Burst 85 — ADR-0036 T4 implementation (set-and-forget orchestrator)."
echo ""
read -rp "Press Enter to close..."
