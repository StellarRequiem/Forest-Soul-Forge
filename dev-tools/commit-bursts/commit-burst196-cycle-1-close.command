#!/bin/bash
# Burst 196 — ADR-0056 cycle 1 close. Smith produced a unit
# test file across 6 plan iterations + an apply step that
# surfaced a real B195 bug. This burst lands all of it.
#
# What ships:
#
#   tests/unit/test_last_shortcut_route.py:
#     - NEW. Four tests for GET /conversations/{id}/last-shortcut.
#       Test architecture authored by Smith across cycles 1.2 -> 1.6
#       under operator review; helper kwargs supplied verbatim by
#       operator after Smith's three earlier attempts paraphrased
#       the create_conversation signature.
#     - 200 path: conversation seeded, audit chain has matching
#       tool_call_shortcut, endpoint returns dict with the six
#       real keys (shortcut_id / shortcut_similarity /
#       shortcut_action_kind / audit_seq / timestamp / instance_id).
#     - 404 (no conversation): conversation_id absent from registry.
#     - 404 (no matching events): conversation exists, audit chain
#       has events but none matching session_id=conv-{id} +
#       event_type=tool_call_shortcut.
#     - most-recent-wins: two matching events for the same
#       conversation, endpoint returns the LATEST one. THIS test
#       caught the B195 bug fixed below.
#     - Uses real DaemonSettings + real Registry + real AuditChain
#       (no mocks). allow_write_endpoints=True is required because
#       audit chain init is gated on that flag at daemon/app.py L229.
#
#   src/forest_soul_forge/daemon/routers/conversations.py:
#     - 1-line bugfix in get_last_shortcut. The previous code did
#       'for entry in reversed(entries)' against audit.tail(200)
#       output. Problem: tail() already returns newest-first
#       (it appends to a deque in file order then list-reverses
#       at return, see audit_chain.py L367), so reversed() flipped
#       it BACK to oldest-first and the loop returned the OLDEST
#       matching shortcut, not the newest. Surfaced by Smith's
#       test_most_recent_wins. Fix: drop reversed(), iterate
#       entries directly. The chat-tab thumbs widget would have
#       been reinforcing stale shortcuts on chatty conversations
#       without this catch.
#
#   docs/decisions/ADR-0056-experimenter-agent.md:
#     - NEW Followups section recording 5 findings from the
#       cycle 1 trial:
#         1. Cycle dispatches are stateless; agent fabricates when
#            prior plan isn't in-prompt.
#         2. Frontier routing produces 2.3x richer specifics vs
#            local qwen2.5-coder:7b on this target.
#         3. min_confidence_to_act=0.55 correctly flags-but-doesn't-
#            stop; right behavior for YELLOW posture.
#         4. <copy_verbatim> wrappers stop kwargs paraphrasing
#            (validated cycle 1.6 vs 1.5).
#         5. Smith's test caught a real B195 bug.
#     - Filed E7 (prior-cycle artifact threading) as the next
#       implementation tranche. Gated on more cycle data before
#       wiring into the daemon.
#
#   dev-tools/smith-cycle-1-plan.command:
#     - NEW. The dispatch script the operator iterated across
#       cycles 1.2 -> 1.6. Final v6 form embeds v3's frozen file
#       body under <prior_cycle> and the helper replacement under
#       <copy_verbatim id="helper">. Builds the JSON body via
#       Python heredoc to handle multi-line content cleanly.
#       Keeps the v6 prompt as the reference implementation of
#       the two prompt-engineering findings.
#
#   dev-tools/smith-cycle-1-v3-snapshot.py:
#     - NEW. Frozen copy of Smith's v3 plan output. Read by the
#       v5+ dispatch scripts to thread prior-cycle context into
#       the prompt. Will be moot once E7 wires this automatically;
#       kept for reproducibility of cycle 1.
#
#   dev-tools/run-last-shortcut-tests.command:
#     - NEW. Operator-runnable pytest invocation for this test
#       file specifically. Saves output to cycle-1-pytest-output.txt
#       so the assistant can read the green/red status without
#       Terminal-text scraping.
#
#   dev-tools/cycle-1-pytest-output.txt:
#     - NEW. The green test run that closed cycle 1.
#
#   dev-tools/smith-cycle-1-plan-response-v{2..6}.json:
#     - NEW. Full iteration trail. v2 was the vapor-target
#       (HSM adapter for nonexistent key_rotate.v1) — caught
#       by operator path validation. v3 was the first solid
#       plan with right schema + 4 tests + risk flagging. v4
#       went sideways when the operator asked for a "minimal
#       fix" without including the v3 body — Smith fabricated
#       a different file. v5 added <prior_cycle> threading and
#       restored structural fidelity but Smith paraphrased the
#       helper kwargs. v6 added <copy_verbatim> markers and
#       achieved full compliance.
#
# Per ADR-0044 D3: zero ABI changes. The conversations.py edit
# is a 1-line behavior fix to an existing read-only endpoint;
# response shape unchanged. Pre-B196 daemons return the OLDEST
# matching shortcut; post-B196 return the NEWEST matching
# shortcut. Operators with chat-tab thumbs already enabled
# benefit immediately.
#
# Per ADR-0001 D2: no identity surface touched. Smith's
# constitution + DNA + audit chain entries from cycles 1.2..1.6
# remain valid. The cycle dispatches themselves are recorded in
# the chain via tool_call_succeeded events (audit_seq 2812 v3,
# 2827 v4, 2842 v4-broken, 2854 v5, 2860 v6).
#
# Verification:
#   - 4 passed in 0.83s on tests/unit/test_last_shortcut_route.py
#   - dev-tools/cycle-1-pytest-output.txt has the green output
#   - build_app() imports clean (test file imports it directly)
#
# Operator-facing follow-up:
#   - Restart daemon (the conversations.py change requires it).
#   - The chat-tab thumbs widget will now surface the most recent
#     shortcut hit, not the oldest. No frontend change needed.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0056-experimenter-agent.md \
        src/forest_soul_forge/daemon/routers/conversations.py \
        tests/unit/test_last_shortcut_route.py \
        dev-tools/smith-cycle-1-plan.command \
        dev-tools/smith-cycle-1-v3-snapshot.py \
        dev-tools/run-last-shortcut-tests.command \
        dev-tools/cycle-1-pytest-output.txt \
        dev-tools/smith-cycle-1-plan-response.json \
        dev-tools/smith-cycle-1-plan-response-v3.json \
        dev-tools/smith-cycle-1-plan-response-v4.json \
        dev-tools/smith-cycle-1-plan-response-v5.json \
        dev-tools/smith-cycle-1-plan-response-v6.json \
        dev-tools/commit-bursts/commit-burst196-cycle-1-close.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 cycle 1 close (B196) — test + bug fix + 5 findings

Burst 196. Lands the deliverables of Smith's first work-mode
cycle. The cycle ran 6 plan iterations (v1 vapor-target -> v6
verbatim-compliant) plus an apply step that surfaced a real
B195 bug.

Output on disk:

tests/unit/test_last_shortcut_route.py: NEW. Four tests for
GET /conversations/{id}/last-shortcut. Real DaemonSettings,
real Registry, real AuditChain. Test architecture authored
by Smith; helper kwargs supplied verbatim by operator after
three paraphrasing attempts. allow_write_endpoints=True
required because audit chain init is gated on that flag.

src/forest_soul_forge/daemon/routers/conversations.py: 1-line
bugfix. get_last_shortcut was double-reversing audit.tail
output (which already returns newest-first), causing it to
return the OLDEST matching shortcut for chat-tab thumbs
reinforcement. Smith's test_most_recent_wins caught it.
Drop reversed(), iterate entries directly.

docs/decisions/ADR-0056-experimenter-agent.md: NEW Followups
section. 5 findings:
1. Cycle dispatches are stateless across versions.
2. Frontier routing produces 2.3x richer plans vs local.
3. YELLOW posture flag-but-dont-stop is correct behavior.
4. <copy_verbatim> wrappers stop kwargs paraphrasing.
5. Smith's tests catch real bugs (this one in B195).

E7 prior-cycle artifact threading filed as next tranche;
gated on more cycle data before wiring into the daemon.

dev-tools/: cycle 1 dispatch script + v3 snapshot + pytest
runner + green output + 5 response JSONs + this commit
script. Full reproducibility trail for the iteration model.

Per ADR-0044 D3: zero ABI changes. Endpoint response shape
unchanged; behavior fix only. Pre-B196 daemons returned
oldest match; post-B196 return newest match.

Per ADR-0001 D2: no identity surface touched. Smith's
constitution + DNA unchanged. Cycle dispatches recorded in
audit chain via tool_call_succeeded events (seq 2812 v3,
2827 v4, 2842 v4-broken, 2854 v5, 2860 v6).

Verification: 4 passed in 0.83s. See
dev-tools/cycle-1-pytest-output.txt for the green output."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "--- restarting daemon to load conversations.py change ---"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
fi

echo ""
echo "=== Burst 196 commit + push + daemon-restart complete ==="
echo "=== ADR-0056 cycle 1 closed. Most-recent-wins fix is live. ==="
echo "Press any key to close this window."
read -n 1
