#!/bin/bash
# Burst 202 — ADR-0058 Tool Forge UI (operator-direct, prompt-template path).
#
# Closes the tool-creation loop for non-developer operators. Skills
# already had B201; tools have an extra dimension (Python implementation)
# that the prompt-template approach sidesteps: operator's "new tool"
# becomes an instance of the generic PromptTemplateTool class with
# template + input_schema baked in. No new Python per tool.
#
# What ships:
#
#   docs/decisions/ADR-0058-tool-forge-ui.md  NEW.
#     Status: Accepted. Decision: option 2 (prompt-template wrapper).
#     Tranches T1-T6 ship in this burst. T7 (Smith demo) is B203.
#     T8 (plugin-protocol path, option 3) is a deferred follow-up.
#
#   src/forest_soul_forge/tools/builtin/prompt_template_tool.py  NEW.
#     Generic PromptTemplateTool class — one Python class, multiple
#     registered instances. Each instance binds at construct time to a
#     specific (name, version, input_schema, prompt_template) read from
#     a forged spec. Validation against input_schema runs at validate();
#     template substitution runs at execute() and routes through
#     ctx.provider.complete() (same path as LlmThinkTool). side_effects
#     = read_only by construction.
#
#   src/forest_soul_forge/forge/prompt_tool_forge.py  NEW.
#     Sister of forge.skill_forge. One-stage propose pipeline: take a
#     description, call provider, parse YAML reply into a
#     ForgedToolSpec, stage as spec.yaml + forge.log under
#     data/forge/tools/staged/<name>.v<version>/. Cross-check at parse
#     time: every {var} in prompt_template MUST appear in
#     input_schema.properties.
#
#   src/forest_soul_forge/daemon/routers/tools_forge.py  NEW.
#     Four endpoints (mirroring skills_forge.py shape):
#       POST   /tools/forge                          propose
#       POST   /tools/install                        install + register live
#       GET    /tools/staged/forged                  list pending
#       DELETE /tools/staged/forged/{name}/{version} discard
#     Install endpoint constructs + registers a PromptTemplateTool
#     LIVE in app.state.tool_registry (no daemon restart needed) and
#     augments app.state.tool_catalog with a synthetic ToolDef so the
#     dispatcher's catalog cross-check passes for forged tools.
#
#   src/forest_soul_forge/daemon/config.py  MODIFIED.
#     Added tool_staged_dir and tool_install_dir Path settings.
#
#   src/forest_soul_forge/daemon/app.py  MODIFIED.
#     Wired the new router. Lifespan walks data/forge/tools/installed/
#     after builtins + plugins, registers one PromptTemplateTool per
#     spec.yaml found, and augments the catalog. Surfaces as a
#     forged_tool_loader entry in startup_diagnostics so /healthz
#     reports load count + any errors.
#
#   tests/unit/test_daemon_tools_forge.py  NEW.
#     12 tests across 5 classes: install happy path (incl. LIVE
#     registry check + audit shape), missing token 401, path
#     traversal 400, overwrite flag, invalid spec 422, undeclared
#     template var 422, listing empty + populated, discard, propose
#     stage with canned-LLM stub, plus direct PromptTemplateTool
#     unit coverage of validate() type-checking + template sub.
#
#   frontend/index.html  MODIFIED.
#     Added "+ New tool" button (btn--primary) to the Tools tab
#     panel header alongside refresh and reload-from-disk.
#
#   frontend/js/tool-registry.js  MODIFIED.
#     openNewToolModal() function — same shape as Skills modal
#     (B201). Description textarea + Forge button → preview pane
#     showing description, input keys, archetypes, prompt template
#     preview, and Install / Discard buttons. Modal removes itself
#     on close (X / Escape / completion). On install, refreshes the
#     registry view so the new tool appears.
#
# What we deliberately did NOT do:
#   - Plugin-protocol path (option 3 from ADR-0058 alternatives).
#     Operators wanting tools with real I/O still go through the MCP
#     plugin protocol per ADR-0043. Future arc.
#   - Forged-proposals subsection in the Approvals tab. Modal handles
#     install/discard inline. The unified proposals queue is a UX
#     nice-to-have deferred from B201; still deferred.
#   - Inline tool editing post-install. Install is overwrite-or-create
#     only. Editing requires re-forge + re-install.
#   - Smith driving the endpoints. That's B203 — runtime test, not
#     a code burst.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — additive router, additive
#                  builtin tool class, additive lifespan step.
#
# Verification:
#   - tests/unit/test_daemon_tools_forge.py:    12 passed
#   - tests/unit/test_daemon_skills_forge.py:   13 passed (no regression)
#   - tests/unit/test_audit_chain.py:           39 passed (no regression)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0058-tool-forge-ui.md \
        src/forest_soul_forge/tools/builtin/prompt_template_tool.py \
        src/forest_soul_forge/forge/prompt_tool_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_daemon_tools_forge.py \
        frontend/index.html \
        frontend/js/tool-registry.js \
        dev-tools/commit-bursts/commit-burst202-tool-forge-ui.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(tools): ADR-0058 Tool Forge UI — operator-direct prompt-template path (B202)

Burst 202. Sister of B201 Skill Forge UI. Closes the tool-creation
loop for non-developer operators using the prompt-template approach
(option 2 per ADR-0058 alternatives, per Alex 2026-05-09): operator's
'new tool' becomes an instance of a generic PromptTemplateTool class
with template + input_schema baked in. No new Python per forged tool.

ADR-0058 documents the design. Substrate is small but cohesive:

  PromptTemplateTool (builtin/prompt_template_tool.py)
    One class, multiple registered instances. Each binds at construct
    time to (name, version, input_schema, prompt_template). Validates
    against schema at validate(); substitutes args via str.format()
    at execute(); calls ctx.provider.complete() (same path as
    LlmThinkTool). side_effects = read_only by construction.

  forge.prompt_tool_forge
    One-stage propose pipeline: description -> LLM -> ForgedToolSpec
    -> staged spec.yaml + forge.log under data/forge/tools/staged/.
    Cross-check at parse: every {var} in prompt_template must appear
    in input_schema.properties.

  Four endpoints:
    POST   /tools/forge                          propose
    POST   /tools/install                        install + register LIVE
    GET    /tools/staged/forged                  list pending
    DELETE /tools/staged/forged/{name}/{version} discard

  Install registers a PromptTemplateTool LIVE in the dispatcher
  registry (no daemon restart) and augments app.state.tool_catalog
  with a synthetic ToolDef so the catalog cross-check passes.

  Lifespan walks data/forge/tools/installed/, registers one
  PromptTemplateTool per spec.yaml found, augments the catalog.
  Survives daemon restarts. Surfaces as a forged_tool_loader entry
  in startup_diagnostics.

  Frontend: + New tool button on the Tools tab opens a modal with
  description textarea + Forge button -> preview pane (description,
  input keys, archetype tags, prompt template preview) with Install
  / Discard. Same UX shape as B201 Skills modal.

12 new tests in tests/unit/test_daemon_tools_forge.py covering
install happy path (+ live registry + catalog augmentation + audit
shape), missing token 401, path traversal 400, overwrite flag,
invalid spec 422, undeclared template var 422, listing empty +
populated + skip-invalid, discard, propose stage with canned-LLM
stub, plus direct unit coverage of PromptTemplateTool.validate()
type checking and min/max bounds.

Deliberately NOT in this burst:
  - Plugin-protocol path (option 3). Operators wanting tools with
    real I/O still go through MCP plugin protocol per ADR-0043.
    Future arc.
  - Forged-proposals subsection in Approvals tab. Modal handles
    install/discard inline; deferred again.
  - Inline tool editing post-install. Install is overwrite-or-create
    only.
  - Smith driving the endpoints. That's B203 — runtime test, not
    a code burst.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — additive router, additive
                 builtin tool class, additive lifespan step."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 202 complete ==="
echo "=== Tool Forge UI shipped — operator-direct prompt-template tools from SoulUX. ==="
echo "Press any key to close."
read -n 1
