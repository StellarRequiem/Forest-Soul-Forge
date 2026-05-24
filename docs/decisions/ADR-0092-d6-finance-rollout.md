# ADR-0092 — D6 Personal Finance Guardian: rollout

**Status:** Accepted (2026-05-24). All four phases shipped:
Phase A (budget_analyst + risk_advisor), Phase B
(transaction_tracker + bill_steward +
`transaction_categorize.v1` + `bill_recurrence_check.v1`),
Phase C (investment_researcher + `investment_compare.v1`),
Phase D (cascade wiring + umbrella birth + domain manifest
flipped to `live`). **D6 closes the cross-domain rollout per
ADR-0067 — all 10 domains shipped (D4 → D3 → D8 → D1 → D2 →
D7 → D9 → D10 → D5 → D6).**
**Date:** 2026-05-24
**Tracks:** Domain Rollout / Personal Finance Substrate
**Supersedes:** none
**Builds on:** ADR-0067 (cross-domain orchestrator — D6 is the
LAST domain in the rollout order
D4→D3→D8→D1→D2→D7→D9→D10→D5→**D6**), ADR-0068 (operator profile —
currency + tax_jurisdiction + fiscal_year frame burn-rate
modeling + tax-season summaries + risk thresholds), ADR-0050
(encryption-at-rest — transactions are sensitive personal
finance; private/lineage scope plus the encryption floor),
ADR-0076 (vector index for personal context —
`personal_recall.v1` surfaces prior categorization decisions +
prior burn-rate windows), ADR-0085 (D8 compliance — tax-season
summaries cascade into D8's audit packet pipeline so a self-
employed operator's audit-readiness is continuous, not annual),
ADR-0086 (D1 knowledge — "you asked about X 3x" curation is the
librarian's lane; D6 surfaces the signal, D1 curates), ADR-0087
(D2 daily-life — bill due-date reminders cascade into D2's
schedule_reminder), ADR-0091 (D5 smart-home — power-bill
anomalies cascade from D5 energy_warden into D6 transaction
monitoring; D6 is the consumer for the inert d5→d6 cascade
declared at ADR-0091 Phase D close), ADR-0085 / ADR-0086 /
ADR-0087 / ADR-0088 / ADR-0089 / ADR-0090 / ADR-0091 (domain-
rollout precedents — same four-phase / one-commit-per-phase
shape).

## Context

D5 Smart Home Brain closed 2026-05-24 (ADR-0091, all four phases
CLOSED, 5 agents alive). Per ADR-0067's rollout-order plan
(D4→D3→D8→D1→D2→D7→D9→D10→D5→**D6**), **D6 Personal Finance
Guardian** is next — the final domain in the cross-domain
rollout.

D6's value proposition (from `config/domains/d6_finance.yaml`):

> Budgeting + expense categorization + bill stewardship +
> investment research (info-only, never advice). Burn-rate
> modeling against historical patterns flags drift early.
> Anti-recommendation engine: actively flags when YOU'RE about
> to do something out-of-pattern ($400 expense in a $50
> category) — agent reports, operator decides. Receipt OCR via
> plugin (Apple Vision wrapper on macOS). Compliance-grade
> audit trail for self-employed/business operators. Cross-
> currency support driven by operator profile + locale.

The manifest's hard rule (the load-bearing constraint that
shapes every other decision in this ADR):

> Hard rule encoded in action-types policy: never executes
> trades, places orders, sends money, initiates transfers.
> Always operator-only on the actual transaction. This domain
> produces analyses + drafts, never executes.

Five roles per the domain manifest:

| Role | Capability | Posture |
|---|---|---|
| `budget_analyst` | budget_analysis | GREEN (burn-rate composition; non-acting) |
| `transaction_tracker` | transaction_monitoring | GREEN (categorization composition; non-acting) |
| `investment_researcher` | investment_research | GREEN (research composition; info-only) |
| `risk_advisor` | risk_analysis | GREEN (anti-recommendation arbitration; never executes) |
| `bill_steward` | bill_management | GREEN (recurrence detection + draft reminders; never charges) |

## Decision

**Decision 1 — Five roles, no new genres; four researchers + one
guardian. ALL GREEN (zero YELLOW roles in D6).**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `budget_analyst` | researcher | thoroughness + evidence_demand + transparency | read_only (burn-rate reports to private memory) |
| `risk_advisor` | guardian | evidence_demand + double_checking + caution + transparency | read_only (anti-recommendation attestations to private memory) |
| `transaction_tracker` | researcher | thoroughness + evidence_demand + transparency | read_only (categorization attestations to private memory) |
| `bill_steward` | researcher | thoroughness + caution + transparency | read_only (recurrence + due-date attestations; D2 schedule_reminder is the fire-time lane via cascade) |
| `investment_researcher` | researcher | research_thoroughness + lateral_thinking + transparency | read_only (research-brief attestations to private memory) |

The fundamental work of a Personal Finance Guardian decomposes
into:

1. **Budget burn-rate analysis** (`budget_analyst` — researcher;
   reads transaction lineage + composes burn-rate-vs-baseline
   reports; never charges anything);
2. **Anti-recommendation arbitration** (`risk_advisor` —
   guardian; reads candidate-operator-action attestations +
   composes "this is out-of-pattern" alerts; never executes);
3. **Transaction categorization** (`transaction_tracker` —
   researcher; dispatches `transaction_categorize.v1` over
   operator-supplied transaction batches + composes
   categorization attestations);
4. **Bill recurrence stewardship** (`bill_steward` — researcher;
   dispatches `bill_recurrence_check.v1` over historical bill
   ledgers + composes due-date attestations the d6→d2 cascade
   picks up as schedule_reminder seeds);
5. **Investment research** (`investment_researcher` —
   researcher; dispatches `investment_compare.v1` over operator-
   supplied option sets + composes side-by-side comparison
   attestations; info-only, never advice).

**All five roles are GREEN.** This is the deliberate departure
from D5's shape (one YELLOW role). D6's manifest hard rule
forbids any transactional action — never charges, never trades,
never transfers. There is no actuator lane in D6 because the
actuating surface (the bank, the broker, the bill-pay rail) is
operator-only by manifest contract. The `bill_steward`'s
recurrence + due-date attestations cascade into D2's
`schedule_reminder.v1` for fire-time delivery; D6 itself never
queues a "pay the rent" action.

This mirrors D10's all-GREEN shape (ADR-0090) but for a
different reason: D10 is GREEN because research synthesis is
non-acting; D6 is GREEN because the hard rule says so. The
ceiling enforcement is the same — guardian + researcher kits
have `read_only` max_side_effects.

**Decision 2 — Anti-recommendation engine = risk_advisor is a
guardian, not an actuator.**

The manifest names "anti-recommendation engine: actively flags
when YOU'RE about to do something out-of-pattern." This is a
classic guardian surface — a refusal/approval arbiter that
reads a candidate operator action attestation + says "this is
out-of-pattern" or "this is consistent with your history." The
arbiter NEVER blocks the operator; the operator decides. The
guardian's deliverable is the alert + the matched pattern.

Same governance shape as `reality_anchor` (ADR-0063) — a
guardian that says "this claim is/is not consistent with the
ground-truth catalog" + the operator decides what to do. The
risk_advisor is the financial-pattern analog: "this $400 in
the $50 category is/is not consistent with your prior 90-day
window."

**Decision 3 — Three new builtin tools across Phases B–C; ALL
read_only.**

| Phase | Tool | Side-effects | Role consumer |
|---|---|---|---|
| B | `transaction_categorize.v1` | read_only | transaction_tracker |
| B | `bill_recurrence_check.v1` | read_only | bill_steward |
| C | `investment_compare.v1` | read_only | investment_researcher |

All three are read_only — D6's manifest hard rule means no
filesystem queue (D2's `schedule_reminder.v1` is the fire-time
lane via cascade), no external HTTP (investment research is
operator-supplied option sets, not live broker queries). The
contrast with D5's filesystem-class `routine_compose.v1` is
intentional: D5 has one acting role; D6 has none.

`transaction_categorize.v1` runs deterministic rule-based
categorization over a transaction batch with operator-supplied
category rules (`groceries: merchants matching [trader joe's,
whole foods, ...]`); falls back to `uncategorized` when no rule
matches. Same shape as D8's `framework_check.v1` — operator-
supplied rule corpus + deterministic apply.

`bill_recurrence_check.v1` detects recurrence patterns over a
bill ledger (monthly/quarterly/annual with operator-tolerable
day_drift); flags missing-cycle anomalies + projects next-due
dates. Read-only.

`investment_compare.v1` composes a side-by-side comparison
over operator-supplied option records (e.g. three index funds:
expense_ratio + 1y/5y/10y returns + holdings overlap percent)
with normalized fields. NEVER advises which to pick — the
operator picks. Same info-only discipline as D10's
`citation_graph_build.v1`.

**Decision 4 — Cross-domain cascades close three rails left INERT
by earlier phases + add one new D6→D2 rail.**

| Cascade | Status before ADR-0092 | Status after Phase D |
|---|---|---|
| d2.reminder → d6.bill_reminder | INERT (declared ADR-0087 Phase D) | INERT — kept inert; D2 reminder is the fire-time lane, D6 sits upstream as the recurrence detector |
| d5.energy_optimization → d6.transaction_monitoring | INERT (declared ADR-0091 Phase D) | ACTIVE — energy anomaly attestations from D5 cascade into D6 transaction monitoring so a power-bill spike enters the operator's burn-rate window |
| d6.bill_management → d2.reminder | (new) | ACTIVE — every D6 bill-due attestation cascades into D2's schedule_reminder so the operator gets a heads-up at the due date |
| d6.tax_season_summary → d8.compliance_scan | (new) | ACTIVE — tax-season summaries cascade into D8's compliance scanner so self-employed audit-readiness is continuous |

The d2.reminder → d6.bill_reminder rail stays INERT
deliberately: D6's bill_steward emits the recurrence detection
+ due-date attestation; D2's TimeSteward emits the actual
reminder at fire time. There is no D6-side "bill_reminder"
capability to route to — the cascade direction is D6→D2, not
D2→D6.

**Decision 5 — Capability aliases. Six entry capabilities, two
extra handoff alias rows in `handoffs.yaml`.**

The manifest names eight capabilities (`budget_analysis`,
`transaction_monitoring`, `investment_research`,
`risk_analysis`, `bill_management`, `burn_rate_forecast`,
`receipt_ocr`, `tax_season_summary`). Each entry capability
resolves to one of the five primary skills; `burn_rate_forecast`
aliases to `budget_analysis`, `tax_season_summary` aliases to
`budget_analysis`, and `receipt_ocr` aliases to
`transaction_monitoring` (receipt OCR is a connector concern,
not a D6 builtin — the forest-finance plugin ingests receipts
and writes them as transactions that transaction_tracker reads
the same way it reads CSV-imported transactions). The umbrella
`finance_brain.v1` skill composes all five primary skills in
one dispatch.

## Phase plan

### Phase A — budget + risk foundation (SHIPPED 2026-05-24, commit e8d5604)

- Add `budget_analyst` (researcher, GREEN) + `risk_advisor`
  (guardian, GREEN) to `trait_tree.yaml`, `genres.yaml`,
  `constitution_templates.yaml`, `tool_catalog.yaml`.
- No new builtin tools — both roles reuse existing kit (LLM-
  driven composition over recall + write; same as D5 Phase A).
- Skill manifests: `budget_analysis.v1`, `risk_analysis.v1`.
- Birth scripts: `dev-tools/birth-budget-analyst.command`,
  `dev-tools/birth-risk-advisor.command`.
- Runbook: `docs/runbooks/d6-finance-ops.md`.

### Phase B — transaction + bill stewardship (SHIPPED 2026-05-24, commit 879e4e2)

- Add `transaction_tracker` (researcher, GREEN) + `bill_steward`
  (researcher, GREEN) to trait_tree / genres /
  constitution_templates / tool_catalog.
- Two new builtin tools:
  - `transaction_categorize.v1` — deterministic rule-based
    categorization over operator-supplied transaction batch +
    operator-supplied category rules. read_only.
  - `bill_recurrence_check.v1` — deterministic recurrence
    detection (monthly/quarterly/annual with tolerable
    day_drift) over historical bill ledger; projects next-due
    dates + flags missing-cycle anomalies. read_only.
- Skill manifests: `transaction_monitoring.v1`,
  `bill_management.v1`.
- Birth scripts: `dev-tools/birth-transaction-tracker.command`,
  `dev-tools/birth-bill-steward.command`.

### Phase C — investment research (SHIPPED 2026-05-24, commit 3522ca9)

- Add `investment_researcher` (researcher, GREEN).
- One new builtin tool:
  - `investment_compare.v1` — deterministic side-by-side
    comparison over operator-supplied option records; normalizes
    fields + flags missing-data gaps. read_only. NEVER advises.
- Skill manifest: `investment_research.v1`.
- Birth script: `dev-tools/birth-investment-researcher.command`.

### Phase D — cascade + umbrella + domain live (SHIPPED 2026-05-24)

- No new roles or builtin tools.
- Skill manifest: `finance_brain.v1` (umbrella composition).
- Cascade wiring in `handoffs.yaml`:
  - ACTIVATE: d5.energy_optimization → d6.transaction_monitoring
    (power-bill anomaly seeds burn-rate window — was INERT per
    ADR-0091 Phase D), d6.bill_management → d2.reminder (bill-
    due attestation seeds operator reminder), d6.tax_season_summary
    → d8.compliance_scan (tax summaries cascade into audit
    packet pipeline).
  - Keep INERT: d2.reminder → d6.bill_reminder (direction is
    D6→D2, not D2→D6 — no D6-side bill_reminder capability),
    d5.routine_management → d1.routines_index (D1-side
    capability never defined; routine queries answered via
    memory_recall).
- Umbrella: `dev-tools/birth-d6-finance.command`.
- Flip `d6_finance.yaml` status to `live`.
- Flip this ADR to Accepted.

## Consequences

**Zero YELLOW roles in D6.** This is the first all-GREEN domain
in the rollout. The manifest hard rule (never executes
transactions) means there is no actuator lane to define. The
ceiling enforcement at genre level (researcher / guardian both
have read_only ceiling) is the substrate confirmation of the
manifest contract.

**Cross-domain cascade closes the rollout.** D6 is the LAST
domain; its Phase D activates three new cross-domain rails
(d5→d6, d6→d2, d6→d8) and the cross-domain orchestrator now
sees a complete graph for the operator's day-to-day finance
work. The d5→d6 rail closes the inert cascade ADR-0091 Phase D
declared; the d6→d2 + d6→d8 rails open new ones.

**Anti-recommendation as guardian, not actuator.** The
risk_advisor role is the surface where the operator gets a
second opinion on a candidate action ("you're about to pay
$400 in groceries this week; your 90-day window is $180"). The
advisor never blocks the operator — same discipline as
reality_anchor. This is the explicit refusal of the "advisor
that decides for you" anti-pattern.

**Substrate-ready without forest-finance connector.** D6 reads
transaction batches + bill ledgers + investment option records
from operator-supplied memory_writes (one-shot attestations OR
forest-finance connector ingestion). The roles dispatch and
attest without the connector installed; when forest-finance
ships, it writes the same attestation shapes the operator does
manually today. Same substrate-ready pattern as D5 vs forest-
home-assistant.
