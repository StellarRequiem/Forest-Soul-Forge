#!/usr/bin/env bash
# Burst 80: ADR-0040 T3.4 — extract writes/archive.py. CLOSES T3.
#
# Final extraction in T3. Moves /archive (the lifecycle-terminal
# surface) out of writes/__init__.py into writes/archive.py. After
# this lands, writes/__init__.py is a pure package facade — no
# @router decorators of its own, just APIRouter declaration +
# include_router calls. Test suite stays green at 2072 passing.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 80 — ADR-0040 T3.4 writes/archive.py extraction (CLOSES T3) ==="
echo
clean_locks
git add -A src/forest_soul_forge/daemon/routers/writes/
git add commit-burst80.command
clean_locks
git status --short
echo
clean_locks
git commit -m "refactor: ADR-0040 T3.4 — extract writes/archive.py (closes T3)

FINAL extraction in T3. Moves the /archive handler out of
writes/__init__.py into its own writes/archive.py sub-router.
After this lands, writes/__init__.py is a pure package facade —
no @router decorators of its own, just the parent APIRouter
declaration (with governance deps) and three include_router calls.

Trust surface owned by this file (per ADR-0040 §1):
lifecycle-terminal governance — marking an existing agent as
archived (status='archived'), emitting the agent_archived audit
event, and idempotency-caching the response. Distinct from
creation (writes/birth.py) and from voice regeneration
(writes/voice.py): no soul.md mutation, no constitution work, no
genre/kit/trait checks. The endpoint deliberately preserves the
agent's identity and artifact state — only the registry status
column flips.

Why /archive does NOT generate a new audit-chain entry through
register_birth: archive doesn't insert a row, so the audit-event
mirror has to be called explicitly via register_audit_event. That
quirk is preserved verbatim from the pre-extraction module.

ADR-0040 T3 status — CLOSED:
- T3.1 (Burst 77): writes/_shared.py (idempotency helpers)
- T3.2 (Burst 78): writes/birth.py (creation surface)
- T3.3 (Burst 79): writes/voice.py (regen surface)
- T3.4 (Burst 80): writes/archive.py (lifecycle terminal) — this
- All four endpoints now live in their own grant-able files.

Final layout under src/forest_soul_forge/daemon/routers/writes/:
  __init__.py     89 lines — pure package facade
  _shared.py     148 lines — idempotency + voice render helpers
  birth.py       863 lines — /birth + /spawn + creation
  voice.py       233 lines — /regenerate-voice
  archive.py     141 lines — /archive
  ----------------------
  Total         1474 lines

Verification:
- import probe: parent router exposes all 4 routes; each sub-router
  exposes its own routes independently:
    birth:    /birth + /spawn
    voice:    /agents/{instance_id}/regenerate-voice
    archive:  /archive
- Full unit test suite: 2072 passed, 3 skipped, 1 xfailed.
- writes/__init__.py decreased from 171 -> 89 lines (-82) — most
  of the loss was unused-import cleanup (the imports that
  /archive needed but no other surface did: AuditChain, Registry,
  UnknownAgentError, threading, datetime, get_audit_chain,
  get_registry, get_write_lock, compute_request_hash,
  get_idempotency_key, AgentOut, ArchiveRequest,
  _chain_entry_to_parsed, _to_agent_out, _shared helpers).
  All those moved to archive.py with the handler.

ADR-0040 status after this burst:
- T1 (file ADR-0040): shipped Burst 71
- T2 (memory.py decomposition): closed Burst 76
- T3 (writes.py decomposition): CLOSED this burst
- T4 (cross-references in STATE.md / CLAUDE.md): pending — Burst 81

What's now possible that wasn't before T3:
A constitution can grant a Voice-iteration agent
\`allowed_paths: [\"src/forest_soul_forge/daemon/routers/writes/voice.py\"]\`
and that agent CANNOT modify creation logic, kit-tier enforcement,
or archival lifecycle — exactly the file-grained governance
ADR-0040 §1 was designed to deliver. The trust-surface count rule
(ADR-0040 §1) survived contact with the real codebase across two
distinct decomposition campaigns (memory + writes), and the
mixin/sub-router pattern proved consistent across class-based and
router-based decomposition shapes."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 80 landed. ADR-0040 T3 CLOSED. writes/archive.py shipped."
echo "writes.py decomposition complete. 4 of 4 endpoint sub-routers extracted."
echo "Next: Burst 81 — T4 STATE.md / CLAUDE.md cross-references (closes ADR-0040)."
echo ""
read -rp "Press Enter to close..."
