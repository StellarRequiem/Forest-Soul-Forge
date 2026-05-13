#!/bin/bash
# Burst 259 — session-end documentation refresh.
#
# Three ADRs closed end-to-end this session (0061 / 0062 /
# 0063), plus a schema bump + a license pivot, plus a
# hotfix, plus 12 commits of new code. STATE.md was
# pinned at post-B233 — 25 bursts stale. Per CLAUDE.md
# "docs/audits/ is the canonical timeline of architectural
# changes" the session deserves an audit doc + a STATE
# refresh.
#
# Files:
#
# 1. docs/audits/2026-05-13-three-adr-arc.md (NEW)
#    Session-end audit covering commits 5d3662e → cd83e83.
#    TL;DR table of the three closed ADRs, per-ADR what's-
#    new sections, defense-surface tables, verification arc
#    (incl. the B256.1 trait-weight hotfix story + the
#    B256.2 test-fixture fix story), schema delta, audit
#    event delta, frontend delta, file inventory, queue of
#    what's NOT done, full commit timeline.
#
# 2. STATE.md
#    Header refreshed to "post-Burst 258" with the four
#    overlapping arcs (B234-B258) folded into the
#    last-updated paragraph. Schema-version cell updated
#    v17 → v20 with the full progression.
#
# No production code changes. Doc refresh only.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/audits/2026-05-13-three-adr-arc.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst259-doc-refresh.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: session-end refresh — 3 ADRs closed (B259)

Burst 259. Documentation pass after the B248-B258 arc closed
three ADRs end-to-end:
- ADR-0061 Agent Passport (B248)
- ADR-0062 Supply-Chain Scanner (B249, B250, B257, B258)
- ADR-0063 Reality Anchor (B251-B256)

New docs/audits/2026-05-13-three-adr-arc.md captures the
architectural changes: TL;DR table, per-ADR what's-new
sections, defense-surface tables, verification arc story
(including the B256.1 trait-weight hotfix the post-session
pytest run surfaced + the B256.2 test-fixture fix), schema
delta (v19→v20), audit event delta (8 new event types),
frontend delta (2 new SoulUX tabs), file inventory, queue
of what's NOT done, full commit timeline.

STATE.md header refreshed to post-Burst 258 — folds 4
overlapping arcs from B234-B258 into the lead paragraph.
Schema-version cell updated v17→v20 with the full
progression.

No production code changes. Per CLAUDE.md verification
discipline: docs are part of the verification surface —
stale state docs mislead the next session as much as a
broken test."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 259 complete ==="
echo "=== STATE.md + audit doc refreshed. ==="
echo "Press any key to close."
read -n 1
