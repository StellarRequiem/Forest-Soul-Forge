#!/bin/bash
# Burst 187 — ADR-0056 E1 — birth Smith (Experimenter agent).
#
# Adds the `experimenter` role across the four config files
# that compose an agent's birth profile + ships the
# birth-smith.command script that drives the actual /birth
# call + post-birth provisioning.
#
# Per ADR-0056 D1 (kit composition): broad legal kit minus
# identity-touching tools. Branch + path constraints land
# post-birth via the constitution YAML patch in
# birth-smith.command's phase 3 — tool_catalog.yaml configures
# kit MEMBERSHIP only; constitution patches configure per-tool
# CONSTRAINTS.
#
# What ships:
#
#   config/trait_tree.yaml:
#     - NEW role `experimenter` with domain weights skewing
#       higher than software_engineer on cognitive (recursive
#       improvement requires deeper planning), audit (Smith's
#       work is under heavy operator review), security (Smith
#       touches more of the system; needs blast-radius
#       reasoning).
#
#   config/genres.yaml:
#     - `experimenter` claimed by the actuator genre
#       (max_side_effects=external, default_initiative_level=L5).
#
#   config/constitution_templates.yaml:
#     - NEW `experimenter` template under role_base. Mirrors
#       software_engineer's actuator-tier policy posture but
#       tightens further: forbid_main_push,
#       forbid_identity_writes, approval_for_tool_creation
#       (ADR-0056 D6 self-augmentation gate). Slightly more
#       permissive min_confidence_to_act (0.55 vs 0.60) because
#       explore-mode hypothesis-floating benefits from lower
#       confidence gates; operator review is the backstop.
#
#   config/tool_catalog.yaml:
#     - NEW `experimenter` archetype with 36 standard_tools
#       covering: cognition + memory (8), code + repo +
#       analysis (13), system inspection read-only (8),
#       network + external gated (4), delegation (3). Excludes
#       any tool that would mutate identity surfaces (audit
#       chain, registry DB, secrets dir).
#
#   dev-tools/birth-smith.command (NEW):
#     - 5-phase script: restart daemon → POST /birth →
#       constitution-patch with branch-isolation per-tool
#       constraints → set posture YELLOW → provision
#       ~/.fsf/experimenter-workspace/Forest-Soul-Forge clone
#       at the current HEAD with the experimenter/cycle-1
#       branch pre-created. Idempotent.
#
# Per ADR-0044 D3: additive role definition. Pre-B187 daemons
# reading post-B187 .env files just don't see the new role
# (the dispatcher's role-to-genre + role-to-archetype lookups
# return no match for unknown roles). Post-B187 daemons reading
# pre-B187 constitutions behave identically.
#
# Per ADR-0001 D2: Smith births with ONE constitution_hash
# bound to its DNA. Future cycles' tool additions go through
# the existing tools_add path (per-instance state mutation,
# not identity mutation). Identity invariance preserved.
#
# Per ADR-0008: Smith's default_provider preference for frontier
# is documented in ADR-0056 D4 but not yet enforced at the
# dispatch layer — that wiring is part of E2 alongside
# ModeKitClampStep. For now, llm_think dispatches against
# Smith default to local; operator can route to frontier
# explicitly per-dispatch via task_caps.
#
# Verification:
#   PYTHONPATH=src:. python3 -c "
#     import yaml
#     for path, key in [
#         ('config/trait_tree.yaml', 'roles'),
#         ('config/genres.yaml', 'genres'),
#         ('config/constitution_templates.yaml', 'role_base'),
#         ('config/tool_catalog.yaml', 'archetypes'),
#     ]:
#         data = yaml.safe_load(open(path))
#         block = data.get(key, {})
#         present = 'experimenter' in block or any('experimenter' in (v.get('roles', []) if isinstance(v, dict) else []) for v in block.values())
#         print(f'{path}: experimenter present = {present}')"
#   -> all four show True
#
# Plus build_app() imports clean.
#
# Operator-facing follow-up (NOT in this commit):
#   1. Run dev-tools/birth-smith.command to actually birth Smith
#      + provision the workspace.
#   2. Verify Smith via the Agents tab (chat dashboard) — should
#      show up as Smith with role=experimenter, genre=actuator,
#      posture=yellow.
#   3. Cycle 1 work-mode dispatches must wait until E2 ships
#      ModeKitClampStep — without it, every dispatch is mode=none
#      and bypasses the per-mode kit clamp. Smith CAN run today
#      under YELLOW posture but every action queues for approval.
#
# Next burst: B188 — E2 (ModeKitClampStep + task_caps.mode +
# per-mode kit definitions + tests).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        config/tool_catalog.yaml \
        dev-tools/birth-smith.command \
        dev-tools/commit-bursts/commit-burst187-adr0056-e1-birth-smith.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E1 — experimenter role + birth-smith script (B187)

Burst 187. First implementation tranche of ADR-0056. Adds the
experimenter role across the four config files that compose an
agent's birth profile + ships birth-smith.command for the
operator to actually birth Smith with proper post-birth
provisioning.

Per ADR-0056 D1: broad legal kit minus identity-touching
surfaces. Branch + path constraints land post-birth via the
constitution YAML patch — tool_catalog configures membership,
constitution configures per-tool constraints.

Ships:

trait_tree.yaml: NEW experimenter role with domain weights
skewing higher than software_engineer on cognitive, audit,
security (recursive-improvement requires deeper planning + is
under heavy operator review + needs blast-radius reasoning).

genres.yaml: experimenter claimed by actuator genre
(max_side_effects=external, default_initiative_level=L5).

constitution_templates.yaml: NEW experimenter template
mirroring software_engineer's actuator-tier policy posture but
tighter — forbid_main_push, forbid_identity_writes,
approval_for_tool_creation. Slightly more permissive
min_confidence_to_act (0.55) because explore-mode hypothesis-
floating benefits from lower confidence gates; operator review
is the backstop.

tool_catalog.yaml: experimenter archetype with 36 standard_tools
across cognition+memory, code+repo+analysis, system inspection
read-only, network+external gated, delegation. Excludes any
tool that would mutate identity surfaces.

dev-tools/birth-smith.command (NEW): 5-phase operator script.
Restart daemon -> POST /birth with role=experimenter,
agent_name=Smith -> constitution patch with shell_exec +
code_edit + web_fetch per-tool constraints (branch isolation,
allowed paths, allowed hosts) -> set posture YELLOW -> clone
the repo to ~/.fsf/experimenter-workspace/ with
experimenter/cycle-1 branch ready. Idempotent.

Per ADR-0044 D3: additive role definition. Pre-B187 daemons
reading post-B187 configs just don't see the new role.

Per ADR-0001 D2: Smith births with one constitution_hash + DNA.
Future cycle tool additions use existing tools_add per-instance
path. Identity invariance preserved.

Per ADR-0008: Smith's frontier preference is documented in
ADR-0056 D4 but the dispatch-time routing wires up alongside
ModeKitClampStep in E2. Today every llm_think against Smith
defaults to local; operator can override via task_caps per
dispatch.

Verification: all four configs parse + experimenter is
present in each + build_app() imports clean.

Operator follow-up (not in this commit): run
dev-tools/birth-smith.command to actually birth Smith.

Next burst: B188 — E2 ModeKitClampStep + task_caps.mode."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 187 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
