#!/bin/bash
# Burst 233 — ship marketplace seed skills 6-10.
#
# Closes the 10-skill seed set Alex approved. With B230+B231
# (10 tools) and B232 (skills 1-5), this completes the 20-item
# marketplace seed catalog: 10 tools + 10 skills, all in
# examples/{tools,skills}/, all parsing cleanly, all composable.
#
# Skills shipped:
#
# 6. agent_activity_digest.v1 — 2 steps
#    memory_recall -> text_summarize
#    Inputs: agent_focus_topic (or "" for undirected)
#    Output: 3-bullet snapshot of recent activity.
#    Use case: operator inspecting a long-running 24/7 agent.
#
# 7. memory_consolidate.v1 — 3 steps
#    memory_recall -> llm_think -> text_summarize
#    Inputs: topic, layer (episodic | semantic | procedural)
#    Output: consolidated prose doc + executive summary +
#            source memory count.
#    Use case: knowledge_consolidator weekly per topic.
#
# 8. incident_first_pass.v1 — 3 steps
#    log_scan -> audit_chain_verify -> llm_think (triage)
#    Inputs: log_paths, pattern, since
#    Output: severity-hinted one-paragraph triage report
#            + match count + chain status.
#    Use case: log_lurker scheduled scan; light-weight version of
#    the full ADR-0033 swarm chain.
#
# 9. release_notes.v1 — 3 steps
#    commit_message -> text_summarize -> tone_shift
#    Inputs: diff, audience (end_users | operators | developers),
#            tone (friendly | technical | concise | celebratory)
#    Output: audience-and-tone-shaped release notes + intermediate
#            artifacts (structured commit msg, bullet draft).
#
# 10. agent_introspect.v1 — 3 steps
#     memory_recall -> llm_think (first-person reflection) ->
#     text_summarize
#     Inputs: reflection_window, voice (candid | professional |
#             playful | anxious | confident)
#     Output: first-person reflection covering focus + patterns +
#             what works + change asks, plus a brief tldr.
#     Use case: agent self-review cycle; output can land in
#     memory_write for later memory_consolidate calls.
#
# All five validated via parse_manifest with post-B208 ref-scope
# coverage: every \${var} is \${inputs.X} or \${step.out.field};
# every output mapping references valid step outputs.
#
# Net seed catalog after this burst:
#   examples/tools/    — 10 prompt-template tools + README (B230, B231)
#   examples/skills/   — 10 marketplace seed skills (B232, B233)
#   plus 26 pre-existing skills from earlier ADR-0033 work
#
# These 20 items are the marketplace seed content the
# forest-marketplace sibling repo (when scaffolded) will index as
# its first entries.
#
# Activation: cp examples/{tools,skills}/*.yaml to the
# corresponding data/forge/{tools,skills}/installed/ directory +
# daemon restart. Once registered, agents can be granted tool
# access via the Agents tab pane (ADR-0060 T6) and can dispatch
# skills via POST /agents/{id}/skills/run.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — 5 new shipped skill
#                  examples. Zero existing call-site changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/skills/agent_activity_digest.v1.yaml \
        examples/skills/memory_consolidate.v1.yaml \
        examples/skills/incident_first_pass.v1.yaml \
        examples/skills/release_notes.v1.yaml \
        examples/skills/agent_introspect.v1.yaml \
        dev-tools/commit-bursts/commit-burst233-skills-6-10.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(skills): marketplace seed skills 6-10 (B233)

Burst 233. Closes the 10-skill seed set Alex approved.

  6. agent_activity_digest.v1 — third-person agent inspection
  7. memory_consolidate.v1 — scattered memories -> coherent doc
  8. incident_first_pass.v1 — log scan + chain check + triage
  9. release_notes.v1 — diff -> audience-tuned release notes
  10. agent_introspect.v1 — first-person agent self-reflection

All five parse via parse_manifest with full ref-scope coverage.

Net seed catalog: 10 tools + 10 skills in examples/{tools,skills}/.
This is the marketplace seed content forest-marketplace will index
as its first entries.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: additive — 5 new shipped skill examples."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 233 complete ==="
echo "=== 10 of 10 skills shipped. 20-item marketplace seed catalog COMPLETE. ==="
echo "Press any key to close."
read -n 1
