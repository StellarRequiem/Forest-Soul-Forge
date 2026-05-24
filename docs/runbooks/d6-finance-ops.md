# Runbook — D6 Personal Finance Guardian (ADR-0092)

**Scope.** Operating the D6 Personal Finance Guardian domain
end-to-end: birth, skill install, first dispatch, observation,
recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D6 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D6 ships in four phases per ADR-0092:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | budget_analyst + risk_advisor | none — reuses existing | SHIPPED |
| **B** | transaction_tracker + bill_steward | transaction_categorize.v1 + bill_recurrence_check.v1 | SHIPPED |
| **C** | investment_researcher | investment_compare.v1 | SHIPPED |
| **D** | (cascade + umbrella + live) | none | pending |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D6's value proposition: **personal finance analysis with anti-
recommendation arbitration, never execution**. The guardian
tracks burn rate, categorizes transactions, stewards bills,
researches investments, and flags out-of-pattern actions —
ALL READ-ONLY. The actuating surface (bank, broker, bill-pay)
is operator-only by manifest contract.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `budget_analyst` | researcher | green | `budget_analysis.v1` | Reads transaction lineage + baseline; composes burn-rate-vs-baseline report attestation. NEVER charges; NEVER categorizes individual transactions; NEVER advises on investments. |
| `risk_advisor` | guardian | green | `risk_analysis.v1` | Reads candidate-operator-action attestations; composes anti-recommendation alert attestations. NEVER blocks operator; NEVER executes transactions; NEVER advises on instruments. |

Both Phase A agents are **operator-birthed via the approval
queue** per ADR-0092 — no auto-birth.

**Why separate analyst + advisor?** Burn-rate analysis and
anti-recommendation arbitration are different governance
surfaces. The analyst composes the burn-rate narrative across
all transactions in a window; the advisor watches for
individual candidate actions that fall out of the operator's
pattern. The two operate on different scopes (window-wide vs.
per-candidate) and produce different attestation shapes
(burn_rate_report vs. risk_alert). Combining them would
conflate window-level synthesis with per-candidate arbitration.

**D6's hard rule — NO transaction execution.** Per the domain
manifest: D6 never executes trades, places orders, sends money,
or initiates transfers. Every role's constitution carries
`forbid_transaction_execution`. The actuating surface is
operator-only. D6's deliverable is the analysis + the alert;
the operator decides what to do.

**Anti-recommendation, not refusal.** The risk_advisor is a
guardian, but it doesn't BLOCK the operator. It surfaces
"this is out-of-pattern" evidence; the operator decides
whether to proceed. Same discipline as reality_anchor
(ADR-0063) — the arbiter surfaces evidence; the operator
overrides if they want to. The constitution's
`forbid_operator_blocking` policy makes the discipline
explicit.

**forest-finance not required for Phase A.** D6 reads
`transaction_categorized` + `candidate_action` +
`burn_rate_baseline` memory attestations. These can be
operator-supplied (one-shot `memory_write` recordings) OR
connector-supplied (the forest-finance plugin, when installed,
ingests bank CSVs + receipt OCR into memory). Phase A ships
substrate-only; the operator chooses ingestion strategy.

**Pacific time everywhere.** Per CLAUDE.md, all D6 timestamps
are Pacific time. The skill manifests explicitly tell the LLM
to use Pacific time + operator-currency formatting.

---

## Phase A — birth + first dispatch

### Birth

```bash
./dev-tools/birth-budget-analyst.command
./dev-tools/birth-risk-advisor.command
```

Each script:
1. Kickstarts the daemon (loads the new role).
2. Checks for an existing agent (by name).
3. POSTs `/birth` with the role + agent_name; the constitution
   templates + tool catalog kits are resolved at birth time.
4. Sets posture to GREEN.

Birth payload uses an idempotency key per agent
(`birth-budget-analyst-d6`, `birth-risk-advisor-d6`) — re-running
the script is safe; the second run finds the existing agent and
skips birth.

### First dispatch — analyst

Compose a burn-rate report for a fiscal-month window. The
prerequisite is at least one operator-supplied
`burn_rate_baseline` memory attestation + at least one
`transaction_categorized` attestation within the
`window_minutes` (default 30 days).

```bash
# Seed a quick test baseline (operator-supplied)
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/agents/${BUDGET_ANALYST_ID}/memory" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "content": "baseline: groceries=$200/wk, dining=$80/wk, transport=$50/wk (90-day median ending 2026-04-30 Pacific, currency=USD)",
    "tags": ["burn_rate_baseline"],
    "scope": "lineage"
  }'

# Seed a sample categorized transaction
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/agents/${BUDGET_ANALYST_ID}/memory" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "content": "transaction: 2026-05-23 trader_joes 64.32 USD category=groceries",
    "tags": ["transaction_categorized"],
    "scope": "lineage"
  }'

# Dispatch budget_analysis.v1
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${BUDGET_ANALYST_ID}/skills/dispatch" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "skill_name": "budget_analysis",
    "skill_version": "1",
    "session_id": "burn-rate-fiscal-month-2026-05",
    "inputs": {
      "window_slug": "fiscal-month-2026-05",
      "window_minutes": 43200,
      "operator_reason": "monthly fiscal-month review"
    }
  }'
```

### First dispatch — advisor

Compose an anti-recommendation alert over a candidate action.
The prerequisite is one operator-supplied `candidate_action`
attestation and a 90-day baseline window readable via
`memory_recall` (the budget_analyst's prior reports OR
`transaction_categorized` attestations).

```bash
# Seed a candidate action (operator-supplied — "I'm about to spend $400 at TJ")
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/agents/${RISK_ADVISOR_ID}/memory" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "content": "candidate: 2026-05-23 trader_joes ~400 USD category=groceries (intended pre-trip stock-up)",
    "tags": ["candidate_action:groceries-2026-05-23-tj-400usd"],
    "scope": "lineage"
  }'

# Dispatch risk_analysis.v1
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${RISK_ADVISOR_ID}/skills/dispatch" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "skill_name": "risk_analysis",
    "skill_version": "1",
    "session_id": "risk-tj-400-2026-05-23",
    "inputs": {
      "candidate_id": "groceries-2026-05-23-tj-400usd",
      "baseline_window_days": 90,
      "operator_reason": "pre-trip stock-up — want a second opinion before committing"
    }
  }'
```

The advisor's alert lands in private memory tagged
`risk_alert:groceries-2026-05-23-tj-400usd`. Read it via
`memory_recall`; **the operator decides** whether the candidate
proceeds. The advisor does NOT block.

---

## Recovery

**Chain integrity halt.** Both skills refuse to compose if
`audit_chain_verify.v1` reports `status != "ok"`. Run
`./dev-tools/check-drift.sh` to confirm chain integrity; if a
real divergence exists, do not bypass — investigate per
`CLAUDE.md` §0 Hippocratic gate.

**Posture-drift halt.** If either agent's
`drift_monitoring.on_drift = halt` fires, the daemon refuses
to dispatch the skill. Re-run the birth script with the same
idempotency key to re-seal the profile hash; verify in
`/agents/{id}/passport`.

**Reality Anchor refusal.** risk_analysis includes a
`verify_claim` step on the candidate-action description. A
reality-anchor refusal blocks the alert. Refresh the
operator-supplied candidate_action attestation and re-dispatch.

---

## Beyond Phase A

Phases B–D will append sections to this runbook as they ship.
For the current state of D6 (which phases have closed, which
roles are alive, which tools are registered), check the table
at the top + `STATE.md`'s D6 row.
