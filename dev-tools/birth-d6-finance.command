#!/bin/bash
# ADR-0092 — D6 Personal Finance Guardian umbrella birth script.
#
# Births all five D6 agents in order, idempotent. Run this after
# pulling D6-A through D6-D and restarting the daemon — each
# child script restarts the daemon itself, so explicit restart
# beforehand is optional.
#
# Order matters loosely (each script is independent):
#   1. budget_analyst         — burn-rate analysis (researcher, GREEN)
#   2. risk_advisor           — anti-recommendation arbitration (guardian, GREEN)
#   3. transaction_tracker    — rule-based categorization (researcher, GREEN)
#   4. bill_steward           — recurrence detection + due-date seeds (researcher, GREEN)
#   5. investment_researcher  — side-by-side comparison (researcher, GREEN)
#
# ALL D6 roles are GREEN. D6's manifest hard rule: NEVER
# executes transactions, places orders, sends money, initiates
# transfers. The actuating surface (bank, broker, bill-pay) is
# operator-only by manifest contract; there is no actuator lane
# to define. First all-GREEN domain in the rollout (per
# ADR-0092 Decision 1). D6 closes the rollout per ADR-0067
# (D4→D3→D8→D1→D2→D7→D9→D10→D5→D6).

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "ADR-0092 — Birth D6 Personal Finance Guardian (5 agents)"
echo "=========================================================="
echo

echo "[1/5] BudgetAnalyst-D6 (researcher, GREEN)"
./dev-tools/birth-budget-analyst.command < /dev/null
echo

echo "[2/5] RiskAdvisor-D6 (guardian, GREEN)"
./dev-tools/birth-risk-advisor.command < /dev/null
echo

echo "[3/5] TransactionTracker-D6 (researcher, GREEN)"
./dev-tools/birth-transaction-tracker.command < /dev/null
echo

echo "[4/5] BillSteward-D6 (researcher, GREEN)"
./dev-tools/birth-bill-steward.command < /dev/null
echo

echo "[5/5] InvestmentResearcher-D6 (researcher, GREEN)"
./dev-tools/birth-investment-researcher.command < /dev/null
echo

echo "=========================================================="
echo "D6 Personal Finance Guardian — 5 agents alive."
echo "=========================================================="
echo
echo "Pipeline (operator dispatch → audited finance pass):"
echo "  1. BudgetAnalyst-D6 reads transaction lineage + operator-"
echo "     supplied baselines + composes burn-rate-vs-baseline"
echo "     reports tagged burn_rate_report:<window_slug>. Cross-"
echo "     domain context (D5 power-bill anomaly via cascade,"
echo "     D2 travel block) narrated. NEVER charges anything."
echo "  2. RiskAdvisor-D6 reads candidate-operator-action"
echo "     attestations + composes anti-recommendation alerts"
echo "     tagged risk_alert:<candidate_id>. NEVER blocks the"
echo "     operator — same discipline as reality_anchor."
echo "  3. TransactionTracker-D6 dispatches transaction_categorize.v1"
echo "     over operator-supplied transaction batches + operator-"
echo "     supplied category rules; composes categorization"
echo "     attestations. Read-only; first match wins; uncategorized"
echo "     fallback."
echo "  4. BillSteward-D6 dispatches bill_recurrence_check.v1 over"
echo "     historical bill ledgers; composes recurrence + due-date"
echo "     attestations. The d6→d2 cascade routes them into D2's"
echo "     schedule_reminder. NEVER pays bills."
echo "  5. InvestmentResearcher-D6 dispatches investment_compare.v1"
echo "     over operator-supplied option records; composes side-by-"
echo "     side comparison attestations. NEVER advises — operator"
echo "     decides per manifest's 'info-only, never advice' floor."
echo
echo "Umbrella skill: finance_brain.v1 — single dispatch that"
echo "delegates to TransactionTracker + BillSteward + BudgetAnalyst"
echo "for the observation pass. Risk arbitration + investment"
echo "research are explicit operator dispatches against their"
echo "respective skills (operator-curiosity-driven)."
echo
echo "Cascade rules wired in config/handoffs.yaml:"
echo "  d5_smart_home.energy_optimization -> d6.transaction_monitoring"
echo "    (ACTIVE — energy anomalies feed burn-rate framing;"
echo "     was INERT per ADR-0091 Phase D until D6 shipped)"
echo "  d6.bill_management -> d2_daily_life_os.reminder"
echo "    (ACTIVE — bill-due attestations seed schedule_reminder)"
echo "  d6.tax_season_summary -> d8_compliance.compliance_scan"
echo "    (ACTIVE — tax summaries feed audit packet pipeline)"
echo
echo "Downstream cascades declared INERT in handoffs.yaml comments:"
echo "  d2.reminder -> d6.bill_reminder (superseded by d6→d2 direction)"
echo "  d6.transaction_monitoring -> d1.knowledge_curation"
echo "    (d1 has no 'you asked about X' surface; query-level"
echo "     memory recall is the answer)"
echo "  d6.transaction_monitoring -> d3.anomaly_correlation"
echo "    (recursive with d5→d6 cascade; D3 already sees the"
echo "     energy anomaly upstream)"
echo
echo "Hard rule: D6 NEVER executes transactions. The actuating"
echo "surface (bank, broker, bill-pay) is operator-only per"
echo "manifest contract. D6's deliverable is analysis + drafts;"
echo "the operator decides what to do."
echo
echo "Press any key to close this window."
read -n 1 || true
