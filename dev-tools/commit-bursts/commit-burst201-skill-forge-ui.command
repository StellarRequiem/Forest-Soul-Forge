#!/bin/bash
# Burst 201 — ADR-0057 Skill Forge UI (operator-direct).
#
# Closes the loop for non-developer operators who want to create
# skills from the SoulUX UI rather than dropping to CLI. The propose
# engine (forge/skill_forge.py) and install path (cli/install.py
# ::run_skill) already existed as CLI flows; this burst is a thin
# HTTP wrapper + a frontend modal so they reach SoulUX.
#
# What ships:
#
#   docs/decisions/ADR-0057-skill-forge-ui.md  NEW.
#     Status: Accepted. Concise — the Decision section maps 1:1 to
#     the four endpoints + the frontend wiring. Tranches T1-T6 in a
#     table at the bottom; T1-T5 ship in this burst, T6 (Smith
#     driving the endpoints) is a runtime test deferred to B203.
#
#   src/forest_soul_forge/daemon/routers/skills_forge.py  NEW.
#     Four endpoints. All authed via X-FSF-Token + writes_enabled
#     except GET /skills/staged which is read-only (still authed for
#     consistency with the rest of the writes surface).
#
#       POST   /skills/forge
#         Calls forge.skill_forge.forge_skill (async; the sync
#         wrapper would deadlock the route handler's event loop).
#         Surfaces ManifestError as 422 (your input was bad), other
#         exceptions as 502 (the substrate failed) — meaningful
#         distinction for the modal's error display.
#
#       POST   /skills/install
#         Mirrors cli/install.py::run_skill. Refuses staged_path
#         outside skill_staged_dir to prevent path traversal. Wraps
#         file copy + audit emit + catalog reload under
#         app.state.write_lock for cross-resource discipline (the
#         chain itself is self-protecting per B199).
#
#       GET    /skills/staged
#         Lists pending staged manifests. Skips invalid ones rather
#         than 500ing.
#
#       DELETE /skills/staged/{name}/{version}
#         Removes the staged dir + emits a forge_skill_proposed
#         event with mode=discarded. Distinct mode field so
#         filter-by-mode queries can separate abandoned proposals
#         from in-flight ones.
#
#   src/forest_soul_forge/daemon/config.py  MODIFIED.
#     Added skill_staged_dir field (default
#     data/forge/skills/staged). Pre-B201 the path was hard-coded
#     inside the engine; promoting to a settings field lets the
#     HTTP endpoints isolate to tmp dirs in tests AND lets operators
#     override the staged root at runtime.
#
#   src/forest_soul_forge/daemon/app.py  MODIFIED.
#     Registered the new router via include_router after the
#     existing skills_* routers.
#
#   tests/unit/test_daemon_skills_forge.py  NEW.
#     13 tests across 4 classes. Covers happy path + auth + path
#     traversal + overwrite flag + invalid manifest + listing +
#     discard + propose stage with a canned-provider stub. All 13
#     pass on Python 3.10 sandbox.
#
#   frontend/index.html  MODIFIED.
#     Added "+ New skill" button (btn--primary) to the Skills tab
#     panel header alongside the existing refresh button.
#
#   frontend/js/skills.js  MODIFIED.
#     Empty-state text updated (no longer instructs operator to drop
#     to CLI). New openNewSkillModal() function creates a self-
#     contained DOM overlay with description textarea + Forge button
#     -> preview pane with Install / Discard buttons. Modal removes
#     itself on close (X / Escape / completion). All DOM created
#     inline rather than via template — no new HTML in index.html
#     beyond the trigger button.
#
# What we deliberately did NOT do:
#   - Build a Forged proposals subsection in the Approvals tab.
#     The modal already handles install/discard inline so the
#     Approvals subsection isn't blocking; deferred to B202
#     where it'll handle BOTH skill and tool proposals together.
#   - Tool Forge UI (ADR-0058). Paired ADR + matching arc; ships
#     in B202. Tools have a Python-implementation dimension that
#     skills don't, so the design choice (option 2: prompt-template
#     wrapper around llm_think.v1, per Alex's directive
#     2026-05-09) needs its own ADR.
#   - Smith driving the endpoints. Same engine that operators call
#     can be called by Smith's experimenter cycle path. T6 of
#     ADR-0057 covers it; runtime demo in B203, not a code burst.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — the new router is additive.
#                  Existing /skills GET endpoint unchanged.
#
# Verification:
#   - tests/unit/test_daemon_skills_forge.py:  13 passed
#   - tests/unit/test_audit_chain.py:           39 passed
#                                              (no regression from B199)
#   - Live smoke (recommended after install): from the SoulUX Skills
#     tab, click "+ New skill", type a workflow description, click
#     Forge, review the staged manifest, click Install. The Skills
#     tab refreshes and the new skill appears as a card. The Audit
#     tab shows matching forge_skill_proposed + forge_skill_installed
#     events.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0057-skill-forge-ui.md \
        src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_daemon_skills_forge.py \
        frontend/index.html \
        frontend/js/skills.js \
        dev-tools/commit-bursts/commit-burst201-skill-forge-ui.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skills): ADR-0057 Skill Forge UI — operator-direct (B201)

Burst 201. Closes the loop for non-developer operators who want to
create skills from SoulUX rather than CLI. The Skill Forge engine
(forge/skill_forge.py) and install path (cli/install.py::run_skill)
already existed; this burst is a thin HTTP wrapper + frontend modal.

ADR-0057 documents the design. Four new endpoints under /skills/:

  POST   /skills/forge                       propose stage
  POST   /skills/install                     install stage
  GET    /skills/staged                      list pending proposals
  DELETE /skills/staged/{name}/{version}     discard a proposal

All authed via X-FSF-Token + writes_enabled (read-only listing kept
authed for consistency with the rest of the writes surface). Audit
emits fire under app.state.write_lock for cross-resource discipline;
chain.append is itself thread-safe per B199.

skill_staged_dir promoted to a first-class DaemonSettings field
(default data/forge/skills/staged) so the HTTP endpoints can isolate
to tmp dirs in tests and operators can override the staged root.

Frontend wiring on the Skills tab: new + New skill button opens a
self-contained DOM overlay with description textarea + Forge button.
On Forge, the modal hides the form and shows a manifest preview with
Install / Discard buttons. Closes on completion / X / Escape.

13 new tests in tests/unit/test_daemon_skills_forge.py covering
happy path + auth + path traversal + overwrite + invalid manifest +
listing + discard + propose stage. All 13 pass on the sandbox
Python 3.10 baseline.

Deliberately NOT in this burst:
  - Forged proposals subsection in the Approvals tab. Modal handles
    install/discard inline; Approvals subsection is a batched-review
    UX nice-to-have deferred to B202 where it covers both skills
    and tools.
  - Tool Forge UI (ADR-0058). Paired arc, ships next burst (B202).
  - Smith driving the endpoints. Runtime demo, not a code burst;
    queued for B203.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — the new router is additive."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 201 complete ==="
echo "=== Skill Forge UI shipped — operator-direct from SoulUX Skills tab. ==="
echo "Press any key to close."
read -n 1
