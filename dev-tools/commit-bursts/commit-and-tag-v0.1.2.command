#!/usr/bin/env bash
# Commit CHANGELOG + STATE refresh, then cut + push v0.1.2 annotated tag.
#
# v0.1.2 captures the SarahR1 absorption arc — 10 commits across two
# days, three Proposed ADRs promoted to Accepted, +133 net tests.
# Implementation work already on origin; this script does the
# release-paperwork pass (CHANGELOG entry + STATE refresh + tag).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== v0.1.2 — SarahR1 absorption release ==="
echo
clean_locks
echo "step 1/5 — staging release paperwork..."
git add CHANGELOG.md STATE.md commit-and-tag-v0.1.2.command
clean_locks
git status --short
echo

echo "step 2/5 — commit..."
clean_locks
git commit -m "v0.1.2: SarahR1 absorption release

CHANGELOG entry + STATE.md refresh. The implementation work that
warrants this version bump is already on origin (commits 889e362
through ddf0326 — 10 commits). This commit captures the release-
paperwork pass.

Test suite: 1434 (v0.1.1 baseline) → 1567 (+133, +9%).
Three Proposed ADRs from external reviewer SarahR1 (Irisviel)
absorbed across the arc and promoted to Accepted on landing:

- ADR-0027-amendment — epistemic memory metadata (T1+T2+T3+T4):
  schema v10→v11 with claim_type / confidence / last_challenged_at
  columns + memory_contradictions table (commit fcd8d2c);
  memory_recall.v1 always-on epistemic surfaces +
  surface_contradictions / staleness_threshold_days opt-in
  parameters + K1 verification fold (commit 24ec62b);
  memory_challenge.v1 operator-driven scrutiny tool (commit
  fdef95b). Closes ADR-0038 H-6 at the data layer.

- ADR-0021-amendment — initiative ladder (T1+T2+T3): per-genre
  max_initiative_level + default_initiative_level on all 13
  genres (commit 03b3d60); constitution.yaml derived
  initiative_level + initiative_ceiling fields, hashed (commit
  823e69c); InitiativeFloorStep dispatcher pipeline step
  (opt-in per tool — per-tool annotation queued for v0.3,
  commit 4e9b8cf).

- ADR-0038 — companion harm model (T1+T2+T3): genre min_trait_floors
  with Companion's evidence_demand >= 50 / transparency >= 60
  H-1 mitigation (commit 03b3d60); voice_safety_filter.py with
  9 sentience-claim pattern denylist + post-render fallback
  (commit fb75c6f); operator_companion constitutional template
  gains forbid_sentience_claims (H-2) +
  forbid_self_modification_claims (H-8) + external_support_redirect
  (H-3) policies + claim_romantic_relationship +
  assume_intimacy_beyond_configured_role out-of-scope (H-4) +
  burnout-awareness operator-duty (H-7), commit ddf0326.

Files NOT changed: audit chain format, DNA derivation,
constitution-hash invariants (field additions, not invariant
changes), schema rebuild path, single-writer SQLite discipline,
memory_verify.v1 K1 API, the seven original genres' identities.

Deferred to v0.3:
  - ADR-0027-am T7 (memory_reclassify.v1 bulk operator tool)
  - ADR-0021-am per-tool annotation audit (opt-in → enforcement)
  - ADR-0038 T4-T7 (telemetry + per-call gates + dashboard)
  - ADR-0035 / ADR-0036 / ADR-0037 (Persona Forge / Verifier Loop /
    Observability — three v0.3 candidates queued from the original
    SarahR1 review)

External catalyst: github.com/SarahR1 (Irisviel). See CREDITS.md
for the full attribution + adopted/declined ledger. Saved
response of record at docs/audits/2026-05-01-sarahr1-review-response.md."

clean_locks
echo "  commit landed"
echo

echo "step 3/5 — push commit..."
clean_locks
git push origin main
clean_locks
echo "  pushed to origin"
echo

echo "step 4/5 — annotated tag v0.1.2..."
clean_locks
if git tag -l v0.1.2 | grep -q v0.1.2; then
  echo "  tag v0.1.2 already exists locally — skipping create"
else
  git tag -a v0.1.2 -m "SarahR1 absorption release

Three Proposed ADRs from external reviewer SarahR1 (Irisviel)'s
2026-04-30 comparative review of FSF vs. her Nexus / Irkalla
project absorbed across 10 commits and promoted to Accepted:

- ADR-0027-amendment — epistemic memory metadata (T1-T4)
- ADR-0021-amendment — initiative ladder (T1-T3)
- ADR-0038 — companion harm model (T1-T3)

Test suite: 1434 (v0.1.1) → 1567 (+133, +9%). Zero regressions.

See CHANGELOG.md [0.1.2] entry + CREDITS.md for the full ledger
+ attribution. Saved response at
docs/audits/2026-05-01-sarahr1-review-response.md."
  echo "  ✓ tag v0.1.2 created locally"
fi

echo
echo "step 5/5 — push tag..."
clean_locks
git push origin v0.1.2
clean_locks

echo
git log -1 --oneline
echo
echo "✓ v0.1.2 released. Verify at:"
echo "  https://github.com/StellarRequiem/Forest-Soul-Forge/releases"
echo ""
read -rp "Press Enter to close..."
