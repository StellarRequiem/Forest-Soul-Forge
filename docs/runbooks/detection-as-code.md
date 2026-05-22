# Runbook — Detection-as-code

**ADR:** ADR-0065 (D3 Local SOC Phase C)
**Tranches shipped:** T1 (B389 Sigma-subset parser) → T2 (B390
DetectionEngine + ingest hook) → T3 (B391 detection_engineer role)
→ T4 (section-01/08 harness extension) → **T5 this** (runbook +
starter rule library). T6 closes Phase C.
**Audience:** SOC operator (Alex) running the kernel locally.

## What detection-as-code adds

The Security Swarm (ADR-0033) does LLM-grade, operator-dispatched
detection — high signal, high cost, ad-hoc. The telemetry pipeline
(ADR-0064) added continuous ingestion. Detection-as-code is the
layer between them: **operator-authored rules that examine each
telemetry batch deterministically and emit `detection_fired` audit
chain events** — no per-event LLM judgment in the hot path.

The two surfaces are complementary. Rules catch the known patterns
cheaply and continuously; `anomaly_ace` does LLM follow-up on what
the rules flag.

## Pipeline shape

```
AdapterIngestor.flush_pending()
        │
        ▼
TelemetryStore.ingest_batch()  ── returns batch_id
        │
        ▼
AuditChain.append("telemetry_batch_ingested", {...})   ← B377 anchor
        │
        ▼
DetectionEngine.scan(batch_id, batch_events, audit_chain)
        │  for rule in active_rules:
        │    matches = [rule.evaluate(ev) for ev in batch]
        │    if matches: append one "detection_fired" entry
        ▼
AuditChain.append("detection_fired", {
  rule_id, rule_version, batch_id, technique, severity,
  matched_event_ids, match_count, fired_at })
```

The scan runs **synchronously after the batch anchor lands** — same
store-first / anchor-second posture as B377. A misbehaving rule set
can't lose the telemetry data or the anchor; engine failure is
recorded in `ingestor.stats.last_error` and surfaced by the harness
(section-08), never silently swallowed.

One `detection_fired` event is emitted **per (rule, batch) pair** —
N matching events collapse into one chain entry carrying the full
`matched_event_ids` list. Chain growth stays proportional to firing
rules, not firing events.

## Wiring the engine

The DetectionEngine is wired alongside the telemetry ingestor — the
same operator-driven lifespan wiring described in
`docs/runbooks/telemetry-pipeline.md` ("Adding a new adapter"). The
engine is **not auto-started**; an operator who enables telemetry
ingestion also constructs the engine and hands it to the ingestor:

```python
from pathlib import Path
from forest_soul_forge.security.detection import DetectionEngine
from forest_soul_forge.security.telemetry.ingestor import AdapterIngestor

engine = DetectionEngine(rules_dir=Path("config/detection_rules"))
if not engine.ready():
    # ADR-0065 D7 — one bad rule blocks the whole engine. Don't
    # wire it; surface the punch list and let the operator fix it.
    for path, err in engine.load_errors:
        log.error("detection rule failed: %s — %s", path, err)

ing = AdapterIngestor(
    MyAdapter(), store,
    audit_chain=app.state.audit_chain,
    detection_engine=engine,        # ← the only new line vs. ADR-0064
)
ing.start()
```

If `detection_engine` is omitted the ingestor behaves exactly as in
Phase B (telemetry anchors, no detection scan). The engine is purely
additive.

## The Sigma subset

Rules live in `config/detection_rules/*.yml` — **one rule per file,
filename matches the rule `id`**. They are operator-editable,
code-reviewed via PR, and committed to git. The format is a defined
**subset of Sigma** (the industry SIEM rule lingua franca) so rules
stay portable.

A rule:

```yaml
id: <snake_case id; must match the filename>
title: <one-line human title>
description: |
  <why this rule exists; what a match means; tuning notes>
level: <informational | low | medium | high | critical>
tags:
  - attack.T1059.004        # ≥1 MITRE ATT&CK technique — MANDATORY
logsource:
  source: <telemetry source name>      # optional — omit to match any source
  event_type: <closed event_type enum> # optional — omit to match any type
detection:
  <selection_name>:
    <field.dotted.path>: <expected literal value>   # equality match
  <another_selection>:
    <field>: <value>
  condition: <boolean expression over selection names>
```

**Within a selection**, every `field: value` pair must match (AND).
**Field names are dotted paths** into the event payload —
`process.image` resolves `payload["process"]["image"]`.
**The condition** is a boolean expression over selection names
supporting `and`, `or`, `not`, and parentheses.

`level`, `detection` (with ≥1 selection + a `condition`), and at
least one `tags` entry are required. `title`/`description`/
`logsource` are optional. ATT&CK tagging is **mandatory** (ADR-0065
D3): a rule with no `tags` fails to load. If you don't know the
technique, tag `attack.unknown` explicitly — that gives a
coverage-gap signal rather than a silent untagged rule.

### What the subset does NOT support

The parser **rejects** these with a clear error so you see the gap:

| Feature | Why excluded | Workaround |
|---|---|---|
| Field modifiers (`name\|contains:`) | Equality-only keeps eval O(events×selections) | Rewrite as exact equality, or wait for a v2 ADR |
| `timeframe` / aggregation (`count() > N`) | Cross-event correlation is a separate engine layer | Pair the rule with batch-volume review |
| Cross-batch correlation | The scan is within-batch | Same — v2 ADR if a concrete need lands |

A single broken rule **blocks the entire engine** (ADR-0065 D7) —
a silent skip would hide drift. `section-01` of the diagnostic
harness re-runs the exact parse the daemon does, so a bad rule is
caught at `diagnostic-all` time, before runtime.

## Payload field convention

Detection rules match against `TelemetryEvent.payload`. The closed
`event_type` enum (ADR-0064) and the recommended nested payload
shape an adapter should emit for rule-friendly detection:

| event_type | recommended payload paths |
|---|---|
| `process_spawn` / `process_exit` | `process.image`, `process.pid`, `process.ppid`, `process.parent_image`, `process.command_line`, `process.user` |
| `network_connection` | `network.direction`, `network.destination_ip`, `network.destination_port`, `network.protocol`, `process.image` |
| `file_change` | `file.path`, `file.directory`, `file.action`, `process.image` |
| `auth_event` | `auth.user`, `auth.method`, `auth.result`, `auth.source_ip` |
| `policy_decision` | `policy.name`, `policy.decision`, `policy.subject` |
| `log_line` | source-specific — `macos_unified_log` emits flat `subsystem`, `category`, `message_type`, `message`, `process`, `pid` |

The shipped `macos_unified_log` adapter emits only `auth_event` and
`log_line`. The other event types populate as endpoint / network
adapters are wired — a rule keyed to `process_spawn` is valid and
loads clean, it simply stays inert until an adapter feeds that type.

## Starter rule library

`config/detection_rules/` ships 8 starter rules — 6 templates keyed
to the closed event-type enum and 2 live against the shipped
`macos_unified_log` adapter:

| Rule | ATT&CK | Level | Status |
|---|---|---|---|
| `reverse_shell_listener_port` | T1571 Non-Standard Port | high | template (needs `network_connection` adapter) |
| `osascript_interpreter_spawn` | T1059.002 AppleScript | medium | template (needs `process_spawn` adapter) |
| `launchdaemon_persistence_write` | T1543.004 Launch Daemon | high | template (needs `file_change` adapter) |
| `gatekeeper_disable_attempt` | T1553.001 Gatekeeper Bypass | high | template (needs `process_spawn` adapter) |
| `keychain_credential_access` | T1555.001 Keychain | medium | template (needs `process_spawn` adapter) |
| `directory_service_account_enum` | T1087.001 Local Account Discovery | low | template (needs `process_spawn` adapter) |
| `opendirectory_auth_error` | T1110.001 Password Guessing | low | **live** — `macos_unified_log` |
| `xprotect_malware_flagged` | T1204.002 Malicious File | high | **live** — `macos_unified_log` |

The templates are editable starting points, not turnkey detections.
Each carries tuning notes in its `description`. Treat the level as a
hint to revisit once you've watched the rule's false-positive rate.

## Authoring a new rule

1. Copy the closest starter rule to
   `config/detection_rules/<new_id>.yml`. Set `id` to match the
   filename.
2. Pick the `event_type` from the closed enum and the field paths
   from the convention table above.
3. Tag the ATT&CK technique(s). Look the ID up at
   <https://attack.mitre.org/techniques/> — the path is
   `/techniques/<id>/`.
4. Validate before committing — the engine refuses to run if any
   rule fails:
   ```bash
   PYTHONPATH=src .venv/bin/python3 -c "
   from pathlib import Path
   from forest_soul_forge.security.detection import parse_rules_from_dir
   parsed, failed = parse_rules_from_dir(Path('config/detection_rules'))
   print(f'{len(parsed)} ok, {len(failed)} failed')
   for p, e in failed: print(f'  {p}: {e}')
   "
   ```
   Or run `dev-tools/diagnostic/section-01-static-config.command` —
   the `config/detection_rules/*.yml` check reports the same.
5. Commit via PR. Per ADR-0078 Decision 4, new detection rules
   route to `d4_code_review` for sign-off before merge.

## Tuning false positives

A rule that fires on benign activity erodes operator trust faster
than a missed detection. When a rule is noisy:

1. **Pull the matches.** Walk `detection_fired` events for that
   `rule_id` and inspect the `matched_event_ids` — what benign
   activity tripped it?
2. **Add an exclusion selection.** Equality-only means you exclude
   by naming the benign value and negating it:
   ```yaml
   detection:
     trigger:
       process.image: /usr/bin/osascript
     known_good_parent:
       process.parent_image: /Applications/SomeApp.app/Contents/MacOS/SomeApp
     condition: trigger and not known_good_parent
   ```
3. **Lower the level** if the rule is informational rather than
   actionable — `low` keeps it in the chain for correlation
   without demanding an operator response.
4. **Re-validate + commit.** The `rule_version` (sha256 of the rule
   body) changes when you edit, so `detection_fired` history pins
   exactly which version of the rule fired.

## Reviewing matches

`detection_fired` events are first-class audit chain entries.
Review them by walking the chain:

```bash
PYTHONPATH=src .venv/bin/python3 -c "
import json
from pathlib import Path
for line in Path('examples/audit_chain.jsonl').read_text().splitlines():
    e = json.loads(line)
    if e.get('event_type') == 'detection_fired':
        d = e.get('event_data', {})
        print(f\"seq={e.get('seq')} rule={d.get('rule_id')} \"
              f\"technique={d.get('technique')} severity={d.get('severity')} \"
              f\"matches={d.get('match_count')}\")
"
```

`section-08` of the diagnostic harness validates every
`detection_fired` entry carries the full ADR-0065 D6 shape, so a
malformed emitter is caught at `diagnostic-all` time rather than
when a downstream consumer trips over a missing field.

## The detection_engineer role

`detection_engineer` is the operator-facing **author** of rules —
born as `DetectionEngineer-D3`, genre `researcher`, posture YELLOW.
The role does NOT run rules at runtime (the engine does) and does
NOT write `config/detection_rules/` directly (the operator commits
rules). The engineer **proposes**; the operator **accepts**.

Its signature skill is `propose_detection.v1`:

1. `memory_recall` — prior proposals on the technique (dedupe).
2. `audit_chain_verify` — chain integrity before drawing trends
   from prior `detection_fired` events.
3. `web_fetch` — the ATT&CK technique reference page.
4. `text_summarize` — the reference, focused on detection-relevant
   data sources.
5. `llm_think` — synthesize a candidate Sigma-subset rule.
6. `memory_write` — record the proposal to **private memory** for
   operator review.

The skill never writes a rule file. The operator reads the
proposal, validates the YAML body parses, edits if needed, and
commits to `config/detection_rules/<id>.yml`.

**Posture note:** DetectionEngineer-D3 is born YELLOW. `web_fetch`
has no allowlisted domains at birth — the operator must allowlist
`https://attack.mitre.org/` (constitution patch or posture
override) before the first `propose_detection` call, then promote
to GREEN. This mirrors `threat_intel_curator`'s
`forbid_silent_feed_substitution` discipline.

## Hot reload

`DetectionEngine.reload_from_dir()` re-parses and atomically swaps
the rule set under the engine lock. Per ADR-0065 D7, **if any rule
fails the swap is refused** — the previous rule set is retained and
the failures land in `load_errors`. A partial rule set would mask
drift.

The `POST /detections/reload` HTTP endpoint (mirroring
`/skills/reload`) is a future tranche — see ADR-0065 Open
Questions. Today, a rule-set change takes effect on **daemon
restart**.

## Verification

After this runbook + the starter library land:

1. `dev-tools/diagnostic/section-01-static-config.command` →
   `config/detection_rules/*.yml` check PASS, 8 rules, full ATT&CK
   tag list in the evidence string.
2. `dev-tools/diagnostic/section-08-audit-chain-forensics.command`
   → `detection_fired events well-formed` PASS (skipped while the
   engine is idle; validates the shape once matches land).
3. `.venv/bin/python3 -m pytest tests/unit/test_b389_detection_parser.py
   tests/unit/test_b390_detection_engine.py
   tests/unit/test_b391_detection_engineer_wiring.py
   tests/unit/test_b392_detection_rules.py -q` → all green.
4. Birth DetectionEngineer-D3 if not already
   (`dev-tools/birth-detection-engineer.command`).

## Cross-references

- **ADR-0065** — the design doc; D1-D7 carry the decisions this
  runbook implements operationally.
- **ADR-0064** / `docs/runbooks/telemetry-pipeline.md` — the
  telemetry substrate the engine scans; the ingestor wiring this
  runbook extends.
- **ADR-0078** — the D3 Local SOC rollup; Phase C is this arc.
- **ADR-0033** — the Security Swarm; `anomaly_ace` is the LLM-grade
  consumer of `detection_fired` events.
- `config/detection_rules/` — the rule library.
- `src/forest_soul_forge/security/detection/` — parser + engine +
  dataclasses.
- <https://sigmahq.io/> — Sigma reference (the format subset).
- <https://attack.mitre.org/> — ATT&CK technique catalog.
