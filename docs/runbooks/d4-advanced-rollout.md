# Runbook — D4 Code Review Advanced Rollout (ADR-0077)

**Scope.** Operating the three new D4 agents (test_author,
migration_pilot, release_gatekeeper) end-to-end: birth, dispatch,
observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ `bc9ab43`
(B337 = first skill landed).

---

## At a glance

D4 Code Review's existing triune (system_architect,
software_engineer, code_reviewer) covers the design → implement →
review hot loop. The **advanced rollout** adds three roles that
round out the discipline:

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `test_author` | researcher | yellow | `propose_tests.v1` | Drafts failing tests against a spec BEFORE software_engineer implements |
| `migration_pilot` | guardian | yellow | `safe_migration.v1` | Owns schema/data migrations; dry-runs in scratch SQLite, surfaces FK-cascade + rollback plans, requires operator approval to apply |
| `release_gatekeeper` | guardian | green | `release_check.v1` | Runs conformance suite + drift sentinel + chain integrity + changelog completeness; emits PASS/FAIL/INSUFFICIENT-EVIDENCE verdict |

All three are **operator-birthed via approval queue** per ADR-0077
D4 — no auto-birth.

---

## One-time setup (after pulling B331-B340)

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kits
land in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required for net-new births to pick them up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that `genre_engine_invariant` reports `status: ok` (no roles unclaimed) and that the
new roles appear in `/genres`.

### 2. Birth the three agents

Run the umbrella from Finder:

```
dev-tools/birth-d4-advanced.command
```

This fires the three individual birth scripts in the recommended
order: `test_author` (cheapest, no apply gate) → `release_gatekeeper`
(advisory-only) → `migration_pilot` (most cautious, birth last so
you've already seen the approval queue UI working).

Each individual script is idempotent — re-runs skip the birth
POST when the agent already exists. So is the umbrella.

If you'd rather birth them one at a time with operator review
between each:

```
dev-tools/birth-test-author.command
dev-tools/birth-release-gatekeeper.command
dev-tools/birth-migration-pilot.command
```

### 3. Install the three skills

The canonical skill manifests live in `examples/skills/`:

```
examples/skills/propose_tests.v1.yaml
examples/skills/safe_migration.v1.yaml
examples/skills/release_check.v1.yaml
```

The daemon loads from `data/forge/skills/installed/` (per ADR-0031).
Two install paths:

- **Skill Forge UI** (web frontend): Skills tab → Install from
  Examples → pick each one → Install button. The UI runs the
  manifest parser + writes to the installed dir + reloads the
  catalog.
- **Operator-direct**: `cp examples/skills/*.v1.yaml
  data/forge/skills/installed/` then POST `/skills/reload` (or
  restart the daemon).

Verify via `/skills` that all three appear with status `installed`.

---

## Dispatch flow

The cross-domain orchestrator (ADR-0067) routes operator intents
through D4. Three new capabilities now resolve:

| Capability | Routes to | Skill |
|---|---|---|
| `test_proposal` | TestAuthor-D4 | `propose_tests.v1` |
| `migration_safety` | MigrationPilot-D4 | `safe_migration.v1` |
| `release_gating` | ReleaseGatekeeper-D4 | `release_check.v1` |

### Example: test-first discipline

Operator says "write tests for the new auth module before I
implement it." `decompose_intent.v1` produces:

```yaml
- intent: "write tests for the new auth module"
  domain: d4_code_review
  capability: test_proposal
  confidence: 0.92
  status: routable
```

`route_to_domain.v1` looks up handoffs.yaml, finds
`d4_code_review.test_proposal → propose_tests.v1`, fires
`delegate.v1` against `TestAuthor-D4`. TestAuthor-D4's dispatcher
runs the four-step pipeline:

1. `prior_context` — memory_recall pulls past test patterns.
2. `draft` — llm_think analyzes the spec + emits a pytest module.
3. `write_test` — code_edit writes to `tests/unit/test_auth.py`.
4. `confirm_failure` — pytest runs; non-zero return code is the
   contract (tests fail because impl doesn't exist).

Operator reads `pytest_stdout` to see what assertions
software_engineer needs to satisfy.

### Example: safe migration

Operator says "this PR adds a migration for v24
`personal_index_runs` table." `decompose_intent.v1` produces a
`migration_safety` subintent → routes to MigrationPilot-D4 →
`safe_migration.v1` runs:

1. `prior_context` — past migration patterns.
2. `analyze` — llm_think emits FK-cascade plan + `---ROLLBACK---`
   delimiter + rollback plan.
3. `clone_registry` — `cp data/registry.sqlite
   /tmp/registry.dryrun.<id>.sqlite`.
4. `dry_run` — sqlite3 runs the migration against the scratch DB.
5. `integrity_check` — `PRAGMA integrity_check;`.
6. `recommend` — llm_think synthesizes the result into a verdict
   line: `GO`, `NO-GO`, or `NEEDS-REVIEW`.

Operator reads `recommendation`. **The skill does NOT apply the
migration to production** — that's a separate operator-driven
step (constitutional `require_human_approval_for_apply` policy).

To apply after a `GO` verdict, run:

```bash
sqlite3 data/registry.sqlite < <migration_file.sql>
```

(A future tranche may ship `fsf migrate apply` as the operator's
gated path; until then, manual sqlite3 is the apply.)

### Example: release gating

Operator preparing to tag v0.7.0 says "is this branch ready to
release?" `decompose_intent.v1` produces a `release_gating`
subintent → routes to ReleaseGatekeeper-D4 → `release_check.v1`
runs:

1. `prior_context` — past release decisions.
2. `conformance` — pytest tests/conformance/.
3. `drift_sentinel` — `./dev-tools/check-drift.sh` + changelog
   grep for the release_tag.
4. `chain_verify` — audit_chain_verify.v1 confirms no fork or
   signature gap.
5. `decide` — llm_think synthesizes evidence into PASS / FAIL /
   INSUFFICIENT-EVIDENCE.

Operator reads the decision. **release_gatekeeper does NOT tag
the release** — `forbid_release_action` constitutional policy
blocks tag/push/publish/announce tools at the kit layer (and
git/twine/curl are physically not in its kit, which is the
defense-in-depth). The operator runs `git tag` themselves once
they've reviewed the verdict.

---

## Observation

### Agent identity

Each agent's identity surface lives at:

```
soul_generated/<AgentName>__<role>_<short_dna>.soul.md
soul_generated/<AgentName>__<role>_<short_dna>.constitution.yaml
```

E.g. `soul_generated/TestAuthor-D4__test_author_52b54fee687c.soul.md`.

### Audit chain

Every dispatch produces a `tool_call_dispatched` + `_succeeded`
or `_rejected` event in the chain. Filter by agent_dna:

```bash
grep '"agent_dna":"<dna>"' examples/audit_chain.jsonl
```

Cross-domain handoffs add `route_to_domain_started` + `_completed`
events; the cascade rule from `d4.review_signoff →
d8.compliance_scan` adds `cascade_attempted` events when the d8
domain becomes dispatchable.

### Approval queue

YELLOW-posture agents (test_author + migration_pilot) queue every
non-read-only dispatch in `tool_call_pending_approvals`. Surface
on the frontend Pending tab or via `GET /pending_calls`.
Approve/reject from the UI; each decision lands in the chain as
`tool_call_approved` or `tool_call_rejected_by_operator`.

GREEN-posture release_gatekeeper emits decisions freely; the
real gate is the operator's tag-time review.

---

## Recovery — common failure modes

### Birth fails with "unknown role: <role>"

The daemon hasn't picked up the new role definition. Run
`./dev-tools/force-restart-daemon.command` and retry the birth
script.

### Birth fails with "AgentKeyStoreError: backend put failed"

If you see this on macOS, the keychain backend might be rejecting
the secret name. B335 fixed the colon-rejection root cause; if
you see it on a daemon at HEAD < `ec5d286`, pull and restart.
Confirm via:

```bash
.venv/bin/python -c "from forest_soul_forge.security.secrets.keychain_store import _valid_name; print(_valid_name('forest_agent_key:test'))"
```

Should print `True`.

### Daemon won't boot — ImportError on python-multipart

B335 added python-multipart to pyproject.toml's `[daemon]`
extras. If you're on a venv that pre-dates B335, run
`./dev-tools/fix-multipart-dep.command` to install the package
into the existing venv.

### Skill dispatches but kit only shows `timestamp_window.v1`

B336 added the per-role archetype kits. If your TestAuthor-D4
(or other D4 advanced agent) was born before B336, its
constitution still has the narrow kit. Two options:

- **Leave it** — the existing agent retains its narrow kit
  forever. Acceptable if you only care about future net-new
  births.
- **Re-birth** — the birth path doesn't currently support
  kit-rebuild for an existing agent. Manually edit the
  constitution.yaml at
  `soul_generated/TestAuthor-D4__test_author_*.constitution.yaml`
  to add the missing tools, OR archive the existing agent +
  birth a new one (no archive flow exists yet; manual SQL).

### Migration apply went wrong, want to roll back

Each `safe_migration.v1` run produces a `---ROLLBACK---` block in
the `analyze` step's output. Read the recommendation's
`fk_and_rollback_analysis` field, find the rollback SQL, run:

```bash
sqlite3 data/registry.sqlite < /tmp/rollback-<migration_id>.sql
```

The skill does NOT auto-write the rollback SQL to disk —
operator extracts it from the analysis output. (A future tranche
may persist rollback files alongside scratch DBs.)

### Release verdict was PASS but conformance silently failed

The `decide` step's prompt requires the verdict to be the LAST
LINE. If you see PASS with a noisy stdout, the LLM emitted prose
after the verdict — re-run with `min_confidence_to_act` enforced
(0.80; below that the LLM should output INSUFFICIENT-EVIDENCE
instead).

---

## What's NOT in scope (deferred)

- **Auto-promote learned rules** — the RA cron from B325
  PROMOTES rules to active. ADR-0072 D4 gates birth on operator
  approval; same gate applies here.
- **`fsf migrate apply` operator command** — manual sqlite3
  works today; a future tranche adds the gated CLI command with
  audit-chain emit.
- **Kit rebuild for already-birthed agents** — TestAuthor-D4
  with the pre-B336 narrow kit retains it; no rebuild flow
  exists.
- **D8 compliance-scan downstream cascade** — d8 is currently
  `status: planned`. The cascade rule from `d4.review_signoff →
  d8.compliance_scan` exists in `handoffs.yaml` but every
  attempt returns `cascade_refused: planned_domain`. D8
  rollout (a future arc) flips the cascade live.

---

## Reference

- ADR-0077 — D4 Code Review Advanced Rollout (decision doc)
- ADR-0034 — SW-track triune (the existing D4 substrate)
- ADR-0067 — Cross-domain orchestrator (the routing rail)
- ADR-0072 — Behavior provenance (the cascade rule precedence)
- ADR-0044 — Kernel/SoulUX positioning (release_gatekeeper's
  conformance check rationale)
- `config/domains/d4_code_review.yaml` — domain manifest
- `config/handoffs.yaml` — cascade rules + skill mappings
- `examples/skills/{propose_tests,safe_migration,release_check}.v1.yaml`
  — the three D4 advanced skills
