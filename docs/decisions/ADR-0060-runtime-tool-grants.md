# ADR-0060 — Runtime Tool Grants

- **Status:** Accepted
- **Date:** 2026-05-11 (drafted, accepted same day)
- **Supersedes:** —
- **Acceptance evidence:** T1 (this ADR's schema + accessor) lands in B219 alongside acceptance. Defaults frozen on the three open questions:
  - **trust_tier default:** `yellow`. Operators must explicitly pass `trust_tier=green` to grant fully-autonomous tier; explicit confirmation prevents accidentally granting maximum trust.
  - **T6 frontend scope:** deferred to a separate burst after T2-T5 land. The substrate must work via API before the UI surface adds polish.
  - **plugin_grants rename:** declined. The two tables differ enough (per-plugin vs per-(tool, version)) that unification would force awkward null columns. Keep them separate, share the architectural pattern not the SQL.
- **Related:** ADR-0001 (DNA + content-addressed soul — defines the immutable identity surface this ADR is the *runtime* counterpart to), ADR-0004 (Constitution builder), ADR-0018 (Tool catalog + tools_add at birth time), ADR-0033 (Security Swarm — primary near-term consumer when new threats land mid-deployment), ADR-0043 follow-up #2 (post-birth MCP plugin grants — the precedent this ADR generalizes), ADR-0045 (Posture / Trust-Light — posture is consulted by the grant gate).

## Context

Today an agent's allowed tool list is **frozen at birth** via `BirthRequest.tools_add` (ADR-0018 T2). To add a catalog tool to an existing agent — for example, granting the freshly-forged `translate_to_french.v1` to `operator_companion` after it was forged via the natural-language Skill Forge UI — operators must re-birth the agent. Re-birth means a new `instance_id`, a new `constitution_hash`, lost lineage, lost memory scopes, lost everything that was tied to the prior identity.

This blocks the operator-usable forge UI loop. The Bursts 200-212 arc made it trivial to forge new tools through the daemon at runtime, but the resulting artifacts can only reach NEW agents. Existing operator companions, scheduled-task agents, the live security swarm — none of them can use a forged tool without re-creation. The mismatch is jarring: forge takes 30 seconds, granting access takes a re-birth with all the lineage destruction that implies.

Two existing precedents in the kernel show how to add capabilities to a born agent without violating constitution immutability:

1. **ADR-0043 follow-up #2** (Burst 113) — `agent_plugin_grants` table. Post-birth additions to the MCP plugin allowlist. Dispatcher computes `effective_mcp_servers = constitution.allowed_mcp_servers ∪ active_grants`. Constitution hash unchanged; identity preserved.

2. **ADR-0053** (Per-Tool Plugin Grants) — finer-grained version of the above, per-tool grants within a plugin.

Both stand on the same architectural pattern: **augmentation tables consulted alongside the constitution, not in place of it.** The constitution is immutable; the augmentation is mutable. Grant events land in the audit chain so every capability addition is traceable to a who / when / why.

## Decision

Add `agent_catalog_grants`, mirroring `agent_plugin_grants` shape but keyed on `(instance_id, tool_name, tool_version)` instead of `(instance_id, plugin_name)`. Schema v16 → v17, additive only.

```sql
CREATE TABLE agent_catalog_grants (
    instance_id      TEXT NOT NULL,
    tool_name        TEXT NOT NULL,
    tool_version     TEXT NOT NULL,
    trust_tier       TEXT NOT NULL DEFAULT 'yellow'
                     CHECK (trust_tier IN ('green', 'yellow', 'red')),
    granted_at_seq   INTEGER NOT NULL,
    granted_by       TEXT,
    granted_at       TEXT NOT NULL,
    revoked_at_seq   INTEGER,
    revoked_at       TEXT,
    revoked_by       TEXT,
    reason           TEXT,
    PRIMARY KEY (instance_id, tool_name, tool_version),
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id) ON DELETE CASCADE
);

CREATE INDEX idx_catalog_grants_active
    ON agent_catalog_grants(instance_id)
    WHERE revoked_at_seq IS NULL;
```

### D1 — Dispatcher integration

The crucial decision: **where in the governance pipeline does the grant check fire?**

Today `ConstitutionGateStep` calls `load_resolved_constraints_fn(constitution_path, name, version)` which returns `None` if the tool isn't in the constitution YAML, refusing with `tool_not_in_constitution`.

The new flow:

1. Constitution gate evaluates as before.
2. If `tool_not_in_constitution` would fire, the gate consults `agent_catalog_grants` for an active grant matching `(instance_id, tool_name, tool_version)`.
3. If a grant exists: load default constraints from the tool catalog (the catalog's `requires_human_approval`, `max_calls_per_session`, `audit_every_call`) and continue with those.
4. If no grant: refuse `tool_not_in_constitution` as today.

The grant does NOT inherit the constitution's per-tool overrides (those don't exist — the constitution doesn't list this tool). Defaults come from the catalog. Posture (ADR-0045) still applies on top.

### D2 — Audit emission

Two new `KNOWN_EVENT_TYPES`:

- `agent_tool_granted` — `{instance_id, tool_name, tool_version, trust_tier, granted_by, reason, granted_at_seq}`
- `agent_tool_revoked` — `{instance_id, tool_name, tool_version, revoked_by, reason, revoked_at_seq, granted_at_seq}` (the original grant seq is included so an auditor can trace the full lifecycle from one row).

Plus an annotation on `tool_call_dispatched` when a call flows through a grant rather than a constitution entry: `{..., granted_via: "catalog_grant", grant_seq: <seq>}`. The auditor's question "did this dispatch come from the constitution or from a runtime grant?" must be answerable from the chain alone.

### D3 — Endpoints

```
POST   /agents/{instance_id}/tools/grant           {tool_name, tool_version, trust_tier, reason}
DELETE /agents/{instance_id}/tools/grant/{tool_name}/{tool_version}
GET    /agents/{instance_id}/tools/grants          (list active grants for inspection)
```

Both write endpoints require `X-FSF-Token` + `require_writes_enabled` (matching the existing plugin-grants endpoints). The DELETE is idempotent — revoking an already-revoked grant returns 200 with `{ok: true, no_op: true}`.

### D4 — Posture interaction

The trust_tier on a grant interacts with the agent's posture (ADR-0045):

| Agent posture | green tier grant | yellow tier grant | red tier grant |
|---|---|---|---|
| green | allowed | allowed | allowed |
| yellow | allowed | allowed | requires_human_approval |
| red | requires_human_approval | requires_human_approval | refused |

A red-posture agent CANNOT dispatch a red-tier grant. The interaction matrix mirrors the per-tool grant logic from ADR-0053. Default trust_tier on a new grant is `yellow` unless the operator explicitly specifies otherwise; the operator endpoint must require explicit confirmation to grant `green` (rationale: green is "fully autonomous on this capability" which is the dangerous default).

### D5 — What this is NOT

- Not a constitution mutation. Constitution hash stays stable. The constitution YAML on disk is never rewritten by this code path.
- Not a way to grant tools that don't exist in the catalog. The grant endpoint must validate the `(tool_name, tool_version)` exists in `app.state.tool_catalog`. Hallucinated grants refuse 400.
- Not a way to bypass per-tool runtime constraints. The catalog's default constraints (max_calls_per_session, audit_every_call) still apply.
- Not retroactive. A grant added at seq N applies only to dispatches at seq > N. Earlier audit entries don't change.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Schema + table accessor | v16→v17 migration; `CatalogGrantsTable` mirror of `PluginGrantsTable` | 0.5 burst |
| T2 | Dispatcher integration | Modify `ConstitutionGateStep` to consult grants on constitution miss; load defaults from catalog; tag `granted_via` on dispatch context | 1 burst |
| T3 | Endpoints + audit events | POST + DELETE + GET, two new chain event types, write_lock discipline | 1 burst |
| T4 | Posture interaction | Implement D4 matrix; refuse red-posture/red-tier; require_approval downgrades | 0.5 burst |
| T5 | Tests | Unit tests for the table, dispatcher fixtures for grant-allowed and grant-revoked paths, posture matrix tests | 1 burst |
| T6 | Frontend | Per-agent grants pane on the Agents tab; grant + revoke buttons | 1 burst |

Total estimate: 5 bursts. T1+T2+T3 land the substrate; T4 hardens; T5 ensures the governance change is non-breaking; T6 is operator-facing surface.

## Consequences

**Positive:**

- Closes the natural-language Forge UI loop end-to-end. Operators can forge a new tool and immediately grant it to any existing agent without re-birth.
- Generalizes the proven `agent_plugin_grants` pattern. No new architectural concepts.
- Constitution hash immutability preserved. Lineage, memory scopes, agent identity all untouched.
- Every grant is auditable. The auditor's "who can do what" question is always answerable from the chain.

**Negative:**

- Increases the dispatch path's branching. `ConstitutionGateStep` now has two failure modes (no grant, no constitution entry) where it used to have one. Test surface grows.
- Operator confusion: "which tools can this agent use?" now requires consulting both the constitution AND the grants table. The GET endpoint mitigates this, but operators trained on pre-ADR-0060 mental models may forget grants exist.
- Grant proliferation: without a TTL or expiration, grants accumulate. A future tranche may add `expires_at` for time-bounded grants.

**Risk:**

- The dispatcher change is load-bearing governance code. T2 MUST land with comprehensive tests (T5) or the constitution gate may silently start letting through tools it shouldn't. Recommend reviewing T2's diff with a code-reviewer agent before commit.
- Posture interaction (D4) creates a coupling between two grant types (plugin + catalog) and one orthogonal axis (agent posture). The interaction matrix needs to be in one place (a `GrantPolicy` helper class) so changes to either side stay consistent.

## Status

**Accepted 2026-05-11.** All three open questions resolved as recorded in the acceptance-evidence header. T1 (schema + accessor) lands alongside this acceptance in B219. T2-T6 queued.
