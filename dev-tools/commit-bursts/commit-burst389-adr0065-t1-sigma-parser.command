#!/bin/bash
# Burst 389 - ADR-0065 T1: Sigma-subset parser + DetectionRule
# dataclass + evaluate() + tests.
#
# Opens the D3 Phase C arc. T2 lands the DetectionEngine + scan()
# integration into AdapterIngestor; T3 adds detection_engineer
# role; T4 wires the harness; T5 ships runbook + starter rules;
# T6 closes.
#
# What lands:
#
#   src/forest_soul_forge/security/detection/__init__.py (NEW)
#     Package surface. Re-exports DetectionRule, DetectionMatch,
#     DetectionRuleError, parse_rule, parse_rules_from_dir.
#
#   src/forest_soul_forge/security/detection/events.py (NEW)
#     DetectionRule (frozen dataclass) - rule_id + title +
#       description + rule_version (sha256 hex) + level + tags +
#       logsource_source/event_type (optional) + selections +
#       condition. __post_init__ validates level + tag-required
#       + non-empty selections + non-empty condition.
#     DetectionRule.applies_to(source, event_type) - cheap
#       rejection by logsource.
#     DetectionRule.evaluate(event_id, source, event_type,
#       payload) - returns DetectionMatch | None. Selection
#       eval is field/value equality with dotted-path lookup
#       (e.g. process.image -> payload["process"]["image"]).
#       Condition eval is recursive-descent over and/or/not/
#       parentheses/identifier; refuses anything else.
#     DetectionMatch (frozen dataclass) - rule_id +
#       rule_version + event_id + technique (first tag) +
#       level + matched_selections.
#     rule_version_hash(body) - sha256 hex helper; the parser
#       canonicalizes via yaml.safe_dump(sort_keys=True) before
#       hashing so whitespace/key-order changes don't flip the
#       version.
#
#   src/forest_soul_forge/security/detection/parser.py (NEW)
#     parse_rule(body, source_path=...) - Sigma-subset YAML parser.
#       Required: id (or rule_id/name), level, tags (non-empty
#         per ADR-0065 D3), detection.condition, at least one
#         selection.
#       Optional: title, description, logsource.source/event_type.
#       Rejects:
#         - field modifiers (`fieldname|contains: ...`) — equality-
#           only subset
#         - timeframe (time-windowed correlation deferred)
#         - non-string tags / level / id / etc.
#         - duplicate rule_ids across a directory (via
#           parse_rules_from_dir)
#     parse_rules_from_dir(dir) - returns (parsed, failed) so
#       the caller can surface ALL failures as a punch list.
#       Per ADR-0065 D7 the daemon's engine refuses to run if any
#       rule fails; returning failures lets the lifespan caller
#       report + halt rather than crash on the first bad rule.
#
#   tests/unit/test_b389_detection_parser.py (NEW)
#     21 tests:
#       Happy-path parse + 8 rejection paths (missing id, tags,
#         condition, selections; invalid level; unsupported
#         modifier; timeframe).
#       evaluate() over: simple selection (match + no-match),
#         logsource mismatch (cheap skip), or-condition, and-not-
#         condition, unknown selection in condition (raises),
#         unsupported syntax (==) raises.
#       rule_version: stable under whitespace + key-order changes;
#         changes when selection value changes; hash helper
#         matches sha256 directly.
#       parse_rules_from_dir: aggregates pass + fail; missing
#         directory returns empty pair.
#     All 21 pass in 0.10s.
#
# What this does NOT do:
#   - No engine. parser produces rules; engine consumes them. T2.
#   - No detection_engineer role. T3.
#   - No harness wiring. T4.
#   - No runbook + starter rule library. T5.
#   - No daemon-side hot-reload endpoint. T2 ships
#     POST /detections/reload alongside the engine.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T1: ADR-0065 is proposed but no
#     code; D3 Phase C is paper-only. T1 is the smallest unit
#     that creates a real surface for the engine + role + harness
#     bursts to build against.
#   Prove non-load-bearing: pure additive package. No daemon
#     code touched (yet); no schema bump; no test failures
#     elsewhere.
#   Prove alternative is strictly better:
#     - Bundle T1+T2 in one burst: ~2x the diff size + the
#       parser's parse-rejection contract gets stress-tested
#       only via the engine path. Separate T1 lets the parser
#       contract land standalone + unit-tested before runtime
#       integration.
#     - Skip parser tests: parser is the rule-quality gatekeeper
#       per ADR-0065 D7. Untested parser = silent rule drift.
#
# CLAUDE.md sec2 + sec3 check:
#   No new dispatcher subsystem. No new builtin tool with
#   _VERSION. Pure package addition. sec2/sec3 don't apply.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b389_detection_parser.py
#      Expected: 21 passed.
#   2. No daemon restart needed (no daemon code touched).
#   3. T2 (DetectionEngine + scan integration) is the next burst.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/detection/__init__.py \
        src/forest_soul_forge/security/detection/events.py \
        src/forest_soul_forge/security/detection/parser.py \
        tests/unit/test_b389_detection_parser.py \
        dev-tools/commit-bursts/commit-burst389-adr0065-t1-sigma-parser.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(detection): ADR-0065 T1 Sigma-subset parser (B389)

Burst 389. Opens D3 Phase C. Sigma-subset YAML parser +
DetectionRule dataclass with evaluate(). 21 tests pass.

src/forest_soul_forge/security/detection/ (NEW package):
  events.py:
    DetectionRule (frozen) - rule_id, title, description,
      rule_version (sha256 hex, canonicalized YAML), level,
      tags (>=1 mandatory per ADR-0065 D3), logsource fields,
      selections, condition.
    DetectionRule.evaluate(event_id, source, type, payload) ->
      DetectionMatch | None. Selection eval: field/value
      equality with dotted-path lookup. Condition eval:
      recursive-descent over and/or/not/parens/identifier; any
      other syntax raises with operator-readable message.
    DetectionMatch (frozen) - rule_id, rule_version, event_id,
      technique (first tag), level, matched_selections.
    rule_version_hash(body) - sha256 hex over canonical YAML
      so whitespace/key-order changes don't flip the version
      (load-bearing for ADR-0065 D5: chain history pins exact
      rule).
  parser.py:
    parse_rule(body) - parses + validates the subset. Rejects:
      field modifiers (equality-only T1), timeframe (correlation
      deferred), missing required keys, invalid level, empty
      tags/selections/condition.
    parse_rules_from_dir(dir) -> (parsed, failed). Aggregates
      failures so the caller (engine/harness) surfaces a punch
      list rather than crashing on the first bad rule
      (ADR-0065 D7).

Tests (21):
  Happy-path + 8 rejection paths + 7 evaluate() cases (match,
  no-match, logsource mismatch, or-condition, and-not, unknown
  selection, unsupported syntax) + 3 rule_version stability +
  parse_rules_from_dir aggregation + missing dir.

What this does NOT do:
  No engine, no role, no harness wiring, no runbook. Those land
  in T2-T5.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: ADR-0065 is paper-only without T1.
  Prove non-load-bearing: pure package addition; no daemon
    code touched; no schema bump.
  Prove alternative is better: separating parser from engine
    lets the parser-rejection contract land standalone +
    unit-tested before runtime integration risk arrives.

After this lands: T2 (DetectionEngine + scan() integration into
AdapterIngestor) is the next burst."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 389 complete - Sigma-subset parser ==="
echo "=========================================================="
echo "Re-test:"
echo "  PYTHONPATH=src python3 -m pytest tests/unit/test_b389_detection_parser.py"
echo "Expected: 21 passed"
echo ""
echo "Press any key to close."
read -n 1 || true
