# ADR-0065 — Detection-as-code + detection_engineer

**Status:** Accepted (2026-05-22, B389-B392). All 6 tranches shipped; D3 Phase C closed; detection_engineer role live (DetectionEngineer-D3). See `docs/runbooks/detection-as-code.md`.
**Date:** 2026-05-18
**Tracks:** D3 Local SOC Phase C
**Supersedes:** none
**Builds on:** ADR-0033 (Security Swarm — the 9-agent blue team
that ad-hoc-detects on operator dispatch), ADR-0064 (telemetry
pipeline — the continuous-ingest substrate Phase C detects
against), ADR-0078 Phase A (forensic_archivist), ADR-0064 T4/T6
Phase B (telemetry_steward + threat_intel_curator)
**Unblocks:** ADR-0066 (SOAR playbooks — Phase D consumes
detections + dispatches response)

## Context

Phase A landed chain-of-custody discipline (`forensic_archivist`,
B342-B347). Phase B landed continuous telemetry + intel curation
(`telemetry_steward`, `threat_intel_curator`, B377-B385). What's
still missing for a real local SOC is **detection-as-code**:
operator-authored rules that examine the telemetry stream and
emit detection events without per-event LLM judgment in the hot
path.

Today's Security Swarm (`anomaly_ace`, `log_lurker`, etc.) uses
LLM-grade per-event reasoning — high signal but high cost, and
operator-dispatched rather than continuous. A real SOC needs both
the LLM-grade roles (already alive) AND a **rule-based detection
engine** that runs continuously over the ingested telemetry,
fires deterministic detections on known patterns, and lets the
operator codify intent in version-controlled YAML.

The detection engine sits between the telemetry store
(`data/telemetry.sqlite`) and downstream consumers
(`anomaly_ace` for LLM follow-up; `response_rogue` for action;
future `playbook_pilot` per ADR-0066 for automated response).

## Decision

Land detection-as-code as **D3 Phase C**:

1. **Sigma-rule subset** as the rule format. Sigma is the
   industry-standard SIEM rule lingua franca; using a defined
   subset (no full Sigma engine — that's a separate ADR if ever
   needed) keeps operators portable across SIEMs.
2. **MITRE ATT&CK tagging** on every rule. Each rule declares the
   ATT&CK technique(s) it detects (e.g., `T1059.004` for shell
   execution). Tagging is operator-visible; it lets the steward
   summarize "what techniques we detect coverage for" against the
   matrix.
3. **`detection_engineer` role** as the operator-facing author of
   rules. The role doesn't write detection events at runtime — the
   engine does that. The role's job is to AUTHOR + REVIEW + TUNE
   rules + summarize coverage gaps. Genre: researcher (allows
   reads + LLM judgment for rule synthesis; no action surface).
4. **Detection engine** as a per-batch scanner. After each
   `telemetry_batch_ingested` event, the engine runs every
   active rule against that batch's events; matches emit
   `detection_fired` audit chain events with `{rule_id,
   batch_id, matched_event_ids, technique, severity}`.

### Architecture

```
TelemetryStore                    AuditChain
  │                                  ▲
  │ ingest_batch returns batch_id    │ telemetry_batch_ingested
  │                                  │   (B377 anchor)
  ▼                                  │
DetectionEngine.scan(batch_id) ─────┘
  │
  │ for rule in active_rules:
  │   matches = rule.evaluate(batch_events)
  │   if matches: emit "detection_fired"
  │
  ▼
AuditChain
  detection_fired {
    rule_id, batch_id, matched_event_ids,
    technique, severity, evidence
  }
  │
  ▼
downstream consumers:
  - anomaly_ace: LLM judgment on the matched events
  - response_rogue: operator-gated response (Phase D playbook)
  - threat_intel_curator: cross-reference matched IOCs with the
    intel cache
  - operator: dashboard view of fired detections
```

## Decisions

**Decision 1 — Sigma subset, not full Sigma.**

Full Sigma supports `selection` + `filter` + `condition` + `near`
+ `aggregation` + back-end-specific lowering. The subset shipped
in T1 supports:
- `logsource` (mapped to `TelemetryEvent.source` + `event_type`)
- `detection.selection` (field/value match dictionaries; AND
  semantics within a selection)
- `detection.condition` (boolean over selections; `selection or
  filter`, `selection and not filter`)
- `level` (severity passthrough)
- `tags` (ATT&CK technique IDs)

What's NOT in the subset: aggregation (`count() > N`),
time-windowed correlation, network/transformation back-ends.
Those land in T-future if a concrete need arises.

**Decision 2 — Rules live in `config/detection_rules/*.yml`.**

Operator-editable. Loaded at daemon lifespan. Reload via
`POST /detections/reload` (mirrors `/skills/reload` shape). Each
rule file is one rule; filename is the operator-readable id.

**Decision 3 — MITRE ATT&CK tagging is mandatory.**

A rule without `tags: [attack.T1234, ...]` fails load. Operators
who don't know the technique use `attack.unknown` explicitly —
that gives the steward a coverage-gap signal rather than letting
untagged rules slip through silently.

**Decision 4 — Engine runs in-process after each batch ingest.**

`AdapterIngestor.flush_pending` (B377) is the hook. After the
`telemetry_batch_ingested` chain event lands, the ingestor calls
`detection_engine.scan(batch_id, batch_events)`. The engine is
synchronous (matches finish before the next flush) but cheap —
the rule set is small enough that per-batch overhead stays in the
millisecond range. If the rule set grows past that budget, T-future
adds an async queue.

**Decision 5 — `detection_engineer` role does NOT run rules at
runtime.**

The role is the operator-facing AUTHOR. Rules live in
`config/detection_rules/` and the engine runs them; the engineer
reads matches, proposes new rules, reviews false-positive rates,
tunes thresholds. Genre: researcher (network reach for ATT&CK
ref pulls + LLM judgment for rule synthesis; no filesystem write
beyond memory).

The role's signature skill is `propose_detection.v1`:
1. recall — prior detection_fired events for the operator's
   focus area
2. recall — prior detection rules (catalog browse)
3. verify_chain_integrity (audit_chain_verify)
4. llm_think — synthesize a candidate rule from the operator's
   description + matched events
5. memory_write — record the proposed rule for operator review
   (NOT direct write to `config/detection_rules/` — operator
   commits the rule themselves; engineer proposes, operator
   accepts)

**Decision 6 — `detection_fired` events are first-class audit
chain entries.**

Same shape as other chain entries:
```
{
  event_type: "detection_fired",
  event_data: {
    rule_id: "<filename>",
    batch_id: "<from telemetry chain>",
    matched_event_ids: ["<event_id>", ...],
    technique: "T1059.004",
    severity: "high",
    fired_at: "<ISO>",
    rule_version: "<sha256 of rule body>",
    evidence: { source: "<source>", event_count: <N> }
  },
  agent_dna: null,  # system-emitted by the engine
}
```

`rule_version` is the sha256 of the rule body so future
detection_fired events can be cross-referenced against the
exact rule that fired (rules change; the chain pins history).

**Decision 7 — Engine refuses to run if the rule set fails to
parse.**

Operator-facing: a single broken rule blocks the entire engine.
That's the right posture — silent skip would hide drift. The
operator must fix the bad rule (or remove it) for the engine to
resume. The daemon log surfaces the parse error at boot;
section-01 (static-config) extends to validate
`config/detection_rules/*.yml` so the harness catches drift before
the operator hits it at runtime.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | ADR doc + Sigma-subset parser + DetectionRule dataclass + tests | 1 burst |
| T2 | DetectionEngine + scan() integration into AdapterIngestor + per-batch hook | 1 burst |
| T3 | detection_engineer role (trait_tree + genre + constitution_template + tool_catalog + handoffs + d3 domain + signature skill + birth + tests) | 1 burst (long) |
| T4 | section-01 harness extension: validate rules at static-config check; section-08 awareness of detection_fired event type | 1 burst |
| T5 | Operator runbook (writing rules, tuning false-positives, reviewing matches) + initial rule library (5-10 starter rules covering common ATT&CK techniques) | 1 burst |
| T6 | CLOSE — final tests, north-star update, status: Accepted | 1 burst |

Total: ~6 bursts. Phase C = ADR-0065 T1-T6.

## Consequences

**Positive:**

- D3 Phase C ships. The SOC gains continuous deterministic
  detection on top of the LLM-grade ad-hoc surface.
- Sigma-subset choice means operators can author rules in a
  format they already know from other SIEMs.
- MITRE ATT&CK tagging gives the steward + operator a coverage
  matrix view essentially for free.
- detection_fired events on the chain mean every detection is
  tamper-evident + queryable + cross-referenceable with the
  telemetry it scored against.

**Negative:**

- Synchronous in-process scan adds per-batch overhead. For T2's
  starter rule set this is fine; growth past ~50 rules may
  warrant the async queue T-future contemplates.
- Operator-authored rules carry false-positive risk. The
  engineer's `propose_detection.v1` skill helps, but the
  operator owns rule quality.
- Sigma-subset means migrating to full Sigma later requires a
  parser swap. Subset choice keeps the substrate scope tight;
  full Sigma is a v2 decision if/when needed.

**Open questions:**

- Hot-reload safety: `POST /detections/reload` re-parses + swaps
  the rule set under the write_lock. Mid-scan reload is queued
  to after the current batch completes. Same pattern as
  `/skills/reload` per ADR-0031 T7.
- Cross-batch correlation (e.g. "5 process_spawns from one user
  within 60s"): out of scope for T1. The subset's selection +
  condition shape handles within-batch; cross-batch is a
  separate engine layer (T-future).
- Multi-host detection: today single-host. If the SOC fleets, the
  engine needs a `host_id` dimension same as telemetry; defer
  to the same future multi-host ADR ADR-0064 mentioned.

## See Also

- ADR-0033 — Security Swarm (the existing LLM-grade detectors)
- ADR-0064 — telemetry pipeline (the substrate detection scans)
- ADR-0066 — SOAR playbooks (the downstream consumer; Phase D)
- ADR-0078 — D3 Local SOC umbrella (this ADR's parent)
- `data/telemetry.sqlite` — what the engine scans
- `config/detection_rules/` — where rules live (created on demand)
- https://sigmahq.io/ — Sigma reference (the format we subset)
- https://attack.mitre.org/ — ATT&CK technique catalog
