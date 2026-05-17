#!/bin/bash
# Burst 353 - two fixes surfaced by section 03 boot-health probe
# against the live daemon after restart.
#
# Fix 1 (real substrate bug): operator_profile_read.v1 +
# operator_profile_write.v1 declared their _VERSION as "v1"
# while every other builtin tool declares it as "1". The
# registry's key composer at tools/base.py:273 does
# f"{name}.v{version}" — passing already-prefixed "v1" produced
# "operator_profile_read.vv1" + "operator_profile_write.vv1"
# in the registry while tool_catalog.yaml has the
# correctly-versioned ".v1" entries. The tool_runtime
# startup_diagnostic flagged this as a registry/catalog mismatch
# but the daemon stayed degraded silently (B350-class: claimed
# state diverged from actual; no test caught it).
#
# Note: the read/write tools ARE dispatchable today because
# the catalog ALSO has them at .v1 (loaded from yaml). What
# breaks is the registry's own integrity check. Tools registered
# under .vv1 keys can't be looked up by their catalog name from
# the dispatcher path that uses catalog-side resolution. Path-
# specific consumers (operator_profile_write.v1 skill steps)
# may also fail when they reach for the registry-keyed shape.
#
# Fix 2 (harness noise filter, ADR-0079 follow-up): section 03
# boot-health's first real run against the live daemon returned
# 5 non-ok startup_diagnostic entries — but 4 were intentional
# opt-in defaults (priv_client, encryption_at_rest,
# personal_index, wake_word all flagged as disabled/off). Only
# 1 entry (tool_runtime) was actually a failure. Section 03's
# bad-check predicate was too strict (anything != "ok" =
# FAIL); the noise hid the real signal.
#
# Updated predicate splits statuses into three categories:
#   INFO_STATUSES = {disabled, off, skipped, not_configured, n/a}
#   BAD_STATUSES  = {failed, error, degraded, broken}
#   ok            = pass
# Unknown statuses still treated as bad (surfaces vocabulary
# drift). The report's FAIL line now includes the actual error
# string per bad entry so operators can act without a separate
# probe; informational entries get a dedicated INFO line so
# they're visible but not counted as bad.
#
# What ships (4 files):
#
# 1. src/forest_soul_forge/tools/builtin/operator_profile_read.py:
#    _VERSION "v1" → "1" + comment referencing B353.
#
# 2. src/forest_soul_forge/tools/builtin/operator_profile_write.py:
#    same.
#
# 3. dev-tools/diagnostic/section-03-boot-health.command:
#    Tri-state filter + per-bad-entry error rendering + INFO
#    line for informational entries.
#
# 4. dev-tools/diagnostic/probe-tool-runtime.command (NEW):
#    Operator-runnable probe that fetches /healthz and dumps
#    the tool_runtime block. Useful for ANY future failure of
#    the same shape — surface registry/catalog mismatch without
#    needing to spelunk the daemon logs.
#
# After this commit + a daemon restart, the live daemon's
# /healthz should report tool_runtime status=ok and the four
# informational opt-outs as such. Section 03's second run will
# turn green (real signal only).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/operator_profile_read.py \
        src/forest_soul_forge/tools/builtin/operator_profile_write.py \
        dev-tools/diagnostic/section-03-boot-health.command \
        dev-tools/diagnostic/probe-tool-runtime.command \
        dev-tools/commit-bursts/commit-burst353-fix-vv1-and-section03-noise.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(tools+harness): vv1 registry typo + section 03 noise filter (B353)

Burst 353. Two fixes surfaced by section 03 boot-health probe
against the live daemon after restart.

Fix 1 (real substrate bug):
  operator_profile_read.v1 + operator_profile_write.v1 declared
  _VERSION as \"v1\" while every other builtin tool declares it
  as \"1\". The registrys key composer at tools/base.py:273 does
  f-string with v-prefix - passing already-prefixed \"v1\"
  produced operator_profile_read.vv1 + operator_profile_write.vv1
  in the registry, while tool_catalog.yaml has the correctly-
  versioned .v1 entries. The tool_runtime startup_diagnostic
  flagged this as a registry/catalog mismatch but the daemon
  stayed degraded silently (B350-class: claimed state diverged
  from actual; no unit test caught it).

  Fix: _VERSION \"v1\" -> \"1\" in both files. Inline comments
  reference B353 so future drift is detectable.

Fix 2 (ADR-0079 harness noise filter):
  Section 03 boot-healths first real run against the live daemon
  flagged 5 non-ok startup_diagnostic entries but 4 were
  intentional opt-in defaults (priv_client, encryption_at_rest,
  personal_index, wake_word). Only tool_runtime was actually a
  failure. The bad-check predicate was too strict (anything !=
  \"ok\" = FAIL); the noise hid the real signal.

  Updated predicate splits statuses into three categories:
    INFO_STATUSES = {disabled, off, skipped, not_configured, n/a}
    BAD_STATUSES  = {failed, error, degraded, broken}
    ok            = pass
  Unknown statuses still treated as bad (surfaces vocabulary
  drift). FAIL line includes the actual error string per bad
  entry; INFO line surfaces opt-outs without counting them bad.

Also ships:
  dev-tools/diagnostic/probe-tool-runtime.command (NEW)
  Operator-runnable probe that fetches /healthz and dumps the
  tool_runtime block. Reusable for future failures of the same
  shape - surfaces registry/catalog mismatch without needing to
  spelunk daemon logs.

After this commit + a daemon restart, /healthz should report
tool_runtime status=ok and the four opt-outs as informational.
Section 03s second run should turn green (real signal only)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 353 complete - vv1 typo + section 03 noise filter shipped ==="
echo "Restart daemon (force-restart-daemon.command) then re-run"
echo "section-03-boot-health.command to confirm tool_runtime turns ok."
echo ""
echo "Press any key to close."
read -n 1 || true
