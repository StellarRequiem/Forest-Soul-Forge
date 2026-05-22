#!/usr/bin/env bash
# ADR-0079 section 08 — audit chain forensics.
#
# Six checks against the live chain:
#   1. audit_chain_verify end-to-end (catches the known seq gap
#      at 3728->3729 if still present)
#   2. signature coverage spot-check (sample N entries, confirm
#      sig present + non-empty)
#   3. body_hash present on summarizable event types (post-Y7
#      lazy summarization preserves body_hash for tamper-evidence)
#   4. detection_fired events well-formed (ADR-0065 D6 — rule_id +
#      rule_version + batch_id + technique + severity +
#      matched_event_ids on every detection-as-code chain entry)
#   5. playbook_executed events well-formed (ADR-0066 D5 —
#      playbook_id + playbook_version + trigger_detection_id +
#      steps + outcome on every SOAR playbook chain entry)
#   6. purple_team_run_completed events carry simulation provenance
#      (ADR-0066 D3 — simulation flag + scenario_id + technique)
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
# B364 — pre-B199 fork artifacts: audit_chain.py documents that
# seqs 3728 / 3735-3738 / 3740 are canonical historical write-race
# duplicates from before ADR-0050 B199 introduced the per-chain
# mutex. The chain at those points has duplicate seqs (two entries
# both claiming the same seq, both with the same prev_hash, one
# orphaned because nothing references its hash). They are NOT
# present-day corruption - they are tracked, named, and immutable
# per the append-only invariant. The harness should distinguish
# these from genuinely-new chain breaks.
# B417: extended to cover the 2026-05-11 race episode (seqs
# 7695-7703). Same root cause as the May 8 set (pre-B199 mutex
# fix); separate write-race incident. Audit doc:
# docs/audits/2026-05-19-audit-chain-may11-race.md.
KNOWN_HISTORICAL_FORKS = {
    # May 8 race (B364 / 2026-05-17 audit doc)
    3728, 3735, 3736, 3737, 3738, 3740,
    # May 11 race (B417 / 2026-05-19 audit doc)
    7695, 7696, 7697, 7698, 7699, 7700, 7701, 7702, 7703,
}

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
        broken = getattr(res, "broken_at_seq", None)
        reason = getattr(res, "reason", "?")
        if isinstance(broken, int) and broken in KNOWN_HISTORICAL_FORKS:
            # Pre-B199 historical fork - documented in
            # core/audit_chain.py ForkScanResult docstring. The
            # break IS real but it predates the writer mutex fix
            # and the chain is append-only, so it stays forever.
            results.append(("INFO", "audit_chain_verify end-to-end",
                            f"broken_at_seq={broken} is a documented pre-B199 fork "
                            f"(ADR-0050); reason={reason}"))
        else:
            results.append(("FAIL", "audit_chain_verify end-to-end",
                            f"broken_at_seq={broken}, reason={reason}"))
except Exception as e:
    results.append(("FAIL", "audit_chain_verify end-to-end",
                    f"{type(e).__name__}: {e}"))

# ---- 2. signature coverage spot-check ------------------------------------
# B370 — ADR-0049 T5 (B244) made signatures OPTIONAL: they appear
# only on events emitted by an agent that has a public key registered.
# Most chain entries are system-emitted (chain_created, scheduler_lag,
# scheduled_task_completed, etc.) with agent_dna=null and no signature
# expected. Most agent-emitted entries also lack signatures today
# because few agents have a registered public key. The pre-B370 probe
# treated absence of signature as FAIL universally, which produced
# 200/200 missing in the spot check.
#
# Reshape: split the sample into three buckets and report counts.
#   - system_emitted: agent_dna is null. Signatures NOT expected.
#   - agent_emitted_with_key: agent_dna set AND that DNA has a public
#     key registered. Signatures ARE expected; missing IS a FAIL.
#   - agent_emitted_no_key: agent_dna set but no key registered.
#     Signatures are not expected (the agent CAN'T sign without a key).
#
# The check passes when:
#   - no agent_emitted_with_key entries are missing signatures, AND
#   - the spot check parsed at least one entry.
# It reports the per-bucket counts as the evidence string so the
# operator gets a coverage metric, not just pass/fail.
try:
    lines = Path(CHAIN_PATH).read_text(encoding="utf-8").splitlines()
    n_total = len(lines)
    sample = lines[-200:] if n_total > 200 else lines

    # Discover which agents have public keys registered. Best-effort:
    # look at agent_created events in the chain whose event_data
    # carries a public_key field. (The agents/{id}/passport endpoint
    # would be authoritative but section-08 stays read-only on the
    # filesystem and doesn't hit the daemon for this check.)
    agents_with_keys: set[str] = set()
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event_type") in ("agent_created", "agent_key_registered"):
            dna = e.get("agent_dna")
            ed = e.get("event_data") or {}
            if dna and (ed.get("public_key") or ed.get("agent_public_key")):
                agents_with_keys.add(dna)

    system_emitted = 0
    agent_with_key_signed = 0
    agent_with_key_unsigned = 0
    agent_no_key = 0
    sampled = 0
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        sampled += 1
        dna = e.get("agent_dna")
        sig = e.get("signature") or e.get("sig")
        if not dna:
            system_emitted += 1
            continue
        if dna in agents_with_keys:
            if sig:
                agent_with_key_signed += 1
            else:
                agent_with_key_unsigned += 1
        else:
            agent_no_key += 1

    evidence = (
        f"sampled={sampled}; system_emitted={system_emitted}; "
        f"agent_emitted: signed={agent_with_key_signed}, "
        f"unsigned_with_key={agent_with_key_unsigned}, "
        f"no_key={agent_no_key}; "
        f"agents_with_keys={len(agents_with_keys)}"
    )
    if sampled == 0:
        results.append(("FAIL", "signature coverage spot-check",
                        "no entries parsed in sample"))
    elif agent_with_key_unsigned > 0:
        # A keyed agent emitted an unsigned entry — that IS a bug.
        results.append(("FAIL", "signature coverage spot-check",
                        f"{agent_with_key_unsigned} entries from keyed agents missing signature; {evidence}"))
    else:
        # Either nothing keyed in sample (no expectation), or all
        # keyed-agent entries were signed. INFO when the sample
        # had no signature-eligible entries; PASS when at least
        # one keyed-agent entry was correctly signed.
        if agent_with_key_signed > 0:
            results.append(("PASS", "signature coverage spot-check", evidence))
        else:
            results.append(("INFO", "signature coverage spot-check",
                            f"no keyed-agent entries in sample; {evidence}"))
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

# ---- 4. detection_fired events well-formed (ADR-0065 T4) -----------------
# ADR-0065 D6: detection_fired events are first-class chain entries.
# Each (rule, batch) match the DetectionEngine scores emits one
# entry carrying rule_id + rule_version + batch_id + technique +
# severity + matched_event_ids. Section-08 confirms that shape on
# disk so a malformed emitter is caught here, not downstream when
# anomaly_ace's LLM follow-up trips over a missing field. No
# detection_fired events yet (engine idle / no matches) is a valid
# state — PASS-skipped, same posture as the body_hash check above.
try:
    sample = lines[-2000:] if n_total > 2000 else lines
    REQUIRED = ("rule_id", "rule_version", "batch_id", "technique",
                "severity", "matched_event_ids")
    fired = 0
    malformed: list[str] = []
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event_type") != "detection_fired":
            continue
        fired += 1
        ed = e.get("event_data") or e.get("payload") or {}
        missing = [k for k in REQUIRED if k not in ed]
        if missing:
            malformed.append(f"seq={e.get('seq')}: missing {missing}")
    if fired == 0:
        results.append(("PASS", "detection_fired events well-formed",
                        "no detection_fired events in sample window "
                        "(engine idle or no matches yet — skipped)"))
    elif not malformed:
        results.append(("PASS", "detection_fired events well-formed",
                        f"{fired}/{fired} detection_fired entries carry the "
                        f"ADR-0065 D6 event_data shape"))
    else:
        results.append(("FAIL", "detection_fired events well-formed",
                        f"{len(malformed)}/{fired} malformed: "
                        + "; ".join(malformed[:5])))
except Exception as e:
    results.append(("FAIL", "detection_fired events well-formed",
                    f"{type(e).__name__}: {e}"))

# ---- 5. playbook_executed events well-formed (ADR-0066 T6) ---------------
# ADR-0066 D5: playbook_executed events are first-class chain
# entries. The PlaybookEngine emits one per fired playbook carrying
# playbook_id + playbook_version + trigger_detection_id + steps +
# outcome. Section-08 confirms that shape on disk so a malformed
# emitter is caught here. No playbook_executed events yet (engine
# idle / no detections fired a playbook) is a valid state —
# PASS-skipped, same posture as the detection_fired check above.
try:
    sample = lines[-2000:] if n_total > 2000 else lines
    REQUIRED = ("playbook_id", "playbook_version", "trigger_detection_id",
                "steps", "outcome")
    ran = 0
    malformed: list[str] = []
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event_type") != "playbook_executed":
            continue
        ran += 1
        ed = e.get("event_data") or e.get("payload") or {}
        missing = [k for k in REQUIRED if k not in ed]
        if missing:
            malformed.append(f"seq={e.get('seq')}: missing {missing}")
    if ran == 0:
        results.append(("PASS", "playbook_executed events well-formed",
                        "no playbook_executed events in sample window "
                        "(no playbook fired yet — skipped)"))
    elif not malformed:
        results.append(("PASS", "playbook_executed events well-formed",
                        f"{ran}/{ran} playbook_executed entries carry the "
                        f"ADR-0066 D5 event_data shape"))
    else:
        results.append(("FAIL", "playbook_executed events well-formed",
                        f"{len(malformed)}/{ran} malformed: "
                        + "; ".join(malformed[:5])))
except Exception as e:
    results.append(("FAIL", "playbook_executed events well-formed",
                    f"{type(e).__name__}: {e}"))

# ---- 6. purple_team_run_completed carries simulation provenance ----------
# ADR-0066 D3: every purple_team_run_completed event carries
# simulation=true + scenario_id + technique so a reviewer can always
# tell synthetic activity from real SOC activity. A purple-team
# event WITHOUT the simulation flag is a provenance break — the most
# important property the substrate has to preserve.
try:
    sample = lines[-2000:] if n_total > 2000 else lines
    runs = 0
    malformed: list[str] = []
    for line in sample:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event_type") != "purple_team_run_completed":
            continue
        runs += 1
        ed = e.get("event_data") or e.get("payload") or {}
        missing = [k for k in ("scenario_id", "technique") if k not in ed]
        if ed.get("simulation") is not True:
            missing.append("simulation!=true")
        if missing:
            malformed.append(f"seq={e.get('seq')}: {missing}")
    if runs == 0:
        results.append(("PASS", "purple_team_run_completed provenance",
                        "no purple_team_run_completed events in sample "
                        "window (no exercise run yet — skipped)"))
    elif not malformed:
        results.append(("PASS", "purple_team_run_completed provenance",
                        f"{runs}/{runs} purple-team entries carry "
                        f"simulation=true + scenario provenance"))
    else:
        results.append(("FAIL", "purple_team_run_completed provenance",
                        f"{len(malformed)}/{runs} broken: "
                        + "; ".join(malformed[:5])))
except Exception as e:
    results.append(("FAIL", "purple_team_run_completed provenance",
                    f"{type(e).__name__}: {e}"))

# ---- emit -----------------------------------------------------------------
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n"
            f"- chain entry count (file lines): {n_total}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 08: {passed}/{len(results)} passed, {info} info")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 08 exit: $RC"
exit "$RC"
