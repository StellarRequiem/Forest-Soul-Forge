#!/bin/bash
# Burst 305 - ADR-0071 T2: tier-specific tool exemplars.
#
# fsf plugin-new's scaffolded tool body branches by tier so
# authors see canonical patterns for their chosen side-effect
# class instead of a generic echo stub for everything.
#
# What ships:
#
# 1. src/forest_soul_forge/cli/plugin_author.py:
#    - _TIER_RUBRIC dict mapping each tier to a one-paragraph
#      explanation surfaced in the scaffolded tool's docstring.
#    - _tier_exemplar(tier) returning (validate_body, execute_body,
#      extra_imports) tuples per tier:
#        * read_only  — echo args (the pre-T2 default; backward-
#                       compat).
#        * network    — urllib.request.urlopen with timeout=10 +
#                       URL scheme validation + URLError handling.
#        * filesystem — pathlib.Path.resolve + an _is_within
#                       helper that validates against ctx.allowed_paths
#                       before any open(). Inline comment makes
#                       clear that path scoping is the tool's
#                       responsibility (Forest doesn't enforce at
#                       OS level).
#        * external   — subprocess.run with capture_output + a 30s
#                       timeout + TimeoutExpired branch. Inline
#                       guidance flags shell=True as a shell-
#                       injection vector.
#    - _render_tool_module() now slots the tier exemplar bodies
#      directly under the class methods. Module-level extra
#      imports (urllib / pathlib + helper / subprocess) get
#      hoisted into the file header.
#
# 2. tests/unit/test_cli_plugin_author.py - 5 new T2 cases:
#    - network scaffold imports urllib.request + emits urlopen +
#      timeout + URL scheme validation
#    - filesystem scaffold demonstrates ctx.allowed_paths
#      validation via the _is_within helper
#    - external scaffold uses subprocess.run with timeout +
#      TimeoutExpired branch + the NEVER-shell-True warning
#    - read_only scaffold keeps the pre-T2 echo exemplar
#      (backward compat: no extra imports leak in)
#    - tier rubric (network's 'outbound HTTP' marker) lands in
#      the module docstring
#
# Sandbox-verified all 4 tier scaffolds parse + contain the
# expected exemplar markers.
#
# What's NOT in T2 (queued):
#   T3: `fsf plugin adapt <upstream>` MCP wrapper generator -
#       the 'port face' for anthropic/mcp-servers ecosystem.
#   T4: plugin author runbook + publishing guide.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/plugin_author.py \
        tests/unit/test_cli_plugin_author.py \
        dev-tools/commit-bursts/commit-burst305-adr0071-t2-templates.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): ADR-0071 T2 - tier-specific tool exemplars (B305)

Burst 305. fsf plugin-new's scaffolded tool body now branches
by tier. Authors see canonical patterns for their chosen
side-effect class (network -> urllib.urlopen with timeout,
filesystem -> _is_within helper validating ctx.allowed_paths,
external -> subprocess.run with timeout + TimeoutExpired)
instead of a generic echo stub for everything.

What ships:

  - cli/plugin_author.py: _TIER_RUBRIC maps each tier to a
    one-paragraph explanation that lands in the scaffolded
    module's docstring. _tier_exemplar(tier) returns
    (validate_body, execute_body, extra_imports) per tier.
    _render_tool_module slots the exemplar under the class
    methods + hoists tier-specific imports (urllib /
    pathlib+_is_within helper / subprocess) into the file
    header.

    Tier exemplars include the canonical safety guidance for
    each: network does URL-scheme validation + URLError handling,
    filesystem requires path validation against ctx.allowed_paths
    BEFORE any open(), external warns against shell=True and
    captures stderr for diagnosability.

Tests: test_cli_plugin_author.py - 5 new cases covering each
tier's exemplar markers, read_only backward-compat (no extra
imports leak in), and tier-rubric inclusion in the module
docstring.

Sandbox-verified all 4 tier scaffolds parse + contain the
expected exemplar markers.

Queued T3-T4: fsf plugin adapt <upstream> wrapper generator
('port face' for anthropic/mcp-servers ecosystem), author
runbook + publishing guide."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 305 complete - ADR-0071 T2 templates shipped ==="
echo ""
echo "Press any key to close."
read -n 1
