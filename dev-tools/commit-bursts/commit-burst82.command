#!/usr/bin/env bash
# Burst 82: full audit + drift sentinel + Run 001 driver commit.
#
# Files an audit doc capturing every drift surfaced during the
# Run 001 cleanup sweep. Commits the drift-check sentinel script
# so future sessions catch numeric drift automatically before
# tagging. Commits the FizzBuzz coding-loop driver as the
# canonical pattern (with 5-bug ledger encoded for future-me).
#
# Does NOT yet remediate the findings — that's Burst 83.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 82 — Full audit doc + drift sentinel + Run 001 driver ==="
echo
clean_locks
git add docs/audits/2026-05-03-full-audit.md
git add dev-tools/check-drift.sh
git add live-test-fizzbuzz.command
git add commit-burst82.command
clean_locks
git status --short
echo
clean_locks
git commit -m "audit: 2026-05-03 full audit + drift sentinel + Run 001 driver

Triggered by the audit-chain path mystery surfaced during Run 001
(FizzBuzz autonomous coding-loop smoke test). The script's
post-mortem read data/audit_chain.jsonl and reported '0 entries'
while the daemon was visibly serving requests elsewhere. That one
thread, pulled, exposed multiple silent drifts. Operator
instruction: no dark corners, no rugs.

Audit findings (full detail in docs/audits/2026-05-03-full-audit.md):

P0 (wrong claims, mislead outsiders or break tooling):
- README.md tests: 1,968 stated -> 2,072 actual. Stale by entire v0.3
  ADR-0036 arc.
- STATE.md commits: ~155 stated -> 234 actual. 79 commits stale.
- STATE.md LoC: ~36,400 stated -> 44,648 actual. 8,248 stale.
- STATE.md .command scripts: 36 stated -> 88 actual. 52 stale (commit-
  burst*.command files accumulated).
- README.md ADRs: 36 stated -> 37 actual (missing ADR-0040).
- README.md trait roles: 17 -> 18 (missing verifier_loop).
- README.md audit event types: 52 -> 55.
- CHANGELOG.md missing the ENTIRE v0.3 arc (Bursts 65-81 = ADR-0036 +
  ADR-0040 = 18 commits). Natural blocker to tagging v0.3.0.
- Audit chain default path is examples/audit_chain.jsonl per
  daemon/config.py — neither STATE nor README explain this. New
  contributors look in data/ and find a stale 5-entry file.

P1 (drift that erodes trust but doesn't break):
- STATE.md initiative annotations: claims '15 of 53'. tool_catalog.yaml
  has 2; 23 builtin source files mention initiative. Annotations
  partially landed in source, mostly missing from catalog. Conflated.
- ADR statuses inconsistent: no structured frontmatter. ADRs 0035 +
  0039 still have placeholder text ('proposed | ratified | rejected
  | superseded'). Several ADRs labeled 'Proposed' are shipped code.
- 13 test-fixture agents accumulated in registry — 9 active, 4
  archived. 5 are Forge_FB001_* fallout from Run 001 v1-v5 iterations
  (no archive-on-exit logic).

P2 (cosmetic):
- ADR number gaps 0009-0015 (7 consecutive missing) with no doc
  explaining why.
- Skill manifest count: 'shipped' (26 in examples/) vs 'installed'
  (23 in data/forge/skills/installed/) conflated in STATE.

Things VERIFIED clean (no finding):
- Audit chain integrity: 1083 entries, all hashes link, no breaks.
- Tool catalog <-> builtin sync: 53 = 53. Names match. No orphans.
- Skill manifest dependencies: all 23 installed skills' tool deps
  exist at claimed versions.

What lands in this burst (does NOT remediate findings):
- docs/audits/2026-05-03-full-audit.md — every finding, full detail,
  remediation plan
- dev-tools/check-drift.sh — drift sentinel that runs every numeric
  check from disk reality vs doc claims. Use before any release tag.
- live-test-fizzbuzz.command — the Run 001 driver, with 5-bug ledger
  encoded in the header (missing tool_version + session_id, relative
  PY_BIN path, heredoc-eats-backticks, heredoc-replaces-stdin,
  pytest_passed false-positive on mixed pass/fail). Future scenario
  runs reuse this pattern; the ledger prevents re-discovery.

Remediation plan (queued for next bursts):
- Burst 83: refresh STATE/README/CHANGELOG with corrected counts,
  archive the 9 zombie test agents, add audit chain path docs.
- Burst 84: ADR status standardization (frontmatter pass over 37 ADRs)
- Burst 85: initiative annotation reconciliation (catalog vs source)
- Burst 86: ADR-INDEX.md with gap explanation + status-at-a-glance
- Burst 87: tag v0.3.0
- Burst 88: file v0.4 app-platform planning doc

Bottom line: the codebase substance is solid. The drift is in the
documentation surface that outsiders read first. The drift sentinel
script ensures we catch this before the next tag rather than after."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 82 landed. Audit doc filed. Drift sentinel committed."
echo "Next: Burst 83 — act on findings (STATE/README/CHANGELOG refresh + zombie cleanup)."
echo ""
read -rp "Press Enter to close..."
