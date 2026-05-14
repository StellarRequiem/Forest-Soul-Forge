#!/bin/bash
# Burst 289 — ADR-0071 T1: plugin author scaffold (`fsf plugin-new`).
#
# Plugin authoring goes from "read ADR-0043 + 3 example dirs + figure
# out the manifest" to "run one command + edit one stub."
#
# What ships:
#
# 1. docs/decisions/ADR-0071-plugin-author-adapter-kit.md — full
#    record. Three decisions (scaffold command, adapter command,
#    reference templates). Four tranches T1-T4.
#
# 2. src/forest_soul_forge/cli/plugin_author.py — `fsf plugin-new`
#    subcommand. Generates:
#      ~/.forest/plugins/<name>/
#      ├── plugin.yaml         (ADR-0043 manifest pre-filled)
#      ├── README.md           (next-steps docs)
#      ├── tools/<tool>.py     (Tool Protocol stub: validate +
#      │                        async execute returning ToolResult)
#      ├── tests/test_<tool>.py (pytest skeleton with mock ctx)
#      └── .gitignore
#    Validation: plugin name lowercase+hyphens, tool name
#    lowercase+underscores, valid tier (read_only / network /
#    filesystem / external). Refuses existing dir without --force.
#
# 3. src/forest_soul_forge/cli/main.py: register `fsf plugin-new`.
#    Top-level subcommand (not nested under existing `fsf plugin`
#    yet — incremental wiring matches the rest of the CLI; future
#    tranche consolidates).
#
# Tests (test_cli_plugin_author.py — 11 cases):
#   Validation:
#     - bad name (uppercase) refused
#     - bad name (digit-prefix) refused
#     - bad tool name (camelCase) refused
#   Scaffolding:
#     - all 5 files created
#     - plugin.yaml valid YAML + correct tier + tool name + license
#     - tool .py parseable + class name in CamelCase + "Tool"
#     - test .py parseable + test fn name present
#   Conflict handling:
#     - existing dir refused without --force
#     - --force overwrites cleanly
#   Tier coverage:
#     - all 4 valid tiers accepted
#   Subparser registration:
#     - add_subparser hook produces a parsable command
#
# What's NOT in T1 (queued):
#   T2: network + filesystem + external reference templates
#   T3: `fsf plugin adapt <upstream>` MCP wrapper generator
#   T4: plugin author runbook + publishing guide

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0071-plugin-author-adapter-kit.md \
        src/forest_soul_forge/cli/plugin_author.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_plugin_author.py \
        dev-tools/commit-bursts/commit-burst289-adr0071-t1-plugin-scaffold.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): ADR-0071 T1 — fsf plugin-new scaffold (B289)

Burst 289. Plugin authoring goes from 'read ADR + 3 example dirs
+ figure out the manifest' to 'run one command + edit one stub.'

What ships:

  - ADR-0071 full record. Three decisions: scaffold command,
    adapter command (T3 queued), reference templates (T2 queued).
    Four tranches T1-T4.

  - cli/plugin_author.py: \`fsf plugin-new\` subcommand.
    Generates plugin.yaml (ADR-0043 manifest pre-filled with
    operator-supplied name + tier + tool + license), README.md
    (next-steps docs), tools/<tool>.py (Tool Protocol stub —
    validate + async execute returning ToolResult; class name
    CamelCase from snake_case + 'Tool'), tests/test_<tool>.py
    (pytest skeleton with mock ctx), .gitignore (Forest plugin
    defaults).

    Validation: plugin name regex (lowercase + hyphens, e.g.
    forest-plaid), tool name regex (lowercase + underscores,
    e.g. transactions_list), tier choices (read_only / network /
    filesystem / external). Refuses existing dir without --force;
    --force overwrites cleanly.

  - cli/main.py: register \`fsf plugin-new\` at top level.

Tests: test_cli_plugin_author.py — 11 cases covering name +
tool-name + tier validation, full scaffold output (all 5 files),
plugin.yaml content (valid YAML, right tier/tool/license), tool
+ test module Python parseability, existing-dir refusal,
--force overwrite, all 4 valid tiers, subparser registration.

Queued T2-T4: network/filesystem/external templates,
\`fsf plugin adapt <upstream>\` MCP wrapper generator (the 'port
face' for anthropic/mcp-servers ecosystem), plugin author
runbook."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 289 complete — ADR-0071 T1 plugin scaffold shipped ==="
echo "Next: T3 fsf plugin adapt (wraps existing MCP servers)."
echo ""
echo "Press any key to close."
read -n 1
