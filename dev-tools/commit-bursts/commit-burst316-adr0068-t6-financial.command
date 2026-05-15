#!/bin/bash
# Burst 316 - ADR-0068 T6: financial + jurisdiction.
#
# Singleton FinancialContext sub-record on OperatorProfile.
# Currency + tax_residence + fiscal_year_start (required) +
# preferred_tooling (optional list). Reality Anchor seeds emit
# currency + tax_residence at HIGH severity so a Finance
# Guardian agent recommending in the wrong currency or
# jurisdiction gets caught at dispatch time.
#
# What ships:
#
# 1. src/forest_soul_forge/core/operator_profile.py:
#    - FinancialContext frozen dataclass (currency, tax_residence,
#      fiscal_year_start required; preferred_tooling tuple
#      defaults to ()).
#    - OperatorProfile gains `financial: Optional[FinancialContext]
#      = None`. Backward-compat: pre-T6 yamls have no financial
#      field and stay readable.
#    - Three new regex constants:
#        _CURRENCY_RE       — ISO 4217 three-letter uppercase
#        _TAX_RESIDENCE_RE  — ISO 3166-1 alpha-2 (optional sub)
#        _MMDD_RE           — MM-DD (validates both month + day)
#    - _parse_financial helper validates dict shape + each field
#      via the regex constants + preferred_tooling list shape +
#      per-entry string requirement. Returns None when absent.
#    - _financial_to_dict serializer omits preferred_tooling when
#      empty for diff-stable YAML.
#    - _to_yaml emits financial only when present.
#    - save_operator_profile forwards financial through the
#      updated_at refresh.
#    - profile_to_ground_truth_seeds emits three new seeds when
#      financial is set:
#        operator_currency (HIGH) — canonical_terms=[currency]
#        operator_tax_residence (HIGH) — canonical_terms=[code]
#        operator_fiscal_year (MEDIUM) — canonical_terms=[MM-DD]
#      Severity rationale: currency + jurisdiction are recommendation-
#      critical (wrong currency in transaction guidance is a real
#      harm). Fiscal year is calendar-context (deadline math),
#      lower stakes.
#
# 2. tests/unit/test_operator_profile_financial.py - 16 cases:
#    Dataclass surface (3):
#      - required-only construction
#      - with tooling
#      - profile defaults financial to None
#    Round-trip + YAML shape (3):
#      - round-trip preserves all fields including tooling tuple
#      - YAML omits financial when None
#      - YAML omits preferred_tooling when empty
#    Loader refusals (8 parametrized + 1):
#      - non-dict financial
#      - missing each of 3 required fields (parametrized)
#      - 6 bad-currency cases (lowercase, wrong length, non-letter)
#      - 4 bad-tax_residence cases
#      - 6 bad-fiscal_year_start cases (bad month, bad day, zero,
#        not-zero-padded, prose)
#      - non-list preferred_tooling
#      - non-string tooling entry
#    Reality Anchor seeds (4):
#      - 3 seeds emit when financial present
#      - severity levels (HIGH for currency + tax_residence,
#        MEDIUM for fiscal_year)
#      - canonical_terms include the values
#      - no seeds emit when financial absent
#
# Sandbox-verified all 10 end-to-end scenarios.
#
# ADR-0068 progress: 7/8 (T1-T6). T7-T8 queued: consent wizard
# (first-boot multi-step UI), migration substrate (schema-version-
# aware loader + v1->v2 helpers — gets the schema_version bump
# treatment as T4-T6 fields accumulated).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/operator_profile.py \
        tests/unit/test_operator_profile_financial.py \
        dev-tools/commit-bursts/commit-burst316-adr0068-t6-financial.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T6 - financial + jurisdiction (B316)

Burst 316. Singleton FinancialContext sub-record on the operator
profile. Currency + tax_residence + fiscal_year_start required;
preferred_tooling tuple optional. Reality Anchor seeds emit
currency + tax_residence at HIGH severity (wrong-currency
recommendations are real harm) and fiscal_year at MEDIUM
(deadline-math context).

What ships:

  - core/operator_profile.py: FinancialContext dataclass +
    OperatorProfile.financial = None default + _CURRENCY_RE +
    _TAX_RESIDENCE_RE + _MMDD_RE validation regexes +
    _parse_financial helper with shape + per-field validation +
    _financial_to_dict serializer (omits empty preferred_tooling)
    + _to_yaml emits financial only when present +
    save_operator_profile forwards the field through updated_at
    refresh + profile_to_ground_truth_seeds emits three new
    seeds with severity-stratified canonical_terms.

Tests: test_operator_profile_financial.py - 16 cases covering
dataclass surface (3), round-trip + YAML omit-when-empty (3),
parametrized loader refusals (non-dict, 3 missing-required, 6
bad-currency, 4 bad-tax_residence, 6 bad-fiscal_year, non-list
+ non-string tooling), and RA seed coverage (emit-when-present,
severity levels, canonical_terms, absent-when-not-set).

Sandbox-verified all 10 functional scenarios.

ADR-0068 progress: 7/8 (T1-T6). T7-T8 queued: consent wizard
(first-boot multi-step UI), migration substrate (schema-version-
aware loader + v1 to v2 helpers — the accumulated T4-T6 field
additions are still v1 because each was additive-with-default,
but T8 introduces explicit version-aware migration plumbing
ahead of any breaking change)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 316 complete - ADR-0068 T6 financial shipped ==="
echo ""
echo "Press any key to close."
read -n 1
