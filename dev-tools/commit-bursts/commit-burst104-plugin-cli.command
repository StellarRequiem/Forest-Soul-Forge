#!/usr/bin/env bash
# Burst 104: ADR-0043 T2 — plugin directory + manifest schema + fsf plugin CLI.
#
# First implementation tranche of the MCP-first plugin protocol.
# T2 ships filesystem operations + CLI surface. No daemon-side
# wiring yet — that's T3 (Burst 105). The repository / manifest
# / CLI are testable in isolation, which is why T2 lands first.
#
# WHAT'S NEW
#
# 1. src/forest_soul_forge/plugins/ — new package:
#    - errors.py    — PluginError / PluginNotFound /
#                     PluginAlreadyInstalled / PluginValidationError
#                     mapped to CLI exit codes 4/5/6/7
#    - paths.py     — default_plugin_root() resolves
#                     $FSF_PLUGIN_ROOT or ~/.forest/plugins;
#                     plugin_directories(root) → installed/,
#                     disabled/, secrets/, registry-cache.json
#    - manifest.py  — Pydantic v1-only PluginManifest with strict
#                     extra="forbid" + name/sha256/env_var format
#                     validators; load_manifest(path) reads + parses
#    - repository.py — PluginRepository with list / load /
#                     install_from_dir / uninstall / enable /
#                     disable / verify_binary. Materializes the
#                     directory layout idempotently. Tests don't
#                     need to pre-mkdir anything.
#
# 2. src/forest_soul_forge/cli/plugin_cmd.py — argparse subparser
#    wired into cli/main.py. Operator surface:
#      fsf plugin list [--json]
#      fsf plugin info <name> [--json]
#      fsf plugin install <path> [--force]
#      fsf plugin uninstall <name>
#      fsf plugin enable <name>
#      fsf plugin disable <name>
#      fsf plugin verify <name>
#    All take --plugin-root for test/operator-override flexibility.
#    Exit codes match errors.py: 4 not-found / 5 already-installed
#    / 6 validation / 7 catch-all / 1 verify-mismatch. JSON output
#    on list + info for scripting.
#
# 3. tests +51 across three new files:
#    - test_plugins_manifest.py: 17 schema tests (acceptance,
#      rejection paths, load_manifest from disk)
#    - test_plugins_repository.py: 24 filesystem tests (layout,
#      install/list/load/enable/disable/uninstall round-trips,
#      force-overwrite semantics, verify_binary match + tamper +
#      missing-file)
#    - test_plugins_cli.py: 10 smoke tests covering argparse
#      dispatch + exit codes + JSON output + the install →
#      list → info flow
#
# WHAT THIS BURST DOES NOT DO
#
# - Daemon-side hot-reload. T3 (Burst 105) wires the repository
#   into the running daemon's tool catalog so newly-installed
#   plugins register without restart. Today: install + restart
#   daemon to pick up the change.
# - Audit-chain integration. T4 (Burst 106) emits the 6 plugin_*
#   events from the lifecycle code.
# - Registry-from-Git install. T5 (Burst 107) bootstraps the
#   forest-plugins repo + sparse-checkout install. Today: install
#   from a local directory only.
# - secrets subcommand. Deferred to a follow-up burst once the
#   first real plugin needs it; the agent_secrets store from
#   ADR-003X K1 already exists.
# - reload subcommand. Bound to the daemon hot-reload story; T3.
#
# DESIGN NOTES
#
# Why module-top-level import of errors in manifest.py: a local
# import inside load_manifest() created class-identity issues
# across the full pytest suite (PluginValidationError raised by
# the local import didn't isinstance-match the test's import).
# Top-level import is the standard fix; CLAUDE.md's pattern
# discipline note worth recording.
#
# Why force=True overwrites disabled-plugins too: an operator
# reinstalling a plugin shouldn't get blocked because the prior
# install was paused. Disabled is part of the same trust surface
# as installed.
#
# Why list() silently skips invalid manifests: one bad plugin
# shouldn't block the whole listing. T4's verify subcommand
# explicitly reports failures; list is the happy-path read.
#
# Why exit 1 (not 6) on verify mismatch: 6 = validation error
# (manifest itself broken). Mismatch means the manifest is fine
# but the binary doesn't match its pin — different failure
# class. Exit 1 = generic-failure aligns with shell tools (cmp,
# diff, etc).
#
# VERIFICATION
#
# Sandbox: PYTHONPATH=src python3 -m pytest tests/unit
#   → 2228 passed, 3 skipped, 1 xfailed (was 2177 + 51 plugin
#   tests; zero regressions in non-plugin code)
#
# Host (operator):
#   1. mkdir -p /tmp/test-plugin
#   2. cd /tmp/test-plugin && create plugin.yaml + a fake server
#   3. fsf plugin install /tmp/test-plugin
#   4. fsf plugin list
#   5. fsf plugin info <name>
#   6. fsf plugin verify <name>
#   7. fsf plugin disable <name> && fsf plugin enable <name>
#   8. fsf plugin uninstall <name>

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 104 — ADR-0043 T2: plugin directory + manifest + CLI ==="
echo
clean_locks
git add src/forest_soul_forge/plugins/
git add src/forest_soul_forge/cli/plugin_cmd.py
git add src/forest_soul_forge/cli/main.py
git add tests/unit/test_plugins_manifest.py
git add tests/unit/test_plugins_repository.py
git add tests/unit/test_plugins_cli.py
git add commit-burst104-plugin-cli.command
clean_locks
git status --short
echo
clean_locks
git commit -m "feat(plugins): plugin directory + manifest + fsf plugin CLI (ADR-0043 T2)

First implementation tranche of the MCP-first plugin protocol.
Filesystem operations + CLI surface. No daemon-side wiring yet
(T3 / Burst 105 lands hot-reload).

New package src/forest_soul_forge/plugins/:
- errors.py: typed exception hierarchy (PluginError +
  PluginNotFound + PluginAlreadyInstalled +
  PluginValidationError); CLI maps to exit codes 4/5/6/7
- paths.py: default_plugin_root() resolves \$FSF_PLUGIN_ROOT or
  ~/.forest/plugins; plugin_directories(root) returns installed/
  + disabled/ + secrets/ + registry-cache.json paths
- manifest.py: Pydantic PluginManifest with extra='forbid' +
  format validators (name shape, sha256 hex, env_var SHOUTY_SNAKE);
  load_manifest(path) reads + parses with PluginValidationError
  on any structural failure
- repository.py: PluginRepository with list / load /
  install_from_dir / uninstall / enable / disable / verify_binary.
  Idempotent layout materialization on construction. Force flag
  overwrites both installed/ AND disabled/ targets (an operator
  reinstalling shouldn't get blocked by a prior pause).

New CLI subparser src/forest_soul_forge/cli/plugin_cmd.py wired
into cli/main.py:
  fsf plugin list [--json]
  fsf plugin info <name> [--json]
  fsf plugin install <path> [--force]
  fsf plugin uninstall <name>
  fsf plugin enable <name>
  fsf plugin disable <name>
  fsf plugin verify <name>

All accept --plugin-root for tests + advanced operators. Exit
codes per errors.py: 4 not-found / 5 already-installed / 6
validation / 7 catch-all / 1 verify-mismatch.

Tests +51:
- test_plugins_manifest.py (17): schema acceptance, rejection
  paths (unknown keys, bad name, short sha256, lowercase
  env_var, unknown schema_version/type/side_effects), and
  load_manifest disk paths
- test_plugins_repository.py (24): layout idempotence,
  install/list/load/enable/disable/uninstall round-trips,
  force-overwrite semantics including disabled-plugin path,
  verify_binary match + tamper-detection + missing-file
- test_plugins_cli.py (10): argparse dispatch, exit-code
  contracts, JSON output, install → list → info flow

Verification: 2177 → 2228 unit tests pass (+51 plugin tests).
Zero regressions in non-plugin code. The module-top-level
import of errors in manifest.py (vs a local import) avoids a
pytest-suite-wide class-identity issue that would otherwise
break PluginValidationError matching across files.

Outstanding ADR-0043 work:
- T3 / Burst 105: daemon hot-reload + /plugins HTTP endpoints
- T4 / Burst 106: audit-chain integration; 6 plugin_* events
- T5 / Burst 107: registry repo bootstrap with canonical
  plugins (filesystem, github, postgres, brave-search, slack)"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 104 landed. fsf plugin CLI is real."
echo "Test it: \`fsf plugin list --plugin-root /tmp/test\`"
echo "Next: Burst 105 — T3 daemon hot-reload + /plugins HTTP endpoints."
echo ""
read -rp "Press Enter to close..."
