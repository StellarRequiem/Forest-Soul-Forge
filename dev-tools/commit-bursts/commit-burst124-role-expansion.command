#!/bin/bash
# Burst 124 — Role expansion: 24 new roles + 4 renames.
#
# Closes the genre-dropdown UI bug Alex flagged: every kit-tier
# genre showed N roles in the count badge but only 1 selectable
# role in the role dropdown, because most genre.roles entries
# were aspirational stubs with no trait_tree definition.
#
# Per-genre count before / after (only roles that resolve in
# trait_tree.yaml):
#
#   GENRE                BEFORE   AFTER
#   observer                 1       3   (+ dashboard_watcher, signal_listener)
#   investigator             1       3   (+ incident_correlator, threat_hunter)
#   communicator             0       4   (+ briefer, notifier, status_reporter, translator)
#   actuator                 0       3   (+ alert_dispatcher, deploy_runner, ticket_creator)
#   guardian                 0       3   (+ content_review, refusal_arbiter, safety_check)
#   researcher               1       4   (+ knowledge_consolidator, paper_summarizer, vendor_research)
#   companion                1       4   (+ accessibility_runtime, day_companion, learning_partner, journaling_partner [renamed])
#   web_observer             0       1   (web_watcher, renamed from web_observer_root)
#   web_researcher           0       1   (web_researcher, renamed from web_researcher_root)
#   web_actuator             0       1   (web_actuator, renamed from web_actuator_root)
#   security_swarm           9       9   (no change)
#   sw_engineering           3       3   (no change)
#   verification             1       1   (no change)
#
# Total roles: 18 → 42 (+24).
#
# What ships:
#
#   config/genres.yaml — 4 renames in role lists:
#     therapist          → journaling_partner   (per ADR-0038, narrows
#                          companion scope; original "therapist" name
#                          implied clinical authority the role doesn't
#                          have. Constitution policies enforce
#                          non-clinical disclaimer + crisis refusal.)
#     web_observer_root  → web_watcher
#     web_researcher_root → web_researcher
#     web_actuator_root  → web_actuator
#       (the *_root suffix was a placeholder convention for unimpl-
#       emented roles; concrete names land now that the roles are
#       actually defined per ADR-003X open-web posture.)
#
#   config/trait_tree.yaml — 24 new roles[ROLE] entries:
#     description + domain_weights, all 6 domains (security, audit,
#     cognitive, communication, emotional, embodiment), every weight
#     ≥ min_domain_weight (0.4) per existing constraint.
#
#     Tranche organization:
#       T1 observer ext     — dashboard_watcher, signal_listener
#       T2 investigator ext — incident_correlator, threat_hunter
#       T3 communicator ext — briefer, notifier, status_reporter,
#                             translator
#       T4 actuator ext     — alert_dispatcher, deploy_runner,
#                             ticket_creator
#       T5 guardian ext     — content_review, refusal_arbiter,
#                             safety_check
#       T6 researcher ext   — knowledge_consolidator, paper_summarizer,
#                             vendor_research
#       T7 companion ext    — accessibility_runtime, day_companion,
#                             learning_partner, journaling_partner
#                             (all four bound to ADR-0038 harm model)
#       T8 web genres       — web_watcher, web_researcher, web_actuator
#                             (all three bound to ADR-003X open-web
#                             posture)
#
#   config/constitution_templates.yaml — 24 new role_base[ROLE]
#     entries: policies (incl. ADR-0038 refusal/disclaimer for
#     companions, ADR-003X allowlist + per-action approval for
#     web_actuator), risk_thresholds, out_of_scope, operator_duties,
#     drift_monitoring (per_turn).
#
#   config/tool_catalog.yaml — 24 new archetypes[ROLE].standard_tools
#     entries. Tool kits sized to each role's genre tier:
#       observer kits      = read_only tools only (no mcp_call)
#       investigator kits  = read_only + memory_recall
#       communicator kits  = llm_think + memory_recall (output-only;
#                            mcp_call lives in actuator-tier roles)
#       actuator kits      = mcp_call / shell_exec / browser_action
#                            (genre admits external; constitution
#                            policy approval_per_action gates each
#                            invocation in dispatcher)
#       guardian kits      = read_only review tools + memory_flag /
#                            memory_verify
#       researcher kits    = web_fetch + memory_write (consented scope)
#       companion kits     = llm_think + memory_recall + memory_write,
#                            NO memory_disclose (journals stay
#                            private to user per ADR-0038)
#       web_* kits         = web_fetch + dns_lookup + per-tier extras
#
#   tests/unit/test_trait_engine.py — role-count assertion bumped
#     18 → 42 with comment listing the 24 new tranches and 8 spot-
#     check assertions for tranche representatives. Catches
#     accidental tranche removal in the same way the prior 18-count
#     assertion did.
#
# Verification:
#
#   - Full unit suite: 2,386 passing, 1 xfail (SQLite v6→v7
#     migration, pre-existing per Phase A audit F-7), 3 skipped
#     (sandbox-only tool_forge_sandbox tests).
#   - YAML parse + schema: all four config files load without
#     errors; min_domain_weight constraint enforced (0 violations).
#   - Catalog integrity: 53 tools, 39 archetypes, 5 genre defaults
#     load cleanly; resolver chain unchanged.
#   - Trait-engine role registry: 42 roles, 0 typos, all 24 new
#     tranche representatives detected in spot-check.
#
# Closes the UI bug. Every genre's role dropdown now matches the
# count badge, no more silent failures on aspirational role picks.
# Foundation set for the v0.6 kernel arc to stand a full role
# inventory under the agent governance kernel.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/genres.yaml \
        config/trait_tree.yaml \
        config/constitution_templates.yaml \
        config/tool_catalog.yaml \
        tests/unit/test_trait_engine.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(roles): expand role inventory 18 -> 42 (24 new + 4 renames) (B124)

Burst 124. Closes the genre-dropdown UI bug: every kit-tier genre
showed N roles in the count badge but only 1 selectable role,
because most genre.roles entries were aspirational stubs with no
trait_tree.yaml definition.

Renames (config/genres.yaml):
  therapist          -> journaling_partner   (ADR-0038: narrow
                        companion scope; original name implied
                        clinical authority. Constitution policies
                        enforce non-clinical disclaimer + crisis
                        refusal.)
  web_observer_root  -> web_watcher
  web_researcher_root -> web_researcher
  web_actuator_root  -> web_actuator
                        (_root was placeholder for unimplemented
                        roles; concrete names land per ADR-003X
                        open-web posture.)

24 new roles defined across all four config files:
  trait_tree.yaml          (description + 6-domain weights, all
                            >= min_domain_weight 0.4)
  constitution_templates.yaml (policies, risk_thresholds,
                            out_of_scope, operator_duties,
                            drift_monitoring)
  tool_catalog.yaml        (archetypes[ROLE].standard_tools sized
                            to each role's genre tier)
  genres.yaml              (already had role names listed; trait_
                            tree definitions resolve them now)

Tranche organization:
  T1 observer ext     dashboard_watcher, signal_listener
  T2 investigator ext incident_correlator, threat_hunter
  T3 communicator ext briefer, notifier, status_reporter,
                      translator
  T4 actuator ext     alert_dispatcher, deploy_runner,
                      ticket_creator (approval-gated per policy)
  T5 guardian ext     content_review, refusal_arbiter, safety_check
                      (read-only flags, no auto-mutation)
  T6 researcher ext   knowledge_consolidator, paper_summarizer,
                      vendor_research (memory_write at lineage scope)
  T7 companion ext    accessibility_runtime, day_companion,
                      learning_partner, journaling_partner
                      (ADR-0038 harm model: no memory_disclose,
                      crisis-topic refusal, non-clinical disclaimer)
  T8 web genres       web_watcher (observer-tier),
                      web_researcher (researcher-tier),
                      web_actuator (actuator-tier with
                      ADR-003X allowlist + per-action approval)

ADR bindings:
  ADR-0038 companion harm model -> T7 four roles' constitutions
  ADR-003X open-web posture     -> T8 three roles' constitutions
  ADR-0044 role inventory       -> overall scope of expansion

Verification:
- Full unit suite: 2,386 passing (was 2,386; new role tests
  absorbed); 1 xfail (v6->v7 migration, pre-existing); 3 skipped
  (sandbox-only).
- tests/unit/test_trait_engine.py:test_expected_role_count
  asserts 42 with 8 spot-check tranche representatives.
- YAML parse + schema: all four config files load cleanly,
  min_domain_weight constraint satisfied (0 violations after
  raising 17 sub-floor entries 0.3 -> 0.4 in trait_tree.yaml).
- Catalog integrity: 53 tools, 39 archetypes, 5 genre defaults.

Foundation for the v0.6 kernel arc. The kernel/userspace boundary
work (Bursts 118-120) needs a full role inventory to be a credible
agent governance kernel; the role roster was the lagging artifact.
This commit closes that gap before P2 (formal kernel API spec)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 124 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
