#!/bin/bash
# One-shot helper to archive existing Reviewer-Main + re-birth it
# under the updated code_reviewer template (B416's allowed_paths).
#
# Why this exists: existing Reviewer-Main was born before B416's
# template patch. Constitution-immutability invariant means it
# can't pick up the new allowed_paths defaults. To enable Option C
# scheduled cadence, Reviewer-Main needs to be rebirthed.
#
# Same B376 pattern used for chaz/Kraine/Victor. Idempotent:
# if Reviewer-Main is already archived, the archive call returns
# a clean "already archived" status; the subsequent birth-triune-
# main.command call re-births only the missing one.

set -uo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
DAEMON="http://127.0.0.1:7423"

echo "=========================================================="
echo "Rebirth Reviewer-Main (B416 allowed_paths pickup)"
echo "=========================================================="

echo
echo "[1/3] Resolving current Reviewer-Main instance_id"
EXISTING=$(curl -s --max-time 5 "${DAEMON}/agents?limit=300" \
  -H "X-FSF-Token: $TOKEN" 2>/dev/null \
  | python3 -c "import sys,json; data=json.load(sys.stdin); ids=[a.get('instance_id') for a in data.get('agents',[]) if a.get('agent_name')=='Reviewer-Main' and a.get('status')=='active']; print(ids[0] if ids else '')" 2>/dev/null \
  || echo "")

if [ -z "$EXISTING" ]; then
  echo "      No active Reviewer-Main found. Will birth fresh in step 3."
else
  echo "      active: $EXISTING"
  echo
  echo "[2/3] Archiving existing Reviewer-Main"
  # B421 fix: archive endpoint is POST /archive (writes router, root-
  # mounted, no /agents prefix) with instance_id IN BODY per
  # ArchiveRequest schema (instance_id + reason required, archived_by
  # optional). Original B420 script POSTed to /agents/{id}/archive
  # which doesn't exist — daemon returned {"detail":"Not Found"} and
  # helper continued blindly to step [3/3], which found the agent
  # still alive and skipped birth. Verified via
  # docs/audits/2026-05-17-quarantine-rebirth.md (B376 lineage:
  # "POST /archive with instance_id=<old>, reason=<lineage...") plus
  # src/forest_soul_forge/daemon/routers/writes/archive.py:80
  # (@router.post("/archive")) and the parent mount at
  # src/forest_soul_forge/daemon/app.py:1226 (no prefix).
  ARCH_RESP=$(curl -s --max-time 15 "${DAEMON}/archive" \
    -X POST -H "X-FSF-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -H "X-Idempotency-Key: archive-reviewer-main-b416" \
    -d "{\"instance_id\": \"${EXISTING}\", \"reason\": \"B416 rebirth to pick up code_reviewer allowed_paths defaults\", \"archived_by\": \"alex\"}" \
    2>&1)
  echo "      Archive response (truncated):"
  echo "$ARCH_RESP" | python3 -m json.tool 2>/dev/null | head -10 || echo "$ARCH_RESP" | head -5
  STATUS=$(echo "$ARCH_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('detail', '?')))" 2>/dev/null || echo "?")
  echo "      Status: $STATUS"
  if [ "$STATUS" = "Not Found" ] || [ "$STATUS" = "?" ]; then
    echo "      ERROR: archive call failed. Refusing to proceed with"
    echo "      step [3/3] — birth-triune-main would silently skip on"
    echo "      finding agent still active. Inspect response above."
    exit 1
  fi
fi

echo
echo "[3/3] Re-running birth-triune-main.command (idempotent)"
bash "$(pwd)/dev-tools/birth-triune-main.command" </dev/null

echo
echo "=========================================================="
echo "Rebirth complete"
echo "=========================================================="
echo
echo "Verify allowed_paths landed:"
echo "  python3 -c \"import yaml, glob; "
echo "  [print(t) for p in glob.glob('soul_generated/Reviewer-Main*.constitution.yaml') "
echo "   for d in [yaml.safe_load(open(p))] for t in d.get('tools',[]) "
echo "   if t.get('name')=='code_read']\""
echo
echo "Then run-reviewer-review.command should reach status=succeeded."
echo
echo "Press any key to close."
read -n 1 || true
