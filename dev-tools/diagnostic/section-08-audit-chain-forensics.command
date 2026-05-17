#!/usr/bin/env bash
# ADR-0079 section 08 — audit chain forensics.
#
# Three checks against the live chain:
#   1. audit_chain_verify end-to-end (catches the known seq gap
#      at 3728->3729 if still present)
#   2. signature coverage spot-check (sample N entries, confirm
#      sig present + non-empty)
#   3. body_hash present on summarizable event types (post-Y7
#      lazy summarization preserves body_hash for tamper-evidence)
#
# Reads the chain file directly from disk. The canonical path
# per CLAUDE.md is examples/audit_chain.jsonl (NOT data/);
# overridable via FSF_AUDIT_CHAIN_PATH.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-08-audit-chain-forensics"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

CHAIN_PATH="${FSF_AUDIT_CHAIN_PATH:-$REPO_ROOT/examples/audit_chain.jsonl}"
GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 08 — audit chain forensics

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- chain path: $CHAIN_PATH

HEADER

if [ ! -f "$CHAIN_PATH" ]; then
  cat >> "$REPORT" <<EOF
## Result

- aborted: chain file not found at $CHAIN_PATH

Set FSF_AUDIT_CHAIN_PATH to override if your daemon writes elsewhere.
EOF
  echo "section 08: chain file missing"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$CHAIN_PATH" <<'PYEOF'
"""Section 08 — audit chain forensics."""
import json
import sys
from pathlib import Path

REPORT, CHAIN_PATH = sys.argv[1:3]
REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

results: list[tuple[str, str, str]] = []

# ---- 1. chain verify ------------------------------------------------------
try:
    from forest_soul_forge.core.audit_chain import AuditChain
    chain = AuditChain(Path(CHAIN_PATH))
    res = chain.verify()
    ok = bool(getattr(res, "ok", False))
    if ok:
        n = getattr(res, "entries_verified", "?")
        results.append(("PASS", "audit_chain_verify end-to-end",
                        f"chain ok ({n} entries)"))
    else:
        broken = getattr(res, "broken_at_seq", "?")
        reason = getattr(res, "reason", "?")
        results.append(("FAIL", "audit_chain_verify end-to-end",
                        f"broken_at_seq={broken}, reason={reason}"))
except Exception as e:
    results.append(("FAIL", "audit_chain_verify end-to-end",
                    f"{type(e).__name__}: {e}"))

# ---- 2. signature coverage spot-check ------------------------------------
# Sample the last 200 entries (or all if smaller). Each entry should have
# a `signature` or `sig` field with non-empty value.
try:
    lines = Path(CHAIN_PATH).read_text(encoding="utf-8").splitlines()
    n_total = len(lines)
    sample = lines[-200:] if n_total > 200 else lines
    missing_sig = 0
    sampled = 0
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        sampled += 1
        sig = e.get("signature") or e.get("sig")
        if not sig:
            missing_sig += 1
    if sampled == 0:
        results.append(("FAIL", "signature coverage spot-check",
                        "no entries parsed in sample"))
    elif missing_sig == 0:
        results.append(("PASS", "signature coverage spot-check",
                        f"{sampled}/{sampled} sampled entries signed"))
    else:
        results.append(("FAIL", "signature coverage spot-check",
                        f"{missing_sig}/{sampled} sampled entries missing signature"))
except Exception as e:
    results.append(("FAIL", "signature coverage spot-check",
                    f"{type(e).__name__}: {e}"))

# ---- 3. body_hash present on summarizable event types --------------------
# Y7 lazy summarization replaces turn bodies but preserves body_hash for
# tamper-evidence. Sample turn_* event types; confirm body_hash present.
try:
    sample = lines[-500:] if n_total > 500 else lines
    turn_events = 0
    missing_bh = 0
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        etype = e.get("event_type") or ""
        if etype.startswith("turn_") or etype in (
            "conversation_turn", "assistant_turn", "user_turn",
        ):
            turn_events += 1
            if not (e.get("body_hash") or e.get("payload", {}).get("body_hash")):
                missing_bh += 1
    if turn_events == 0:
        results.append(("PASS", "body_hash on turn events",
                        "no turn events in sample window (skipped)"))
    elif missing_bh == 0:
        results.append(("PASS", "body_hash on turn events",
                        f"{turn_events}/{turn_events} turn events have body_hash"))
    else:
        results.append(("FAIL", "body_hash on turn events",
                        f"{missing_bh}/{turn_events} turn events missing body_hash"))
except Exception as e:
    results.append(("FAIL", "body_hash on turn events",
                    f"{type(e).__name__}: {e}"))

# ---- emit -----------------------------------------------------------------
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n"
            f"- chain entry count (file lines): {n_total}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 08: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 08 exit: $RC"
exit "$RC"
