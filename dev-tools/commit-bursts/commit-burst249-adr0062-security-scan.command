#!/bin/bash
# Burst 249 — ADR-0062 supply-chain hardening, scanner first.
#
# In direct response to the 2025-26 npm Shai-Hulud worm
# generations (Sep 2025 → Nov 2025 → Feb 2026 → Apr 2026),
# the LiteLLM/Telnyx PyPI compromise (Apr 2026), the
# Axios npm compromise (Apr 2026), and the Anthropic MCP
# STDIO RCE disclosure (Apr 2026, ~200K vulnerable
# servers). Forest's plugin install path, forge engines,
# marketplace install path, and pyproject all sit on the
# same blast surface; this burst lands the first defense.
#
# Files:
#
# 1. docs/decisions/ADR-0062-supply-chain-scanner.md (NEW)
#    7 decisions + 7 tranches. Status: T1+T2+T3 shipping
#    in this burst, T4 (install-time gate) queued for B250,
#    T5 (forge-stage scanner) for B251, T6 (Security tab) for
#    B252. ADR cites every incident with URL.
#
# 2. config/security_iocs.yaml (NEW)
#    16-rule IoC pattern catalog covering:
#      CRITICAL — mcp_stdio_command_injection,
#                 home_dir_wipe_python,
#                 home_dir_wipe_shell,
#                 eval_atob_obfuscation
#      HIGH     — aws_credentials_read, ssh_keys_read,
#                 github_token_pattern, aws_access_key_pattern,
#                 slack_token_pattern,
#                 env_var_enumerate_then_post,
#                 network_beacon_short_lived_domain
#      MEDIUM   — subprocess_shell_true_with_variable,
#                 dangerous_pickle_load,
#                 insecure_yaml_load
#      LOW      — plain_http_url
#      INFO     — unpinned_dependency_pyproject
#    Each rule has: id, severity, pattern, applies_to,
#    rationale, references (incident URLs).
#
# 3. src/forest_soul_forge/tools/builtin/security_scan.py (NEW)
#    SecurityScanTool. Args: scan_kind in
#    {plugins, forged_tools, forged_skills, pyproject, all}
#    + optional scan_paths override + catalog_path override
#    + max_findings cap. Output: findings list (severity,
#    pattern_id, file, line, evidence_excerpt, rationale,
#    references), by_severity totals, scan_fingerprint
#    (sha256 over sorted path list — diff two scans by
#    comparing). side_effects=read_only.
#
#    Safety: symlinks NOT followed (defense against
#    planted symlinks redirecting scanner). Files > 4 MiB
#    skipped (binary blobs / minified vendor dumps).
#    _MAX_FILES_SCANNED=5000 cap. Catalog regex errors
#    surface in catalog_errors output rather than
#    crashing the scan (one bad rule shouldn't kill it).
#
# 4. src/forest_soul_forge/tools/builtin/__init__.py
#    Import + register SecurityScanTool with descriptive
#    comment pointing at ADR-0062.
#
# 5. config/tool_catalog.yaml
#    security_scan.v1 entry with archetype_tags
#    [security_low, guardian, observer] — any LogLurker-tier
#    or higher agent can run it.
#
# 6. tests/unit/test_security_scan.py (NEW)
#    Coverage:
#      - Argument validation (4 cases)
#      - Catalog loader (3 cases: missing file, bad regex,
#        missing required fields)
#      - Path resolution (2 cases: nonexistent paths,
#        symlinks-not-followed)
#      - Each real-catalog pattern hits (9 cases: MCP RCE,
#        home wipe, eval/atob, AWS creds, GitHub PAT,
#        short-lived C2, env-then-post, unpinned pyproject,
#        plain HTTP)
#      - Clean directory returns zero findings
#      - Output shape (by_severity totals, max_findings cap,
#        scan_fingerprint stability + delta)
#
# Smoke verification (sandbox):
#   - Real catalog loads with 16 rules, zero errors.
#   - End-to-end fixture (manifest.yaml + evil.py +
#     pyproject.toml + safe.py) produces 9 findings:
#     3 CRITICAL + 4 HIGH + 0 MEDIUM + 1 LOW + 1 INFO.
#   - Clean fixture (pure adder function) produces zero
#     findings.
#
# Per ADR-0062 D2: scanner is read-only first, gate-second.
#   B249 ships the report path; B250 wires the refuse path.
# Per CLAUDE.md §0 Hippocratic gate: we report findings before
#   we block on them — prove the false-positive rate is
#   acceptable in production before we refuse installs.
# Per ADR-0001 D2: identity surface untouched.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0062-supply-chain-scanner.md \
        config/security_iocs.yaml \
        config/tool_catalog.yaml \
        src/forest_soul_forge/tools/builtin/security_scan.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        tests/unit/test_security_scan.py \
        dev-tools/commit-bursts/commit-burst249-adr0062-security-scan.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0062 supply-chain IoC scanner (B249)

Burst 249. Direct response to the 2025-26 npm Shai-Hulud
worm generations, LiteLLM/Telnyx PyPI compromise, Axios npm
compromise, and Anthropic MCP STDIO RCE disclosure (~200K
vulnerable servers). Forest's plugin install path, forge
engines, marketplace install path, and pyproject sit on the
same blast surface.

ADR-0062 lays out the threat model + 7 decisions + 7 tranches.
T1+T2+T3 ship now; T4 install-time gate queued for B250.

T1: config/security_iocs.yaml — 16-rule IoC catalog with
severity tiers (CRITICAL/HIGH/MEDIUM/LOW/INFO) covering MCP
STDIO command injection, home-dir wipe, eval(atob(...))
obfuscation, AWS/SSH/GitHub/Slack credential harvest,
env-var enumerate-then-post, short-lived C2 beacons,
pickle/yaml.load, plain HTTP, unpinned pyproject deps. Each
rule carries an incident URL.

T2: security_scan.v1 builtin tool. Args: scan_kind +
optional scan_paths/catalog_path/max_findings. Output:
findings + by_severity totals + scan_fingerprint (sha256
over sorted scanned-path list — diff two scans by
comparison). side_effects=read_only. Symlinks NOT followed
(defense against planted symlinks). Per-file 4 MiB cap +
5000-file scan cap.

T3: tool catalog registration + 20+ unit tests covering
validation, catalog-loader resilience, symlink defense,
each real-catalog pattern, output shape, caps, fingerprint
stability.

Smoke fixture (manifest.yaml + evil.py + pyproject.toml +
safe.py) produces 9 findings: 3 CRITICAL + 4 HIGH + 1 LOW +
1 INFO. Clean fixture (pure adder) → zero findings.

Per ADR-0062 D2: report-only in v1 — prove false-positive
rate is acceptable in production before wiring refuse-on-
CRITICAL into /plugins/install (B250).

Per CLAUDE.md Hippocratic gate: report before block."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 249 complete ==="
echo "=== ADR-0062 T1+T2+T3 live. IoC scanner ready for operator use. ==="
echo "Press any key to close."
read -n 1
