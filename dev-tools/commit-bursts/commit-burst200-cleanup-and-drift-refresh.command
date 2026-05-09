#!/bin/bash
# Burst 200 — working-tree cleanup + STATE/README drift refresh +
# drift-sentinel "latest tag" bug fix.
#
# Three coupled paperwork items:
#
# 1. Delete 8 untracked scratch/backup files that have accumulated
#    since 2026-05-06. §0 audited individually:
#
#      .env.bak-20260506-194735              point-in-time .env backup
#      .env.bak-20260506-195032              point-in-time .env backup
#      .env.bak-frontier-fix-20260506-200302 point-in-time .env backup
#      dev-tools/_diag_combined.txt          pytest output capture
#      dev-tools/_diag_existing.txt          pytest output capture
#      dev-tools/_diagnose_imports.command   one-off diagnostic script
#      dev-tools/_e7_smoke_base.md           smoke fixture, "DO NOT FIRE"
#      dev-tools/_e7_smoke_verbatim.json     smoke fixture, "DO NOT FIRE"
#
#    All 8 are untracked (never committed to git). Reference scan
#    confirms none are load-bearing: the diag/_diagnose trio is self-
#    referencing only; the _e7_smoke pair has no external references;
#    the .env.bak files are point-in-time snapshots, with the helper
#    scripts (setup-anthropic-frontier.command, fix-frontier-base-url
#    .command) generating fresh backups when needed. The current .env
#    is in active use (qwen2.5-coder:7b loaded across all 5 model
#    roles per /healthz output 2026-05-08).
#
# 2. STATE.md + README.md drift refresh. Both docs were timestamped
#    "post-Burst 124" but the repo had advanced 75 bursts to B199.
#    Drift sentinel deltas (most severe):
#       - Source LoC:       50,289 / 44,648 -> 56,113 actual
#       - Tests passing:    2,386 / 2,177 -> 2,598
#       - ADRs filed:       43 / 38 -> 53 (ADR-0001 through ADR-0056)
#       - Frontend modules: 22 -> 26
#       - .command scripts: 43 root + 100 archive -> 59 root + 172
#                                                    archive + 16 dev-tools
#       - Total commits:    281 -> 357
#       - Audit docs:       13 -> 15
#       - Audit event types: 70 -> 71 (+14 catalog drift fix per B199)
#    Headline tables in both files updated. "Last updated" line in
#    STATE.md rewritten to reflect the 75-burst arc (ADR-0047
#    assistant chat, ADR-0049/0050/0051 hardware, ADR-0052/0053
#    secrets+grants, ADR-0054 procedural shortcuts, ADR-0055
#    marketplace draft, ADR-0056 experimenter, B198 result_digest
#    fix, B199 chain fork fix).
#
# 3. dev-tools/check-drift.sh "latest tag" bug fix. Pre-B200 used
#    \`git tag --sort=-version:refname | head -1\` which placed
#    pre-release suffixes (-rc, -beta) AFTER the base version, so
#    v0.5.0-rc shadowed v0.5.0 in the report even though v0.5.0 was
#    tagged a day later. Switched to \`git for-each-ref
#    --sort=-creatordate\` so chronology answers "what's the most
#    recent tag" correctly.
#
# What we deliberately did NOT do:
#   - Re-install the 23 skills that were in data/forge/skills/installed/
#     before the post-2026-05-03 reset. Skills load from examples/ via
#     the catalog at runtime regardless; persistent installation is an
#     operator decision per scenario. Documented in STATE.md.
#   - Tag a v0.6.0-rc. v0.6 release is gated on integrator validation
#     per ADR-0044 P7; tagging now would jump the gun.
#   - Touch the 6 historical chain forks at seqs 3728/3735-3738/3740.
#     Append-only includes broken parts (per the B199 audit doc).
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — pure docs + cleanup + a one-line
#                  fix to a dev-tools sentinel.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

echo "--- removing 8 untracked scratch/backup files ---"
rm -v \
  .env.bak-20260506-194735 \
  .env.bak-20260506-195032 \
  .env.bak-frontier-fix-20260506-200302 \
  dev-tools/_diag_combined.txt \
  dev-tools/_diag_existing.txt \
  dev-tools/_diagnose_imports.command \
  dev-tools/_e7_smoke_base.md \
  dev-tools/_e7_smoke_verbatim.json

echo ""
echo "--- staging drift refresh + sentinel fix + this script ---"
git add STATE.md \
        README.md \
        dev-tools/check-drift.sh \
        dev-tools/commit-bursts/commit-burst200-cleanup-and-drift-refresh.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "chore(docs): cleanup + STATE/README drift refresh + sentinel fix (B200)

Burst 200. Three coupled paperwork items.

(1) Cleanup: removed 8 untracked scratch/backup files accumulated
since 2026-05-06.

  3 .env.bak files  point-in-time backups, helper scripts make new
                    ones when needed, current .env in active use
  3 _diag* files    diagnostic output captures + the one-off script
                    that generated them; self-referencing only
  2 _e7_smoke*      smoke fixtures, marked 'DO NOT FIRE', no refs

All 8 §0-audited as non-load-bearing. None tracked by git.

(2) STATE.md + README.md drift refresh. Both docs were timestamped
post-Burst 124 but the repo had advanced 75 bursts to B199. Headline
deltas:

  Source LoC      50,289 / 44,648  ->  56,113
  Tests passing   2,386 / 2,177    ->  2,598
  ADRs filed      43 / 38          ->  53 (ADR-0056 latest)
  Frontend modules 22              ->  26
  .command root   43               ->  59
  .command archive 100             ->  172
  Total commits   281              ->  357
  Audit docs      13               ->  15
  Audit event types 70             ->  71 (B199 catalog fix)

'Last updated' line rewritten to reflect the 75-burst arc:
ADR-0047 assistant chat (Sage agent), ADR-0049/0050/0051 hardware
binding, ADR-0052/0053 secrets storage + per-tool plugin grants,
ADR-0054 procedural shortcut dispatch (substrate DEFAULT OFF),
ADR-0055 marketplace draft, ADR-0056 experimenter (Smith agent),
B198 result_digest scheduler fix, B199 chain fork fix.

(3) dev-tools/check-drift.sh 'latest tag' bug. Pre-B200 used
\`git tag --sort=-version:refname\` which placed pre-release
suffixes after the base version (v0.5.0-rc shadowed v0.5.0 even
though v0.5.0 was tagged a day later). Switched to
\`git for-each-ref --sort=-creatordate\` — chronology answers
'what's the most recent tag' correctly.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — pure docs + cleanup + a
one-line dev-tools sentinel fix."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 200 complete ==="
echo "=== Working tree clean; STATE/README honest; sentinel fixed. ==="
echo "Press any key to close."
read -n 1
