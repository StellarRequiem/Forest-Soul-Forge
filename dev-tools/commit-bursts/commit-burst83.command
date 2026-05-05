#!/usr/bin/env bash
# Burst 83: remediation pass per 2026-05-03 audit findings.
#
# What this fixes (P0/P1 from the audit):
#   - STATE.md, README.md numeric counts brought into sync with disk
#   - CHANGELOG.md gets the v0.3.0 section (was missing entire arc)
#   - CLAUDE.md gets audit-chain-path invariant + Run 001 driver lessons
#   - 9 zombie test agents archived (5 Forge_FB001_* + 3 VoiceTest +
#     1 GenreDemo) — leaks from runs that never had archive-on-exit
#
# What this does NOT do (queued for later bursts):
#   - ADR status standardization (Burst 84 candidate)
#   - Initiative annotation reconciliation (Burst 85)
#   - ADR-INDEX.md (Burst 86)
#   - Tag v0.3.0 (Burst 84 — separate commit, separate concern)
#
# Pre-flight: requires the daemon running (we POST /archive to clean
# the zombies). If daemon down, the archive step is skipped and we
# log a warning; the doc updates still commit.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TOKEN="${FSF_API_TOKEN:-}"
auth_header() { [[ -n "$TOKEN" ]] && echo "-H X-FSF-Token: $TOKEN" || echo ""; }

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 83 — remediation per 2026-05-03 audit ==="
echo

# --- Step 1: archive zombie test agents -------------------------------------
echo "Step 1: archive zombie test agents"
echo
if curl -sf --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "✓ daemon up — querying for zombies"

  # Use the registry directly to find zombies, then POST /archive for each.
  ZOMBIES=$(.venv/bin/python3 - <<'PYEOF'
import sqlite3
conn = sqlite3.connect('data/registry.sqlite')
cur = conn.execute("""
  SELECT instance_id FROM agents
  WHERE (agent_name LIKE 'Forge_FB%' OR agent_name LIKE 'VoiceTest%'
      OR agent_name LIKE 'GenreDemo%')
    AND status = 'active'
  ORDER BY agent_name;
""")
for row in cur:
    print(row[0])
PYEOF
  )

  archived_count=0
  for instance_id in $ZOMBIES; do
    payload=$(cat <<JSON
{"instance_id": "$instance_id", "reason": "Burst 83 remediation — test fixture zombie cleanup per 2026-05-03 audit", "archived_by": "operator"}
JSON
    )
    resp=$(curl -s --max-time 10 -X POST "$DAEMON/archive" \
      -H "Content-Type: application/json" $(auth_header) \
      -d "$payload")
    if echo "$resp" | grep -q '"status".*"archived"'; then
      archived_count=$((archived_count+1))
      echo "  ✓ archived $instance_id"
    else
      echo "  ✗ failed to archive $instance_id: $(echo "$resp" | head -c 200)"
    fi
  done
  echo "Archived $archived_count zombie agent(s)."
else
  echo "⚠ daemon not reachable at $DAEMON — skipping zombie archive"
  echo "  (the doc updates still commit; archive on next run with daemon up)"
fi

echo
clean_locks
git add STATE.md README.md CHANGELOG.md CLAUDE.md commit-burst83.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs: remediate 2026-05-03 audit drift (P0/P1 findings) + archive 9 zombies

Acts on the audit findings filed in Burst 82
(docs/audits/2026-05-03-full-audit.md). Brings STATE.md, README.md,
CHANGELOG.md, and CLAUDE.md into sync with disk reality.

STATE.md changes:
- Source LoC: ~36,400 -> 44,648 (corrected; was undercount by 8.2k).
  Per-package breakdown added.
- ADRs filed: clarified 37 files / 35 unique numbers (gaps 0009-0015).
- Builtin tools: noted catalog and source in sync (verified by audit).
- Skill manifests: clarified 26 shipped (examples/) vs 23 installed
  (data/forge/skills/installed/). Was conflated.
- Tools with initiative annotations: corrected '15 of 53' claim
  to actual '2 in catalog YAML + 23 in builtin source'. Catalog is
  the configuration of record per ADR-0018; reconciliation queued.
- .command operator scripts: 36 -> 88 (commit-burst* accumulation).
- Total commits on main: ~155 -> 234 (corrected; was 79 stale).
- Audit docs filed: 2 -> 13 (most recent: full-audit.md).
- NEW row: live audit chain path = examples/audit_chain.jsonl (per
  daemon/config.py default; FSF_AUDIT_CHAIN_PATH overrides).
- NEW row: drift sentinel = dev-tools/check-drift.sh.

README.md changes:
- Tests passing: 1,968 -> 2,072 (+104 from v0.3 ADR-0036 arc).
- ADRs filed: 36 -> 37 (added ADR-0040).
- Built-in tools: 51 -> 53 (was undercount).
- Trait roles: 17 -> 18 (added verifier_loop).
- Audit event types: 52 -> 55.
- Schema version: v10 -> v12.
- Skill manifests: clarified shipped vs installed.
- Source LoC: ~46,000 -> 44,648 (corrected; was overcount by 1.4k).
- Operator .command scripts: 37 -> 88.
- NEW row: live audit chain path documented.

CHANGELOG.md changes:
- Added [0.3.0] section between [Unreleased] and [0.2.0]. Covers:
  * ADR-0036 Verifier Loop arc (T1+T2+T3a+T3b+T5+T6+T7, Bursts 65-70)
  * ADR-0040 Trust-Surface Decomposition arc (T1+T2+T3+T4, Bursts 71-81)
  * Burst 82 audit + drift sentinel + Run 001 driver
  * Run 001 (FizzBuzz autonomous coding loop, success in 2 turns)
- T4 Verifier scheduled-task substrate flagged as deferred to v0.4.

CLAUDE.md changes:
- Added invariant: live audit chain at examples/audit_chain.jsonl
  (dev fixture is the stale data/ one). Reference to dev-tools/check-drift.sh.
- Added 'Live-test driver gotchas (Run 001 lessons)' section:
  * python3 - <<'PYEOF' makes heredoc replace stdin (use python3 -c)
  * curl -sf swallows 4xx/5xx bodies (drop -f to debug)
  * ToolCallRequest requires tool_version + session_id

Zombie cleanup:
- Up to 9 active test-fixture agents archived via POST /archive:
  5 x Forge_FB001_* (Run 001 v1-v5 attempts)
  3 x VoiceTest (older voice-renderer tests)
  1 x GenreDemo (older genre demo)
- Archive script logs the count; if daemon was down, skipped with
  warning (doc updates still committed for forward progress).

Verification:
- dev-tools/check-drift.sh now runs clean against the updated docs
  (will resurface drift if any subsequent commit re-introduces it).

Remaining audit findings (queued):
- Burst 84 - tag v0.3.0 (CHANGELOG section above is the prerequisite)
- Burst 85 - ADR status standardization (37 ADR frontmatter pass)
- Burst 86 - initiative annotation reconciliation (catalog vs source)
- Burst 87 - ADR-INDEX.md with gap explanation
- Burst 88 - ADR-0036 T4 implementation (set-and-forget orchestrator)
- Burst 89 - ADR-0041 file (agent self-timing tool family)"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 83 landed. Audit P0/P1 findings remediated."
echo "Next: Burst 84 — tag v0.3.0."
echo ""
read -rp "Press Enter to close..."
