# ADR-0064 — Telemetry pipeline

**Status:** Proposed
**Date:** 2026-05-17
**Tracks:** Security / D3 Local SOC Phase B substrate
**Supersedes:** none
**Builds on:** ADR-0033 (Security Swarm), ADR-0049 (per-event
signatures), ADR-0050 (encryption-at-rest), ADR-0073 (audit chain
segmentation), ADR-0078 (D3 advanced rollout — the umbrella)
**Unblocks:** D3 Phase B (telemetry_steward +
threat_intel_curator), portions of D5 (Smart Home) and D6
(Finance) that benefit from a continuous-ingest substrate

## Context

ADR-0078 named three queued infrastructure ADRs that the D3 SOC
advanced rollout pulls in. This one — **telemetry pipeline** — is
the gating ADR for Phase B (`telemetry_steward` +
`threat_intel_curator`).

The existing Security Swarm (ADR-0033) reads logs ad-hoc when an
operator dispatches a triage capability. A real SOC needs a
**continuous-ingest substrate**: telemetry events flow in from
multiple sources, get integrity-stamped, get stored under a
retention policy, and become queryable by the
`telemetry_steward` agent (and downstream by `anomaly_ace` /
`response_rogue` / Phase D's `purple_pete`).

What the existing substrate gives us and what it doesn't:

- **Audit chain (ADR-0049, ADR-0073).** Already provides hash-
  linked + segment-sealed event storage with tamper-evidence.
  But it's the GOVERNANCE chain (who-did-what); it would be the
  wrong place to ingest 10K/day of macOS unified-log entries.
  Co-locating them would inflate the chain past the
  lazy-summarization budget + force every SOC noise event to
  carry the same signature overhead as a constitution-change
  event.
- **Encryption-at-rest (ADR-0050).** Already covers the registry
  + soul/constitution files. The telemetry store is a new
  sqlite database that needs the same wrapper.
- **Per-tool sandbox (ADR-0051).** Already isolates tool
  execution. Telemetry ingestion adapters need the same
  treatment — they pull from sources the agent doesn't trust
  (system logs, network sockets, third-party feeds).

Telemetry ≠ audit chain. **Telemetry events describe what
HAPPENED in the world the operator's machine observes.** Audit
chain events describe what the AGENT KERNEL DID. Different
provenance, different signing keys, different retention,
different consumer. The telemetry store is a sibling subsystem,
not an extension of the chain.

## Decision

**Decision 1 — Canonical telemetry event shape.**

Every telemetry event, regardless of source, is a
`TelemetryEvent`:

```python
@dataclass(frozen=True)
class TelemetryEvent:
    event_id: str                  # uuid4, server-assigned
    timestamp: str                 # ISO 8601 with timezone offset
    source: str                    # what produced this (e.g.
                                   #   "macos_unified_log",
                                   #   "lsof", "process_monitor")
    event_type: str                # see Decision 2 enum
    severity: str                  # info | warn | critical
    payload: dict                  # source-specific structured data
    correlation_id: str | None     # optional grouping (incident id,
                                   #   session id, parent event)
    integrity_hash: str            # sha256 of canonical_form()
    ingested_at: str               # ISO 8601 — when the daemon stored it
    retention_class: str           # standard | security_relevant | ephemeral
```

The `integrity_hash` is computed by `canonical_form()` — a
deterministic JSON serialization that excludes `event_id` (which
is server-assigned) and `ingested_at` (which is server-assigned).
This lets external ingestors compute the hash independently +
the daemon verify on receipt.

**Decision 2 — Closed event-type enum + open extension via
`source`.**

Eight canonical event types ship in T1:

| event_type | purpose | typical source(s) |
|---|---|---|
| `process_spawn` | new process started | process_monitor, audit subsystem |
| `process_exit` | process terminated | same |
| `network_connection` | TCP/UDP open or close | lsof, pf, network_monitor |
| `file_change` | file created/modified/deleted | fsevents, inotify, audit |
| `auth_event` | login/logout/sudo/keychain unlock | os.log, pam |
| `log_line` | structured log entry from any logger | macos_unified_log, syslog, journald |
| `policy_decision` | a security policy fired (e.g. xprotect quarantine) | os.log filtered |
| `sensor_reading` | catchall for typed numeric/string readings | smartmon, temperature, anything custom |

The `source` field is open: any string an adapter declares.
The `event_type` field is closed: adapters that don't fit one of
the eight emit `sensor_reading` with the kind in `payload`.

**Decision 3 — Storage backend = SQLite, separate file from
registry.**

Telemetry lives at `data/telemetry.sqlite`, NOT in the registry
(`data/registry.sqlite`). Rationale:

- Different lifecycle: telemetry retention sweeps drop rows;
  registry never does (registry deletes go through migrations).
- Different lock discipline: telemetry can tolerate
  multi-writer (we serialize via a separate `telemetry_write_lock`)
  because adapters may legitimately ingest from N producers.
  Registry is single-writer per ADR-0007 — never compromise.
- Different encryption posture: encrypted-at-rest via the same
  ADR-0050 wrapper, but with a separate key derivation salt
  so a compromised telemetry DB doesn't leak the master key.

Schema (v1):

```sql
CREATE TABLE telemetry_events (
    event_id        TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    source          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    correlation_id  TEXT,
    integrity_hash  TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    retention_class TEXT NOT NULL DEFAULT 'standard'
);
CREATE INDEX idx_telemetry_timestamp     ON telemetry_events(timestamp);
CREATE INDEX idx_telemetry_correlation   ON telemetry_events(correlation_id);
CREATE INDEX idx_telemetry_severity      ON telemetry_events(severity);
CREATE INDEX idx_telemetry_event_type    ON telemetry_events(event_type);
CREATE INDEX idx_telemetry_retention     ON telemetry_events(retention_class, timestamp);
```

The `retention_class + timestamp` composite index supports the
retention sweep (delete WHERE retention_class = ? AND timestamp <
?) without table scan.

**Decision 4 — Retention policy.**

Three retention classes:

| class | TTL | typical event_types |
|---|---|---|
| `ephemeral` | 7 days | high-volume noise (process_spawn for benign processes, log_line at info) |
| `standard` | 90 days | most events; baseline for unclassified |
| `security_relevant` | 365 days | auth_event, policy_decision, anything tagged severity=critical |

Retention sweep runs daily, hourly granularity. Each sweep emits
ONE `telemetry_retention_sweep` audit event with
`(cutoff_timestamp, class, count_deleted)`. No per-row audit —
that would re-inflate the chain.

Adapters tag retention_class at ingest time using a rule table
(initially: `auth_event`, `policy_decision`, severity=critical
→ `security_relevant`; everything else → `standard`; explicit
opt-in for `ephemeral`).

**Decision 5 — Audit chain integration: batch-ingest events,
not per-event events.**

Every batch of N events (default N=100, configurable) emits one
audit chain entry:

```json
{
  "event_type": "telemetry_batch_ingested",
  "batch_id": "<uuid4>",
  "count": 100,
  "integrity_root": "<sha256 of sorted event integrity_hashes>",
  "source_summary": {"macos_unified_log": 87, "lsof": 13}
}
```

This gives the audit chain a tamper-evidence anchor: if any
event in the batch is altered post-ingest, recomputing the
Merkle-like root from the stored events won't match the chain
entry. We don't put per-event hashes in the chain — that would
defeat the inflation-avoidance reason for separating telemetry
from the chain in the first place.

Verification path (operator-driven, runs on demand via `fsf
telemetry verify <batch_id>`):
1. Read the `telemetry_batch_ingested` chain entry → get
   `integrity_root` + `count`.
2. Query telemetry store for all events with that `batch_id`.
3. Sort by event_id, concatenate `integrity_hash`es, sha256 →
   compare against `integrity_root`.

A mismatch surfaces as `tamper_suspected` per the ADR-0073
sealed-segment verifier pattern.

**Decision 6 — Per-tool sandbox for ingestion adapters.**

Telemetry adapters are subprocess-sandboxed via ADR-0051. Each
adapter declares:
- `source` string (must be allowlisted in
  `config/telemetry_sources.yaml`)
- An exec command (e.g., `tail -F /var/log/system.log`, `lsof
  -i -P -n -r 5`, `log stream --predicate "..."`)
- An output parser (Python callable that turns adapter stdout
  into `TelemetryEvent` objects)

The adapter subprocess runs under the same per-tool subprocess
sandbox the ADR-0051 catalog uses. Crash isolation: a misbehaving
parser can't take down the daemon.

This ADR defines the SUBSTRATE — `TelemetryEvent` + store +
retention + chain integration. **Adapters ship in T2** (the next
burst). The reference adapter is `macos_unified_log_adapter` —
read-only, narrow predicate, demonstrates the end-to-end path.

**Decision 7 — `telemetry_steward` is a guardian-genre
read-only role.**

`telemetry_steward`'s job is to ingest (via adapters), tag
retention class, and emit batch-ingested audit events. It does
NOT analyze (`anomaly_ace` already does that), does NOT respond
(`response_rogue` does), does NOT generate alerts on its own.
Pure observability hygiene.

The role ships in **T4** (after the substrate + adapters land).

## Consequences

**Positive:**

- D3 Phase B can ship. `telemetry_steward` has a real
  continuous-ingest substrate to operate against.
- The chain stays small. Per-event audit entries would 100×
  the chain's growth rate; batch-ingest keeps the
  governance-chain budget intact.
- Encryption-at-rest is preserved. Same ADR-0050 wrapper, new
  salt, no new key material to manage.
- D5 (Smart Home) and D6 (Finance) get a substrate they can
  reuse. Smart Home telemetry (motion sensor events, HVAC
  state) and Finance telemetry (transaction events, account
  state changes) both fit the same shape.

**Negative:**

- New storage backend = new operational surface. Backup + the
  daily retention sweep + the periodic encryption rotation all
  have to add `data/telemetry.sqlite` to their scope. T5 ships
  the operator runbook covering this.
- Adapters are the long tail. The substrate is finite; the set
  of useful adapters (macOS unified log, lsof, fsevents, syslog,
  pf, eBPF, ...) is open-ended. T2 ships ONE reference adapter;
  more land as the operator declares specific needs.
- Per-tool sandboxing has overhead. Each adapter forks a
  subprocess. For high-frequency adapters (e.g., process_spawn
  on a busy host) the per-event overhead matters. T6 adds a
  micro-batching layer in the parser so the subprocess pipes
  one batch every N seconds rather than spamming one event per
  pipe write.

**Open questions:**

- Retention-class override mechanism: per-event class
  determined by rule table OR by adapter declaration OR by
  post-ingest reclassifier? Default: rule table; adapters can
  override per-event; reclassifier is a manual operator tool.
- Cross-host telemetry: today single-host. If the SOC fleets,
  the chain integration needs a host_id dimension + cross-host
  correlation_id. Out of scope for T1; defer to a future ADR
  if the multi-host arc actually starts.
- External SIEM forwarding: out of scope. Operators who want
  Splunk / Datadog / SentinelOne integration write a forwarder
  adapter that consumes from the telemetry store + pushes
  outward. Not in T1-T6.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | THIS BURST (B348). ADR + TelemetryEvent + TelemetryStore + retention + tests | 1 burst |
| T2 | macos_unified_log reference adapter + adapter substrate (subprocess + parser contract) | 1 burst |
| T3 | Audit chain integration: batch-ingest event emission + `fsf telemetry verify <batch_id>` CLI | 1 burst |
| T4 | telemetry_steward role + birth script + skill + handoffs wiring + tests | 1 burst (long) |
| T5 | Operator runbook (backups, retention sweep, adapter management) | 1 burst |
| T6 | Micro-batching layer + threat_intel_curator role (also Phase B) | 1 burst (long) — CLOSES Phase B |

Total: ~6 bursts. Phase B = ADR-0064 T1-T6.

## See Also

- ADR-0033 — Security Swarm (the existing 9-agent blue team)
- ADR-0049 — per-event signatures (the chain's tamper-evidence
  primitive)
- ADR-0050 — encryption-at-rest
- ADR-0051 — per-tool subprocess sandbox (adapter isolation)
- ADR-0073 — audit chain segmentation (the inflation-budget
  reason for batch-ingest)
- ADR-0078 — D3 Local SOC advanced rollout (the umbrella that
  pulls this ADR in)
- `data/telemetry.sqlite` — store location (created on demand)
- `config/telemetry_sources.yaml` — adapter allowlist (ships in T2)
