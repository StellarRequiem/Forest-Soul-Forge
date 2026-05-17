# Runbook — D3 Local SOC Phase A (ADR-0078)

**Scope.** Operating ForensicArchivist-D3, the chain-of-custody
guardian for SOC artifacts. End-to-end: birth, skill install,
first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands B346 (`examples/skills/archive_evidence.v1.yaml`).

**Phase context.** This runbook covers **Phase A only**. D3's
advanced rollout under ADR-0078 ships in four phases gated on
three infrastructure ADRs:

| Phase | New agent(s) | Gating ADR |
|---|---|---|
| **A (this runbook)** | forensic_archivist | none (no new infra) |
| B | telemetry_steward + threat_intel_curator | ADR-0064 telemetry pipeline |
| C | detection_engineer | ADR-0065 detection-as-code |
| D | playbook_pilot + purple_pete | ADR-0066 SOAR playbooks |

The four phases share a substrate that B335/B336/B341 hardened
during the D4 rollout, so each subsequent phase should be ~7-9
bursts rather than the 10-burst arc D4 ran.

---

## At a glance

D3 Local SOC's existing Security Swarm (ADR-0033) is a 9-agent
blue team covering baseline monitoring + triage (log_lurker,
anomaly_ace, response_rogue, vault_warden, patch_patrol,
gatekeeper, net_ninja, zero_zero, deception_duke). **Phase A**
adds one role that covers a gap the swarm has today: forensic
chain-of-custody.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `forensic_archivist` | guardian | green | `archive_evidence.v1` | Verifies artifact integrity, attests custody transitions, NEVER mutates artifact bytes. Read-only end-to-end. |

ForensicArchivist-D3 is **operator-birthed via approval queue**
per ADR-0078 — no auto-birth.

**Why a separate role from vault_warden?** vault_warden owns
`forensic_cleanup` — DISPOSAL of artifacts after an incident
closes. forensic_archivist owns `forensic_archive` — PRESERVATION
of artifacts while they're operationally relevant. Different
verbs, different governance.

---

## One-time setup (after pulling B342-B347)

### 1. Restart the daemon

The new role definition lands in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kit
lands in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required before the net-new birth can pick
them up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that
`genre_engine_invariant` reports `status: ok` (no roles
unclaimed) and that `forensic_archivist` appears in `/genres`
under the `guardian` genre's `roles` list.

### 2. Birth the agent

Run the umbrella from Finder (D3 Phase A only has one agent, so
the umbrella is light — but it follows the D4 pattern so Phases
B/C/D can extend it without rewriting the shape):

```
dev-tools/birth-d3-phase-a.command
```

Or, if you'd rather invoke the individual script directly:

```
dev-tools/birth-forensic-archivist.command
```

Both are idempotent — re-runs skip the birth POST when the agent
already exists. The script auto-creates `data/forensics/` at
birth time (the archivist's kit is read-only and can't create
directories on its own).

The script ends by setting the agent's posture to GREEN. Per
ADR-0078 Decision 5, chain-of-custody verification is non-acting
— the gate is the operator's later USE of the artifact, not the
archivist's attestation. (Compare to TestAuthor-D4 which is
YELLOW because every `code_edit` needs review; the archivist has
nothing to gate.)

### 3. Install the skill

The canonical skill manifest lives at:

```
examples/skills/archive_evidence.v1.yaml
```

The daemon loads skills from `data/forge/skills/installed/` (per
ADR-0031). Two install paths:

- **Skill Forge UI** (web frontend): Skills tab → Install from
  Examples → pick `archive_evidence.v1.yaml` → Install. The UI
  runs the manifest parser + writes to the installed dir +
  reloads the catalog.
- **Operator-direct**: `cp examples/skills/archive_evidence.v1.yaml
  data/forge/skills/installed/` then POST `/skills/reload` (or
  restart the daemon).

Verify via `/skills` that `archive_evidence.v1` appears with
status `installed`.

---

## Dispatch flow

The cross-domain orchestrator (ADR-0067) routes operator intents
through D3. The new capability resolves:

| Capability | Routes to | Skill |
|---|---|---|
| `forensic_archive` | ForensicArchivist-D3 | `archive_evidence.v1` |

### Example: acquiring an artifact into custody

Operator says "log the memory dump from incident INC-2026-001
into chain-of-custody." `decompose_intent.v1` produces:

```yaml
- intent: "log the memory dump from incident INC-2026-001 into chain-of-custody"
  domain: d3_local_soc
  capability: forensic_archive
  confidence: 0.91
  status: routable
```

`route_to_domain.v1` looks up `handoffs.yaml`, finds
`d3_local_soc.forensic_archive → archive_evidence.v1`, fires
`delegate.v1` against `ForensicArchivist-D3`. The skill's
five-step pipeline runs:

1. `prior_context` — `memory_recall scope=private query=<artifact_id>`
   walks the per-artifact custody chain. Empty for first-acquire.
2. `verify_artifact_integrity` — `file_integrity snapshot` on the
   artifact_path. Produces the sha256 the attestation will record.
3. `verify_chain_integrity` — `audit_chain_verify` confirms the
   audit chain itself is hash-linked. A broken chain invalidates
   any new attestation.
4. `evaluate_transition` — `llm_think` applies the 7-rule decision
   matrix. For a first-acquire on an unknown artifact_id, the
   verdict is `ATTEST`.
5. `write_attestation` — `memory_write` records the verdict
   (whether `ATTEST` or `HALT`) with tags
   `["chain_of_custody", "<artifact_id>", "transition:acquire",
   "attestor:ForensicArchivist-D3"]`.

Operator reads the `verdict_block` field of the skill output.

### Example: handing off custody

Operator says "hand off INC-2026-001_memdump_apache to the
incident-review team." `decompose_intent.v1` produces a
`forensic_archive` subintent with `transition_type=handoff` +
`handoff_to=incident_review_team` → routes the same way →
the skill runs again.

This time `prior_context` returns the prior acquire attestation;
the 7-rule matrix in step 4 checks:

- Is the chain still intact? (chain_broken)
- Is there a prior acquire entry? (orphan_transition)
- Was a recipient supplied? (missing_handoff_target)
- Does the current artifact hash still match the prior recorded
  hash? (tamper_suspected)
- Does the operator-asserted prior_hash agree with the chain's
  most recent entry? (operator_chain_disagreement)

If any rule fires, the verdict is `HALT` with the specific
`HALT_CODE`. Otherwise `ATTEST`. **The skill records the verdict
regardless** — HALT verdicts are part of the audit trail (the
`forbid_silent_archive` constitutional policy).

### Example: retiring an artifact

Once an incident's evidence is no longer operationally relevant,
operator says "retire INC-2026-001 artifacts." Each artifact
gets a `forensic_archive` subintent with `transition_type=retire`.
The chain captures the retirement reason; the artifact bytes
remain at their path until the operator physically removes them.

**Distinction from vault_warden's `forensic_cleanup`:**
forensic_archivist's retire attestation closes the chain;
vault_warden's `forensic_cleanup` actually deletes / sanitizes
the bytes. The recommended flow is **retire first** (closes the
chain) **then cleanup** (deletes the bytes after the chain
closure is in the audit log).

---

## Observation

### Agent identity

ForensicArchivist-D3's identity surface lives at:

```
soul_generated/ForensicArchivist-D3__forensic_archivist_<short_dna>.soul.md
soul_generated/ForensicArchivist-D3__forensic_archivist_<short_dna>.constitution.yaml
```

The constitution's `tools:` block carries the per-tool constraint
patches from `birth-forensic-archivist.command`:
- `code_read` and `file_integrity` allowed_paths scoped to
  `data/forensics/` + the audit chain
- `audit_chain_verify` no constraints (daemon-routed)
- explicit forbid on `src/`, `config/`, `data/registry.sqlite`,
  `.env`, `~/.fsf/secrets`

### Audit chain

Every dispatch produces a `tool_call_dispatched` + `_succeeded`
or `_rejected` event in the chain. Filter by agent_dna:

```bash
grep '"agent_dna":"<dna>"' examples/audit_chain.jsonl
```

Cross-domain handoffs add `route_to_domain_started` + `_completed`
events.

**No new cascade rule from `forensic_archive`** (deliberate per
ADR-0078 Phase A). The attractive `d3.incident_response →
d3.forensic_archive` cascade is deferred to Phase D when ADR-0066
SOAR playbooks ship a per-incident severity check that decides
WHICH incidents auto-preserve evidence. Until then, every
`forensic_archive` dispatch is operator-triggered.

### Custody log

The chain-of-custody log lives in the archivist's private memory.
Walk per-artifact via:

```bash
curl -s "http://127.0.0.1:7423/agents/<archivist_instance_id>/memory?tag=<artifact_id>" \
  -H "X-FSF-Token: $TOKEN" | jq
```

Or via `memory_recall.v1` from another agent in the archivist's
lineage (none today; Phase B+ may add).

### Approval queue

GREEN-posture forensic_archivist emits decisions freely; the
real gate is the operator's later use of the artifact (deciding
to investigate it, hand it off, retire it). The constitutional
`min_confidence_to_act = 0.75` means the agent itself refuses to
emit a verdict if it's not confident — the verdict block in that
case marks it as `HALT` with `HALT_CODE: insufficient_confidence`.

---

## Recovery — common failure modes

### Birth fails with "unknown role: forensic_archivist"

The daemon hasn't picked up the new role definition. Run
`./dev-tools/force-restart-daemon.command` and retry the birth
script. (Same recovery as D4 advanced rollout — see ADR-0077
runbook §Recovery.)

### Birth fails with "AgentKeyStoreError: backend put failed"

Same root cause as D4 — B335 fixed the keychain colon-rejection.
If you see this on a daemon at HEAD < `ec5d286`, pull and
restart. Confirm via:

```bash
.venv/bin/python -c "from forest_soul_forge.security.secrets.keychain_store import _valid_name; print(_valid_name('forest_agent_key:forensic_archivist'))"
```

Should print `True`.

### Skill dispatch returns "skill not found"

The handoffs.yaml mapping was added in B345 but the skill
manifest didn't ship until B346 (or wasn't installed). Verify:

```bash
curl -s "http://127.0.0.1:7423/skills" -H "X-FSF-Token: $TOKEN" \
  | jq '.skills[] | select(.name == "archive_evidence")'
```

If the result is empty, install the skill (see §3 above).

### Skill returns HALT — tamper_suspected

The artifact's current hash doesn't match the chain's most recent
recorded hash for that artifact_id. Possibilities:

- **Legitimate**: someone (operator or another agent) modified
  the artifact between the prior attestation and now. The HALT
  is the system telling you the chain caught it.
- **Recompression**: tools that "preserve content" (image
  optimizers, log rotators) change the bytes. Treat as legitimate
  tamper from the chain's perspective; re-acquire with a new
  artifact_id rather than overriding.
- **Symlink swap**: someone swapped a symlink at artifact_path
  to point at a different file. The catalog's `file_integrity.v1`
  refuses to follow symlinks (records `symlink:<target>` instead
  of the digest), so this surfaces as a digest format change,
  which the matrix catches as a mismatch.

**Recovery options:**

1. Read the verdict block for the specific prior_hash + current_hash.
2. Investigate via the audit chain (`grep` for the artifact_id
   across recent dispatches — was there a recent
   `tool_call_succeeded` for `file_integrity` or `code_read`
   against that path?).
3. If the divergence is intentional, retire the prior chain
   (transition_type=retire + reason: "superseded by re-acquire
   after <change>") and start a new chain with a new artifact_id.

### Skill returns HALT — chain_broken

`audit_chain_verify` returned non-ok. The audit chain itself is
the problem, not the artifact. Stop attesting until the chain is
repaired. See ADR-0049 (per-event signatures) + ADR-0073 (chain
segmentation) recovery procedures.

### Skill returns HALT — orphan_transition

You asked for `handoff` or `retire` on an artifact_id with no
prior `acquire` entry. Either (a) you mistyped the artifact_id
(check tag spelling), or (b) the acquire entry was never
recorded (genuine bug — file it). Don't paper over by issuing a
fake acquire after the fact; the chain's value IS that the
sequence is real.

### Constitution patch was skipped (warned at birth time)

If the birth script's `[3/4]` step printed
`WARN: could not resolve constitution_path`, the per-tool
constraints didn't land. ForensicArchivist-D3 will run with the
guardian-genre defaults (which include the right tools but
without the path constraints). Two recovery options:

- **Manual patch**: edit
  `soul_generated/ForensicArchivist-D3__*.constitution.yaml`
  directly and add the `constraints` blocks. See
  `birth-forensic-archivist.command` step 3 for the exact
  block contents.
- **Re-birth**: archive the existing agent + birth a new one
  (no archive flow exists yet; manual SQL).

---

## What's NOT in scope (deferred)

- **Auto-archive cascade** — a `d3.incident_response →
  d3.forensic_archive` cascade is attractive but inflates the
  audit chain with attestations operators may never consult.
  Deferred to Phase D (ADR-0066 SOAR playbooks) where a playbook
  step can decide WHICH incidents need auto-archive based on
  severity. `test_forensic_archive_has_no_outbound_cascade` in
  `tests/unit/test_d3_handoffs_wiring.py` pins the decision in
  code.
- **Operator-driven artifact move tooling** — the archivist
  attests; the operator moves bytes. A future `fsf forensics
  acquire` CLI command could provide a gated operator path that
  (a) computes hash, (b) moves the file under `data/forensics/`,
  (c) fires the `forensic_archive` subintent. Not in Phase A.
- **forensics-to-vault handoff workflow** — the retire-then-
  cleanup flow above is documented but not enforced. A future
  burst could wire `forensic_archive.retire` → `forensic_cleanup`
  as an explicit cascade.
- **Multi-host chain-of-custody** — D3 today is single-host. If
  the SOC arc expands to a fleet, the archivist's chain has to
  reconcile across hosts. Not in scope for Phase A.

---

## Reference

- ADR-0078 — D3 Local SOC Advanced Rollout (decision doc)
- ADR-0033 — Security Swarm (the existing 9-agent blue team)
- ADR-0049 — per-event signatures
- ADR-0050 — encryption-at-rest
- ADR-0051 — per-tool subprocess sandbox
- ADR-0067 — Cross-domain orchestrator (the routing rail)
- ADR-0072 — Behavior provenance
- ADR-0073 — Audit chain segmentation
- ADR-0077 — D4 Code Review Advanced Rollout (the template this
  follows)
- `config/domains/d3_local_soc.yaml` — domain manifest
- `config/handoffs.yaml` — cascade rules + skill mappings
- `examples/skills/archive_evidence.v1.yaml` — the Phase A skill
- `dev-tools/birth-d3-phase-a.command` — umbrella birth script
- `dev-tools/birth-forensic-archivist.command` — individual birth
- `tests/unit/test_d3_phase_a_rollout.py` — role + manifest tests
- `tests/unit/test_d3_handoffs_wiring.py` — handoffs + cascade tests
- `tests/unit/test_archive_evidence_skill.py` — skill manifest tests
