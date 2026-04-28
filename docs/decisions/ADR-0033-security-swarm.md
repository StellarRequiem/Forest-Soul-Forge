# ADR-0033 — Security Swarm

- **Status:** Accepted
- **Date:** 2026-04-27 (filed) / 2026-04-28 (accepted after Phase E passed live)
- **Supersedes:** —
- **Acceptance evidence:** the canonical cross-agent chain fired end-to-end on
  2026-04-28 against a running daemon: `LogLurker.morning_sweep` →
  `AnomalyAce.investigate_finding` → `ResponseRogue.contain_incident` →
  `VaultWarden.key_audit`. 47 audit events, 4 skills, 3 `agent_delegated`
  hops, terminating cleanly at VaultWarden. See
  [`docs/audits/2026-04-28-phase-d-e-review.md`](../audits/2026-04-28-phase-d-e-review.md).
- **Related:** ADR-0019 (tool execution runtime — every Swarm tool ships through the dispatcher with audit + approval), ADR-0021 (role genres — this ADR adds three new genres `security_low/mid/high`), ADR-0022 (memory subsystem — Swarm chains depend on v0.2 cross-agent disclosure landing), ADR-0025 (threat model v2 — Swarm is a primary consumer of the model), ADR-0027 (memory privacy contract — per-tier ceilings flow from §1), ADR-0030 (Tool Forge — every Swarm tool is forged through the pipeline), ADR-0031 (Skill Forge — agent-level procedures are skill manifests).

## Context

The forge has a tool runtime, a memory subsystem, a genre engine, and the ability to forge new tools and skills on demand. It does not yet have a **defensive posture** — agents whose entire purpose is watching the operator's machine, escalating findings, and protecting the rest of the lineage from tampering. Filing this ADR is the gate for putting a real defense plane on the platform without doing it as cosplay.

The brief is nine agents arranged in three swarm tiers, each tier wider in privilege than the last:

```
  ┌──────────────── Low Swarm ─────────────────┐
  │ PatchPatrol — anxious perfectionist; OS &  │
  │   software inventory, patch nag, basic     │
  │   AV signature checks, config hardening.   │
  │ Gatekeeper  — blunt bouncer; firewall +    │
  │   network access controls, MFA enforcer,   │
  │   USB/device control.                      │
  │ LogLurker   — introverted hoarder; log     │
  │   aggregation, obvious-pattern matching,   │
  │   simple alerting.                         │
  └────────────────────────────────────────────┘
                     ▼ escalates to ▼
  ┌──────────────── Mid Swarm ─────────────────┐
  │ AnomalyAce    — curious detective; SIEM,   │
  │   UEBA, baseline + anomaly scoring.        │
  │ NetNinja      — calm wire-watcher; NDR,    │
  │   protocol dissection, lateral movement.   │
  │ ResponseRogue — action hero; SOAR style    │
  │   triage, isolation, evidence collection.  │
  └────────────────────────────────────────────┘
                     ▼ escalates to ▼
  ┌──────────────── High Swarm ────────────────┐
  │ ZeroZero       — cold clinical paranoid;   │
  │   continuous auth, JIT access, posture,    │
  │   micro-segmentation.                      │
  │ VaultWarden    — overprotective guardian;  │
  │   key inventory, HSM-bounded rotation,     │
  │   tamper detection.                        │
  │ DeceptionDuke  — playful trickster;        │
  │   honeypots, canary tokens, decoy files,   │
  │   attacker-time-wasting.                   │
  └────────────────────────────────────────────┘
```

The shape is intentionally a strict hierarchy: low catches the obvious, mid digs into what low can't explain, high assumes everything else is hostile. Each tier escalates *up* via Memory v0.2 disclosure, never silently jumping levels. Anything that crosses a tier boundary is an audit-chain event.

## What this ADR is **not**

Three claims must be retired up front so we don't quietly fake-deliver:

1. **Not an EDR/NDR/SIEM/SOAR replacement.** Forest is not a real-time control plane. The Swarm reads what the daemon and operator-approved tools can read, reasons over it via LLM and forged tools, and recommends or — with explicit approval — actuates. Where the brief says "SIEM" or "SOAR," what we deliver is a *log-correlation engine* and an *approval-gated playbook runner*. They are honest neighbors of those products, not those products.
2. **Not a substitute for hardware.** VaultWarden does not perform HSM operations without an HSM. Without hardware (YubiKey HSM, Nitrokey, SE050, equivalent), VaultWarden's "key management" reduces to permission-hardened key files + rotation reminders. The capability is gated on the operator confirming hardware is present; the agent's soul.md states the limitation explicitly.
3. **Not a packet-capture stack.** NetNinja sees flow-level telemetry from `lsof -i`, `pfctl -ss`, and operator-supplied flow exports. Wire-level packet inspection requires pcap/eBPF and root, which is a separate decision (see §Privilege model). Until that decision is made, NetNinja's "protocol dissection" reads what the OS already exposes — service banners, connection tuples, DNS queries — not packet payloads.

These three retirements are the difference between this ADR being honest and it being a pitch deck. Every agent's soul.md inherits a "what I cannot do" stanza derived from this section.

## Decision

The Security Swarm is an **expansion of the genre engine** with three new genres (`security_low`, `security_mid`, `security_high`), nine canonical role definitions, ~25 forged tools split by tier, a per-tier approval-policy graduation, and an inter-agent delegation primitive that permits skill chains across the tier boundary with explicit audit. Existing infrastructure (audit chain, approval queue, Tool Forge, Skill Forge, Memory v0.1, Memory v0.2 once it lands) carries the entire weight; this ADR adds no new subsystems.

### Three new genres

Added to `config/genres.yaml`:

```yaml
  security_low:
    description: |
      Always-on, narrow-scope defenders that catch the obvious. Read-only or
      local-advisory by default. Findings flow up to security_mid via memory
      lineage scope; never act outside their own narrow tool kit.
    risk_profile:
      max_side_effects: read_only
      memory_ceiling: lineage   # per ADR-0027 §1
    default_kit_pattern:
      - host_inventory
      - log_observation
      - policy_audit
    trait_emphasis: [vigilance, audit_trail_discipline, thoroughness, double_checking]
    memory_pattern: short_retention
    spawn_compatibility: [security_low, security_mid]
    roles: [patch_patrol, gatekeeper, log_lurker]

  security_mid:
    description: |
      Investigators and responders. Read broadly across host telemetry,
      correlate, score anomalies, and — with operator approval — contain.
      Receives findings from security_low and emits its own to security_high.
    risk_profile:
      max_side_effects: network
      memory_ceiling: lineage
    default_kit_pattern:
      - cross_source_correlation
      - behavioral_baseline
      - host_containment
    trait_emphasis: [evidence_demand, lateral_thinking, technical_accuracy, suspicion]
    memory_pattern: episodic_long
    spawn_compatibility: [security_low, security_mid, security_high]
    roles: [anomaly_ace, net_ninja, response_rogue]

  security_high:
    description: |
      The paranoid apex. Assumes hostility, gates everything, and runs the
      smallest blast radius the platform can produce. Memory writes default
      to private; cross-tier disclosure is explicit and audited. Spawns are
      tier-internal — high never spawns out of tier.
    risk_profile:
      max_side_effects: external
      memory_ceiling: private
      provider_constraint: local_only   # mirrors Companion's floor
    default_kit_pattern:
      - zero_trust_enforcement
      - key_management
      - deception_primitives
    trait_emphasis: [caution, suspicion, evidence_demand, audit_trail_discipline]
    memory_pattern: long_consolidated
    spawn_compatibility: [security_high]
    roles: [zero_zero, vault_warden, deception_duke]
```

The `memory_ceiling` field is **new** in genres.yaml. ADR-0027 §1 already names per-genre ceilings; this ADR makes them explicit YAML rather than implicit-in-prose. Loader extends to validate the field against the four ADR-0027 scopes (`private`, `lineage`, `consented`, `realm`).

### Nine canonical roles

Each role is a soul.md + trait emphasis + tool override + skill catalog tuple. The catalog cross-cuts: an agent's tools come from forged plugins (§Toolkit), its skills from manifests authored via Skill Forge.

| Role | Genre | Trait emphasis (additive over genre) | Defining tools | Defining skills |
|---|---|---|---|---|
| `patch_patrol` | security_low | suspicion, audit_trail_discipline | `patch_check`, `software_inventory`, `config_audit` | `daily_patch_sweep`, `cve_diff_report` |
| `gatekeeper` | security_low | caution, formality, directness | `port_policy_audit`, `usb_device_audit`, `mfa_check` | `connectivity_baseline`, `device_inventory` |
| `log_lurker` | security_low | vigilance, thoroughness | `log_scan`, `log_aggregate`, `audit_chain_verify` | `morning_sweep`, `signal_match`, `chain_health_probe` |
| `anomaly_ace` | security_mid | lateral_thinking, evidence_demand | `behavioral_baseline`, `log_correlate`, `anomaly_score`, `ueba_track` | `investigate_finding`, `baseline_refresh` |
| `net_ninja` | security_mid | technical_accuracy, vigilance | `traffic_flow_local`, `lateral_movement_detect`, `port_scan_local` | `wire_review`, `lateral_audit` |
| `response_rogue` | security_mid | directness, caution | `triage`, `isolate_process` (privileged), `evidence_collect` | `contain_incident`, `evidence_capture` |
| `zero_zero` | security_high | caution, suspicion (extreme) | `jit_access`, `continuous_verify`, `posture_check`, `dynamic_policy` | `tighten_policy`, `verify_session`, `posture_drift_audit` |
| `vault_warden` | security_high | audit_trail_discipline, formality | `key_inventory`, `tamper_detect`, `file_integrity` | `key_audit`, `tamper_probe`, `rotation_reminder` |
| `deception_duke` | security_high | lateral_thinking, technical_accuracy | `canary_token`, `honeypot_local`, `decoy_file` | `lay_canaries`, `attacker_track` |

Trait emphasis is *additive* over the genre's: an agent inherits the genre-level emphasis and adds its own for the LLM voice renderer (ADR-0017) to weight.

### Toolkit (split by reality)

The full forge target is **27 tools**, of which 22 are local-only (no operator decision needed beyond approval) and 5 are gated on operator decisions documented in §Privilege model.

```
LOW TIER (9 tools, all local-only)
─────────────────────────────────────────────────────────────
patch_check.v1            Query brew/apt/system updaters; emit pending CVEs.
software_inventory.v1     Walk installed apps; sha256 binaries; baseline diff.
config_audit.v1           Compare /etc + dotfiles against a baseline manifest.
log_scan.v1               Regex/pattern scan over a file or dir of log files.
log_aggregate.v1          Multi-file roll-up with timestamp normalization.
audit_chain_verify.v1     Walk the daemon's JSONL chain; verify hashes.
file_integrity.v1         sha256 baseline + diff over operator-named paths.
port_policy_audit.v1      Read-only enumeration of listening ports + owners.
usb_device_audit.v1       Snapshot USB device tree; diff against baseline.
mfa_check.v1              Verify MFA posture across configured accounts.

MID TIER (10 tools, 9 local-only + 1 privileged)
─────────────────────────────────────────────────────────────
behavioral_baseline.v1    Per-process / per-user activity profile in memory.
log_correlate.v1          Cross-source join over normalized log streams.
anomaly_score.v1          Score deviation from baseline; emit ranked findings.
ueba_track.v1             Per-user behavioral fingerprint over time.
traffic_flow_local.v1     Parse `lsof -i` + `pfctl -ss` into flow records.
lateral_movement_detect.v1Graph analysis over local connection history.
port_scan_local.v1        TCP/UDP scan against 127.0.0.1/lo only.
triage.v1                 Take an alert, ask N diagnostic questions, score.
isolate_process.v1   ⚠    Kill / network-quarantine a process. SUDO REQUIRED.
evidence_collect.v1       Snapshot process tree, open files, env, into a tarball.

HIGH TIER (8 tools, 5 local-only + 2 sudo + 1 hardware)
─────────────────────────────────────────────────────────────
jit_access.v1             Time-bound credential grant; revoked at expiry.
continuous_verify.v1      Re-check posture every N seconds during a session.
posture_check.v1          Device + user + process posture snapshot.
dynamic_policy.v1   ⚠     Push / revoke a pf rule. SUDO REQUIRED.
key_inventory.v1          Enumerate keys, expirations, scopes.
tamper_detect.v1     ⚠    Verify boot-chain + system-bin signatures. SUDO REQUIRED if reading SIP-protected paths.
canary_token.v1           Plant tracked decoy files; alert on access.
honeypot_local.v1   ⚠     Bind an unused port; log connection attempts.

⚠ = operator decision required (see §Privilege model)
```

`vault_warden`'s key-rotation capability is not in this list because it depends on hardware: when an HSM is present, `key_rotate.v1` is forged on top of the device's CLI; absent hardware, the role exposes only `key_inventory.v1` and `rotation_reminder` (a skill, not a tool). VaultWarden's soul.md is explicit about this.

### Cross-agent chains via `delegate.v1`

One new built-in tool: `delegate.v1`. It takes a target agent ID, a target skill name, an arguments object, and a one-line `reason`. Audit chain logs `agent_delegated` with the calling agent, the target, the skill, and the reason. The target agent's skill runtime fires as if the operator triggered it, except the audit chain entry is labeled `triggered_by: agent:<caller_id>`.

Three canonical chains ship at Phase D:

```
LogLurker.morning_sweep
  → finds 3 anomalous signal matches
  → memory.write(scope=lineage, summary)
  → delegate(AnomalyAce, "investigate_finding", {finding_ids:[...]})
       → AnomalyAce.investigate_finding
       → correlates against baseline
       → memory.write(scope=lineage, score)
       → if score > THRESHOLD: delegate(ResponseRogue, "contain_incident", ...)
            → ResponseRogue.contain_incident
            → snapshot + isolate (sudo gate fires)
            → memory.write(scope=lineage, evidence_path)
            → delegate(VaultWarden, "key_audit", ...)
                 → VaultWarden audits key state, no further chain.
```

Every link is an audit-chain event. Every memory write respects the per-tier ceiling. Every privileged step hits the approval queue. The chain is **explicit and inspectable** — there is no hidden swarm gossip.

### Privilege model — operator decisions

Five tools depend on operator decisions before they can be forged honestly:

1. **`isolate_process.v1`** — kills a PID or quarantines a process via firewall rule. Requires either (a) the daemon running as root, or (b) a sudo helper script the operator installs at `/usr/local/sbin/fsf-priv` and authorizes via `/etc/sudoers.d/fsf`. Recommendation: option (b). The helper accepts a strict allowlist of operations from the daemon over a Unix-domain socket; everything else is rejected. **Decision needed: install sudo helper yes/no.**
2. **`dynamic_policy.v1`** — pushes/revokes pf rules. Same sudo helper as above.
3. **`tamper_detect.v1`** — reads SIP-protected paths to verify boot chain. **Decision needed: enable SIP-protected reads yes/no.** Without, `tamper_detect` covers user-space binaries only.
4. **`honeypot_local.v1`** — binds an unused port. No privilege required for ports ≥1024; ports <1024 require root or sudo helper. Recommendation: stick to ≥1024 and let DeceptionDuke pick the port.
5. **VaultWarden hardware** — HSM-backed `key_rotate` requires a YubiKey HSM, Nitrokey HSM, or equivalent. **Decision needed: hardware present? If yes, which?** If absent, the tool is omitted and the agent's documented capability is "advise + remind."

These five decisions are the **only operator gates** in the entire build. Everything else is local-only and forge-routine.

### External integrations (deferred)

Adapter MCPs for real defense products (Wazuh, Suricata, CrowdStrike Falcon, Microsoft Defender for Endpoint, 1Password CLI, pf via remote agent, etc.) are **out of scope** for this ADR. They land later as ADR-0019 T7-T9 (MCP integration) consumers. The Swarm is designed to upgrade in place: a `wazuh.mcp` connector landing means `log_lurker`'s tool kit gets `wazuh.search` added without re-birthing the agent.

The ADR commits to: when a real product connector exists, the corresponding role's kit is extended. It does **not** commit to building any specific connector here.

### Audit + memory integration

Eight new audit event types extend the existing chain (KNOWN_EVENT_TYPES is updated):

```
agent_delegated              — delegate.v1 fired
swarm_escalation             — finding crossed a tier boundary
swarm_containment            — privileged action approved + executed
swarm_decoy_triggered        — canary or honeypot caught something
swarm_posture_drift          — continuous_verify saw a drift
swarm_policy_pushed          — dynamic_policy.v1 actuated
swarm_key_rotation_advised   — VaultWarden flagged a rotation due
swarm_chain_anomaly          — audit_chain_verify found a hash break
```

Every chain event includes the swarm tier, the calling and called agents, and a correlation ID that ties a chain together (so Phase E's smoke test can assert end-to-end coherence).

Memory v0.2 disclosure (ADR-0022 v0.2 T12-T17) is **a hard prerequisite** for the full chain: without `mode=lineage` recall, a mid-tier agent cannot read what a low-tier agent wrote. Phase A includes shipping v0.2 alongside the genre work; the Swarm cannot fully wire until v0.2 lands.

## Phases

```
PHASE A — Foundation                              (~3 rounds)
  A1  Add security_low/mid/high to genres.yaml + memory_ceiling field
  A2  Memory v0.2 implementation: T12-T17 (already designed)
  A3  delegate.v1 built-in tool + audit event
  A4  Per-genre approval policy graduation
  A5  spawn_compatibility tests + tier-crossing audit events

PHASE B — Toolkit forge                           (~5 rounds)
  B1  Low tier tools (9)        — patch_patrol/gatekeeper/log_lurker tools
  B2  Mid tier tools (10)        — anomaly_ace/net_ninja/response_rogue tools
  B3  High tier tools (8)        — zero_zero/vault_warden/deception_duke tools
  Each tool: forge → static-analyze → sandboxed test → install as plugin

PHASE C — Adapter MCPs                            (open-ended, optional)
  Built only when a real product is on the operator's machine.

PHASE D — Birth + skill catalog                   (~3 rounds)
  D1  Birth 9 agents with soul.md + trait emphasis + tool overrides
  D2  Forge ~25 skill manifests (2-3 per agent)
  D3  Wire the three canonical chains via delegate.v1

PHASE E — Validation                              (~1 round)
  E1  scripts/security-smoke.sh — synthetic-incident drive
  E2  Regression suite for the chain
  E3  Frontend: Swarm tab listing tiers, agents, recent chain events
  E4  Push to GitHub, ship.

Total realistic envelope: ~12 rounds, 3-5 weeks of focused build.
```

## What this does NOT do (canonical retirement list)

- Does not deploy real firewall rules without the sudo helper installed.
- Does not perform real HSM operations without hardware.
- Does not capture packets at the wire level without pcap/eBPF + root.
- Does not replace EDR, NDR, SIEM, SOAR, or zero-trust products.
- Does not fetch threat intel from the internet without an explicit connector being configured (no implicit egress).
- Does not silently chain across tiers — every escalation is an audit event.
- Does not allow an agent in `security_high` to spawn out-of-tier — apex is a sink.
- Does not write to `realm` scope from any security tier — findings stay at `lineage` or tighter.

## Open questions for the operator

These are the only items where the build pauses for input. Ordered by when they bite:

1. **Sudo helper** — install `/usr/local/sbin/fsf-priv` + `/etc/sudoers.d/fsf` to enable `isolate_process.v1` and `dynamic_policy.v1`? (Phase B2/B3 gate.)
2. **SIP-protected reads** — enable for `tamper_detect.v1`? (Phase B3 gate.)
3. **HSM hardware** — present? Which model? (Phase D gate; if absent, VaultWarden role is birthed without `key_rotate`.)
4. **External products** — Wazuh, Suricata, Defender, 1Password CLI, others present that we should adapter-wrap? (Phase C gate; adapters built only on confirmed need.)

The build proceeds through Phase A and the unprivileged portions of Phase B without any of these answered. Privileged tools and high-tier skills hold until the answers land.

## Consequences

- **Genre catalog grows from 7 to 10.** All existing tests stay green; new tests cover the three additions.
- **Memory v0.2 becomes a hard ship.** This ADR's value is gated on cross-agent disclosure, so the v0.2 design from ADR-0022 must promote from design to code in Phase A.
- **Audit chain gains 8 new event types.** KNOWN_EVENT_TYPES grows; old events stay parseable.
- **Approval queue load increases.** Every privileged Swarm action passes through it. UX for the operator must keep up — Phase E includes a Swarm-aware Approvals view.
- **Soul.md narratives carry "what I cannot do" stanzas.** Forced honesty per the §What this ADR is not retirement list.
- **Forest becomes a defensive platform in addition to a generative one.** This is the first ADR that gives the system *teeth* — every previous capability either describes, recalls, or generates. The Swarm is the first family of agents whose purpose is to act on the operator's environment.

## Ship criteria

The Security Swarm ships when:

1. All three new genres pass the genres.yaml loader test.
2. Memory v0.2 cross-agent disclosure is in code, not just spec.
3. All 22 unprivileged tools are forged, installed, and addressable.
4. The three canonical chains drive end-to-end through `scripts/security-smoke.sh`.
5. Frontend Swarm tab renders the 9 agents with their tier, recent chain events, and current memory state.
6. `tests/integration/test_security_swarm_smoke.py` passes against a clean state.

Privileged tools (`isolate_process`, `dynamic_policy`, `tamper_detect`, HSM-backed `key_rotate`) are **deferred to "ships when operator answers the gating question"** and do not block initial ship.
