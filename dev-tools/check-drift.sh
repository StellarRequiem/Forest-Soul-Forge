#!/usr/bin/env bash
# dev-tools/check-drift.sh — drift sentinel for STATE.md and README.md.
#
# Runs every count claim in the headline tables against disk reality and
# prints a comparison. Use before any release tag, after any significant
# arc of work, or whenever you suspect the docs have drifted.
#
# Usage:  bash dev-tools/check-drift.sh
#
# Origin: Burst 82 (2026-05-03). Triggered after the audit chain path
# mystery + LoC count drift surfaced during Run 001 cleanup. Several
# claims had silently rotted: STATE's commit count was 79 commits
# stale, .command scripts undercounted by 52, LoC undercounted by ~8k.
# The lesson: "I just bumped the doc" is not a substitute for a
# deterministic check.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

bar() { printf "\n=== %s ===\n" "$1"; }
row() { printf "  %-34s claim=%-16s actual=%s\n" "$1" "$2" "$3"; }

bar "Source LoC (Python)"
SLOC=$(find src -name "*.py" -not -path "*/__pycache__/*" 2>/dev/null | xargs cat 2>/dev/null | wc -l | tr -d ' ')
SLOC_STATE=$(grep -E "Source LoC" STATE.md | grep -oE "[~]?[0-9,]+" | head -1)
SLOC_README=$(grep -E "Source LoC" README.md | grep -oE "[~]?[0-9,]+" | head -1)
row "STATE.md says"   "$SLOC_STATE"   "$SLOC"
row "README.md says"  "$SLOC_README"  "$SLOC"

bar "Tests (passing)"
TESTS_STATE=$(grep -E "Tests \(passing\)" STATE.md | grep -oE "\*\*[0-9,]+\*\*" | head -1)
TESTS_README=$(grep -E "Tests \(passing\)" README.md | grep -oE "\*\*[0-9,]+\*\*" | head -1)
row "STATE.md says"   "$TESTS_STATE"  "(run pytest to confirm)"
row "README.md says"  "$TESTS_README" "(run pytest to confirm)"

bar "Builtin tools"
TOOL_FILES=$(ls src/forest_soul_forge/tools/builtin/*.py 2>/dev/null | grep -v __init__ | wc -l | tr -d ' ')
TOOLS_STATE=$(grep -E "Builtin tools" STATE.md | grep -oE "\*\*[0-9]+\*\*" | head -1)
row "STATE.md says"   "$TOOLS_STATE"  "$TOOL_FILES"

bar "Genres"
GENRES_ACT=$(python3 -c "import yaml; d=yaml.safe_load(open('config/genres.yaml')); print(len(d.get('genres',{})))" 2>/dev/null)
GENRES_STATE=$(grep -E "^\| Genres" STATE.md | grep -oE "[0-9]+" | head -1)
GENRES_README=$(grep -E "Genres" README.md | grep -oE "\*\*[0-9]+\*\*" | head -1)
row "STATE.md says"   "$GENRES_STATE"  "$GENRES_ACT"
row "README.md says"  "$GENRES_README" "$GENRES_ACT"

bar "Skill manifests"
EX_SKILLS=$(ls examples/skills/*.yaml 2>/dev/null | wc -l | tr -d ' ')
INST_SKILLS=$(ls data/forge/skills/installed/*.yaml 2>/dev/null | wc -l | tr -d ' ')
SKILL_STATE=$(grep -E "Skill manifests" STATE.md | grep -oE "[0-9]+" | head -1)
SKILL_README=$(grep -E "Skill manifests" README.md | grep -oE "\*\*[0-9]+\*\*" | head -1)
row "STATE.md says"   "$SKILL_STATE"   "examples=$EX_SKILLS  installed=$INST_SKILLS"
row "README.md says"  "$SKILL_README"  "examples=$EX_SKILLS  installed=$INST_SKILLS"

bar "ADRs filed"
ADR_FILES=$(ls docs/decisions/ADR-*.md 2>/dev/null | wc -l | tr -d ' ')
ADR_UNIQUE=$(ls docs/decisions/ADR-*.md | sed 's/.*ADR-//' | cut -c1-4 | sort -u | wc -l | tr -d ' ')
ADR_STATE=$(grep -E "^\| ADRs filed" STATE.md | grep -oE "[0-9]+" | head -1)
ADR_README=$(grep -E "ADRs filed" README.md | grep -oE "\*\*[0-9]+\*\*" | head -1)
row "STATE.md says"   "$ADR_STATE"     "files=$ADR_FILES  unique-numbers=$ADR_UNIQUE"
row "README.md says"  "$ADR_README"    "files=$ADR_FILES  unique-numbers=$ADR_UNIQUE"

bar "Frontend modules (vanilla JS)"
FE_JS=$(find frontend -name "*.js" -not -path "*/node_modules/*" 2>/dev/null | wc -l | tr -d ' ')
FE_STATE=$(grep -E "Frontend modules" STATE.md | grep -oE "[0-9]+" | head -1)
FE_README=$(grep -E "Frontend modules" README.md | grep -oE "\*\*[0-9]+\*\*" | head -1)
row "STATE.md says"   "$FE_STATE"      "$FE_JS"
row "README.md says"  "$FE_README"     "$FE_JS"

bar ".command operator scripts"
CMD_FILES=$(ls *.command 2>/dev/null | wc -l | tr -d ' ')
CMD_STATE=$(grep -E "command\` operator scripts" STATE.md | grep -oE "[0-9]+" | head -1)
row "STATE.md says"   "$CMD_STATE"     "$CMD_FILES"

bar "Total commits on main"
COMMITS_ACT=$(git log --oneline 2>/dev/null | wc -l | tr -d ' ')
COMMITS_STATE=$(grep -E "Total commits" STATE.md | grep -oE "~?[0-9]+" | head -1)
row "STATE.md says"   "$COMMITS_STATE" "$COMMITS_ACT"

bar "Latest tag"
LATEST_TAG=$(git tag --sort=-version:refname | head -1)
TAG_STATE=$(grep -E "v0\.[0-9]+\.[0-9]+ shipped" STATE.md | grep -oE "v0\.[0-9]+\.[0-9]+" | sort -V | tail -1)
row "STATE.md says"   "$TAG_STATE"     "$LATEST_TAG"

bar "Audit chain default path"
DEFAULT=$(grep -A1 "audit_chain_path" src/forest_soul_forge/daemon/config.py | grep -oE 'Path\("[^"]+"\)' | head -1)
row "Daemon default"  "$DEFAULT"       "(env override: FSF_AUDIT_CHAIN_PATH)"

bar "ADR number gaps (ADR-0009 .. ADR-0015 are intentionally missing)"
for n in $(seq -f "%04g" 1 50); do
  if ! ls docs/decisions/ADR-${n}* 2>/dev/null | grep -q . ; then
    if [[ $n -le $(echo "$LATEST_TAG" | grep -oE "[0-9]+\$" || echo 0)$(printf "0040") ]]; then
      printf "  missing: ADR-%s\n" "$n"
    fi
  fi
done | head -10

bar "Untracked files (excluding .gitignore'd)"
git ls-files --others --exclude-standard 2>&1 | head -10

bar "Stale test agents in registry (Forge_FB*, EngTest, RevTest, VoiceTest, GenreDemo with status=active)"
python3 -c "
import sqlite3
conn = sqlite3.connect('data/registry.sqlite')
cur = conn.execute(\"\"\"
  SELECT instance_id, agent_name, role, status FROM agents
  WHERE (agent_name LIKE 'Forge_FB%' OR agent_name LIKE 'EngTest%'
      OR agent_name LIKE 'RevTest%' OR agent_name LIKE 'VoiceTest%'
      OR agent_name LIKE 'GenreDemo%')
    AND status = 'active'
  ORDER BY agent_name;
\"\"\")
rows = list(cur)
if not rows:
    print('  none — clean.')
else:
    for r in rows:
        print(f'  {r[3]:8s}  {r[1]:30s}  {r[0]}')
" 2>/dev/null

echo ""
echo "Drift check complete."
echo "If any 'claim != actual' rows surface, update STATE.md / README.md before tagging."
