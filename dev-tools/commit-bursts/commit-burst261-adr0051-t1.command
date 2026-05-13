#!/bin/bash
# Burst 261 — ADR-0051 T1: per-tool subprocess sandbox abstraction +
# macOS sandbox-exec implementation.
#
# Lands the FIRST tranche of the per-tool subprocess sandbox arc
# (ADR-0051, the last of the four Phase-4 hardening ADRs).
# Substrate is INERT by default — FSF_TOOL_SANDBOX=off is the
# default, and even when this module is imported no tool dispatch
# path changes until T4 wires it in. T2 adds the Linux bwrap impl.
#
# === T1 scope ===
#
# T1.1 — Add ``sandbox_eligible: bool`` field to ToolDef + catalog
#        parser. Default True (additive schema posture — pre-ADR
#        entries keep loading without annotation). False marks
#        tools that structurally can't run in a subprocess
#        (memory_*, delegate, llm_think need direct daemon-state
#        handles).
#
# T1.2 — New ``SerializableToolContext`` (sandbox_context.py).
#        Frozen dataclass projecting the pickle-safe subset of
#        ToolContext: instance_id, agent_dna, role, genre,
#        session_id, JSON-safe constraints. Drops the seven
#        live-handle fields (provider, logger, memory, delegate,
#        priv_client, secrets, agent_registry,
#        procedural_shortcuts). Round-trip-safe through pickle.
#
# T1.3 — New ``tools/sandbox.py`` with SandboxProfile dataclass,
#        SandboxResult dataclass, build_profile() per ADR Decision
#        4, Sandbox Protocol, MacOSSandboxExec implementation
#        wrapping /usr/bin/sandbox-exec with .sb profile
#        generation, default_sandbox() platform sniff.
#
# T1.4 — New ``tools/_sandbox_worker.py`` — subprocess entrypoint.
#        Reads pickled (tool_module, tool_class, args, ctx) from
#        stdin, imports + instantiates + runs the tool inside the
#        sandbox, writes pickled SandboxResult to stdout. Error
#        classification: setup_failed | timeout | sandbox_violation
#        | tool_error | unexpected.
#
# T1.5 — Tests:
#        - new tests/unit/test_tool_sandbox.py (~13 cases):
#          SerializableToolContext filter + drop + rehydrate +
#          pickle round-trip; build_profile mapping for each
#          side_effects; .sb profile text shape (deny default,
#          system reads, path injection defense); default_sandbox
#          platform sniff; SandboxProfile/Result frozen
#          invariants; Protocol conformance; darwin-gated
#          end-to-end ``setup_failed`` smoke.
#        - extended tests/unit/test_tool_catalog.py with
#          TestSandboxEligible class (4 cases): default True,
#          explicit false, explicit true, non-bool rejected.
#
# === Files touched ===
#
#   src/forest_soul_forge/core/tool_catalog.py      (edit: ToolDef + _parse_tool_entry)
#   src/forest_soul_forge/tools/sandbox_context.py  (NEW)
#   src/forest_soul_forge/tools/sandbox.py          (NEW)
#   src/forest_soul_forge/tools/_sandbox_worker.py  (NEW)
#   tests/unit/test_tool_sandbox.py                 (NEW)
#   tests/unit/test_tool_catalog.py                 (edit: add TestSandboxEligible)
#
# === Out of scope for T1 ===
#
# - Dispatcher integration (T4 — does NOT touch dispatcher.py)
# - Linux bwrap implementation (T2)
# - Catalog yaml annotations marking memory_*/delegate/llm_think
#   as sandbox_eligible: false (T3)
# - Audit chain event_data sandbox_* fields (T6)
# - Permissive-mode fallback in dispatcher (T7)
# - Runbook (T8)
#
# === Tests expected ===
#
# pre-burst: 145 passing
# post-burst: 145 + 13 new (test_tool_sandbox) + 4 new
#             (test_tool_catalog::TestSandboxEligible) = 162

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/tool_catalog.py \
        src/forest_soul_forge/tools/sandbox_context.py \
        src/forest_soul_forge/tools/sandbox.py \
        src/forest_soul_forge/tools/_sandbox_worker.py \
        tests/unit/test_tool_sandbox.py \
        tests/unit/test_tool_catalog.py \
        dev-tools/commit-bursts/commit-burst261-adr0051-t1.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0051 T1 — per-tool sandbox abstraction (B261)

Burst 261. First tranche of ADR-0051 (per-tool subprocess sandbox
— the last of the four Phase-4 hardening ADRs). Lands the
substrate; dispatcher integration is T4. Default FSF_TOOL_SANDBOX=
off preserves bit-identical existing behavior.

What's in T1:

T1.1 — ToolDef.sandbox_eligible: bool field + catalog parser
       (additive schema; default True keeps pre-ADR entries
       loading without annotation; explicit False opt-out for
       tools that can't pickle their ToolContext —
       memory_recall/_write/_disclose, delegate, llm_think).

T1.2 — SerializableToolContext (tools/sandbox_context.py).
       Pickle-safe projection of ToolContext: keeps instance_id,
       agent_dna, role, genre, session_id, filtered constraints.
       Drops provider, logger, memory, delegate, priv_client,
       secrets, agent_registry, procedural_shortcuts (live
       daemon-state handles that don't survive a subprocess).
       Frozen dataclass; round-trip-safe.

T1.3 — Sandbox Protocol + MacOSSandboxExec (tools/sandbox.py).
       SandboxProfile + SandboxResult dataclasses; build_profile()
       implements the ADR Decision 4 mapping (read_only/network/
       filesystem/external → allowed_read_paths +
       allowed_write_paths + allow_network + allowed_hosts +
       allowed_commands). MacOSSandboxExec generates a .sb profile
       (sandbox-exec TinyScheme syntax: deny-default + system read
       paths Python needs to even start + tool's allowlists),
       spawns subprocess via /usr/bin/sandbox-exec, classifies
       failure as setup_failed | timeout | sandbox_violation |
       unexpected. default_sandbox() returns MacOSSandboxExec on
       darwin (when /usr/bin/sandbox-exec present) else None.

T1.4 — Sandbox worker entrypoint (tools/_sandbox_worker.py).
       Subprocess module invoked as 'python -I -m
       forest_soul_forge.tools._sandbox_worker'. Reads pickled
       invocation from stdin, imports + instantiates the tool,
       rehydrates ToolContext via SerializableToolContext.
       to_tool_context() (None for the seven live-handle
       fields), runs via asyncio.run, pickles a SandboxResult
       to stdout. Error mapping: ImportError/AttributeError →
       setup_failed; ToolError → tool_error (in-band); anything
       else → unexpected.

T1.5 — Tests:
       tests/unit/test_tool_sandbox.py (13 cases) covers the
       Serializable projection, profile builder mapping, .sb
       text shape (deny default, system reads always present,
       path-injection defense via _quote_sb_path rejecting
       quotes), platform sniff (darwin/linux/win32), dataclass
       frozen invariants, Protocol conformance, plus a darwin-
       gated end-to-end smoke (setup_failed when sandbox-exec
       binary path is monkeypatched to nonexistent).
       tests/unit/test_tool_catalog.py extended with
       TestSandboxEligible (4 cases): default True absent,
       explicit false, explicit true, non-bool string 'yes'
       rejected.

What T1 does NOT do (deliberate scoping per ADR Decision 1):

- Dispatcher integration. T4 reads FSF_TOOL_SANDBOX env, looks
  up sandbox_eligible, decides in-process vs sandbox, and
  emits sandbox_* audit annotations. T1 ships the substrate
  without touching dispatcher.py.
- Linux bwrap. T2's responsibility. default_sandbox() returns
  None on Linux today.
- Catalog YAML annotations marking memory_*/delegate/llm_think
  as sandbox_eligible: false. T3 ships those alongside the
  field-honoring dispatcher.
- Audit chain event_data fields (sandbox_mode, sandbox_used,
  sandbox_violation). T6.
- Permissive-mode fallback. T7.
- Runbook. T8.

Verification: pytest pre-burst 145 passing; post-burst expected
145 + 17 new = 162 (13 sandbox + 4 sandbox_eligible). Will
verify post-push via diag-session-tests."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 261 complete — ADR-0051 T1 shipped (substrate inert until T4) ==="
echo "Press any key to close."
read -n 1
