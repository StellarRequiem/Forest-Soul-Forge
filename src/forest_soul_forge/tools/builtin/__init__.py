"""Built-in tool implementations — ADR-0019 T1.

These ship with the daemon and are registered at lifespan. They mirror
catalog entries one-for-one. Operator-installed tools live in
``~/.fsf/plugins/`` and get loaded by the .fsf plugin loader (T5).

T1 ships ``timestamp_window.v1`` only — pure function, no I/O, perfect
proving ground for the Tool Protocol contract. Other catalog entries
will get implementations as the corresponding tranches land.
"""
from forest_soul_forge.tools.builtin.anomaly_score import AnomalyScoreTool
from forest_soul_forge.tools.builtin.audit_chain_verify import AuditChainVerifyTool
from forest_soul_forge.tools.builtin.behavioral_baseline import BehavioralBaselineTool
from forest_soul_forge.tools.builtin.canary_token import CanaryTokenTool
from forest_soul_forge.tools.builtin.continuous_verify import ContinuousVerifyTool
from forest_soul_forge.tools.builtin.delegate import DelegateTool
from forest_soul_forge.tools.builtin.dns_lookup import DnsLookupTool
from forest_soul_forge.tools.builtin.dynamic_policy import DynamicPolicyTool
from forest_soul_forge.tools.builtin.evidence_collect import EvidenceCollectTool
from forest_soul_forge.tools.builtin.git_blame_read import GitBlameReadTool
from forest_soul_forge.tools.builtin.git_diff_read import GitDiffReadTool
from forest_soul_forge.tools.builtin.git_log_read import GitLogReadTool
from forest_soul_forge.tools.builtin.file_integrity import FileIntegrityTool
from forest_soul_forge.tools.builtin.honeypot_local import HoneypotLocalTool
from forest_soul_forge.tools.builtin.isolate_process import IsolateProcessTool
from forest_soul_forge.tools.builtin.jit_access import JitAccessTool
from forest_soul_forge.tools.builtin.key_inventory import KeyInventoryTool
from forest_soul_forge.tools.builtin.lateral_movement_detect import LateralMovementDetectTool
from forest_soul_forge.tools.builtin.log_aggregate import LogAggregateTool
from forest_soul_forge.tools.builtin.log_correlate import LogCorrelateTool
from forest_soul_forge.tools.builtin.log_scan import LogScanTool
from forest_soul_forge.tools.builtin.memory_challenge import MemoryChallengeTool
from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from forest_soul_forge.tools.builtin.memory_write import MemoryWriteTool
from forest_soul_forge.tools.builtin.mypy_typecheck import MypyTypecheckTool
from forest_soul_forge.tools.builtin.patch_check import PatchCheckTool
from forest_soul_forge.tools.builtin.port_policy_audit import PortPolicyAuditTool
from forest_soul_forge.tools.builtin.port_scan_local import PortScanLocalTool
from forest_soul_forge.tools.builtin.posture_check import PostureCheckTool
from forest_soul_forge.tools.builtin.pytest_run import PytestRunTool
from forest_soul_forge.tools.builtin.ruff_lint import RuffLintTool
from forest_soul_forge.tools.builtin.semgrep_scan import SemgrepScanTool
from forest_soul_forge.tools.builtin.software_inventory import SoftwareInventoryTool
from forest_soul_forge.tools.builtin.tamper_detect import TamperDetectTool
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
from forest_soul_forge.tools.builtin.traffic_flow_local import TrafficFlowLocalTool
from forest_soul_forge.tools.builtin.tree_sitter_query import TreeSitterQueryTool
from forest_soul_forge.tools.builtin.triage import TriageTool
from forest_soul_forge.tools.builtin.ueba_track import UebaTrackTool
from forest_soul_forge.tools.builtin.browser_action import BrowserActionTool
from forest_soul_forge.tools.builtin.mcp_call import McpCallTool
from forest_soul_forge.tools.builtin.memory_verify import MemoryVerifyTool
from forest_soul_forge.tools.builtin.code_edit import CodeEditTool
from forest_soul_forge.tools.builtin.code_read import CodeReadTool
from forest_soul_forge.tools.builtin.llm_think import LlmThinkTool
from forest_soul_forge.tools.builtin.shell_exec import ShellExecTool
from forest_soul_forge.tools.builtin.suggest_agent import SuggestAgentTool
from forest_soul_forge.tools.builtin.usb_device_audit import UsbDeviceAuditTool
from forest_soul_forge.tools.builtin.web_fetch import WebFetchTool

__all__ = [
    "TimestampWindowTool",
    "MemoryRecallTool",
    "MemoryWriteTool",
    "MemoryDiscloseTool",
    "MemoryChallengeTool",
    "DelegateTool",
    "AuditChainVerifyTool",
    "FileIntegrityTool",
    "LogScanTool",
    "LogAggregateTool",
    "PatchCheckTool",
    "SoftwareInventoryTool",
    "PortPolicyAuditTool",
    "UsbDeviceAuditTool",
    "BehavioralBaselineTool",
    "RuffLintTool",
    "PytestRunTool",
    "GitLogReadTool",
    "GitDiffReadTool",
    "GitBlameReadTool",
    "MypyTypecheckTool",
    "SemgrepScanTool",
    "TreeSitterQueryTool",
    "AnomalyScoreTool",
    "LogCorrelateTool",
    "LateralMovementDetectTool",
    "UebaTrackTool",
    "PortScanLocalTool",
    "TrafficFlowLocalTool",
    "EvidenceCollectTool",
    "TriageTool",
    "IsolateProcessTool",
    "PostureCheckTool",
    "ContinuousVerifyTool",
    "JitAccessTool",
    "KeyInventoryTool",
    "DynamicPolicyTool",
    "TamperDetectTool",
    "CanaryTokenTool",
    "HoneypotLocalTool",
    "WebFetchTool",
    "MemoryVerifyTool",
    "BrowserActionTool",
    "McpCallTool",
    "SuggestAgentTool",
    "LlmThinkTool",
    "CodeReadTool",
    "CodeEditTool",
    "ShellExecTool",
    "DnsLookupTool",
]


def register_builtins(registry) -> None:  # noqa: ANN001 — circular import dance
    """Register every built-in tool into the supplied registry.

    Called from daemon lifespan. Centralizing the registration here
    means adding a new built-in is one line in one file rather than
    a hunt across the lifespan code.
    """
    registry.register(TimestampWindowTool())
    registry.register(MemoryRecallTool())
    registry.register(MemoryWriteTool())
    registry.register(MemoryDiscloseTool())
    registry.register(DelegateTool())
    # ADR-0018 archetype kits — DNS resolution. Implemented 2026-04-30
    # under C-1 zombie-tool dissection (was specced in ADR-0018 but
    # never landed; verdict: IMPLEMENT — foundational primitive, no
    # substitute, network_watcher kit needs it).
    registry.register(DnsLookupTool())
    # ADR-0033 Phase B1 — security_low pure-python tools.
    registry.register(AuditChainVerifyTool())
    registry.register(FileIntegrityTool())
    registry.register(LogScanTool())
    registry.register(LogAggregateTool())
    # ADR-0033 Phase B1 — security_low OS-shellout tools.
    registry.register(PatchCheckTool())
    registry.register(SoftwareInventoryTool())
    registry.register(PortPolicyAuditTool())
    registry.register(UsbDeviceAuditTool())
    # ADR-0033 Phase B2 — security_mid pure-python analytics.
    registry.register(BehavioralBaselineTool())
    registry.register(AnomalyScoreTool())
    registry.register(LogCorrelateTool())
    registry.register(LateralMovementDetectTool())
    # ADR-0033 Phase B2 — security_mid telemetry + forensics.
    registry.register(UebaTrackTool())
    registry.register(PortScanLocalTool())
    registry.register(TrafficFlowLocalTool())
    registry.register(EvidenceCollectTool())
    # ADR-0033 Phase B2 — security_mid LLM-driven + privileged.
    registry.register(TriageTool())
    registry.register(IsolateProcessTool())
    # ADR-0033 Phase B3 — security_high posture + verification.
    registry.register(PostureCheckTool())
    registry.register(ContinuousVerifyTool())
    # ADR-0033 Phase B3 — security_high access + key inventory.
    registry.register(JitAccessTool())
    registry.register(KeyInventoryTool())
    # ADR-0033 Phase B3 — security_high privileged (PrivClient).
    registry.register(DynamicPolicyTool())
    registry.register(TamperDetectTool())
    # ADR-0033 Phase B3 — security_high deception layer.
    registry.register(CanaryTokenTool())
    registry.register(HoneypotLocalTool())
    # ADR-003X Phase C2 — open-web fetch primitive. Per-agent host
    # allowlist + optional secrets-store auth. The cheapest path off
    # 127.0.0.1 for an agent that needs to read a public API or pull
    # an RFC. Side effects: network. Approval gating is up to the
    # agent's constitution; the host allowlist IS the structural gate.
    registry.register(WebFetchTool())
    # ADR-003X Phase K1 — verified-memory tier (Iron Gate equivalent).
    # External human verifier promotes a memory entry to verified
    # status. Reuses memory_consents table via 'operator:verified'
    # sentinel — no schema bump.
    registry.register(MemoryVerifyTool())
    # ADR-0027-amendment §7.4 — memory_challenge.v1. Operator-only at
    # v0.2: stamps last_challenged_at on an entry to record explicit
    # operator scrutiny without writing a competing entry. Surfaces
    # through memory_recall.v1's staleness flag.
    registry.register(MemoryChallengeTool())
    # ADR-003X Phase C3 — browser_action.v1. Drives a chromium browser
    # via Playwright. Heaviest open-web primitive; always gated
    # (side_effects=external triggers requires_human_approval). Lazy
    # playwright import so daemon boots without the browser extra.
    registry.register(BrowserActionTool())
    # ADR-003X Phase C4 — mcp_call.v1. Calls an operator-registered
    # MCP server via stdio JSON-RPC. SHA256 verification of the binary
    # before each launch defends against typosquat / supply-chain swap.
    # Per-agent allowed_mcp_servers list + per-server allowlisted_tools
    # list keep the dispatch surface tight.
    registry.register(McpCallTool())
    # ADR-003X Phase C6 — suggest_agent.v1. Operator-facing agent
    # matcher. BM25 over (role + agent_name + genre) returns top-K
    # ranked candidates. Reads from ctx.agent_registry; refuses
    # cleanly when the dispatcher wasn't given a registry handle.
    # Read-only; no audit gating.
    registry.register(SuggestAgentTool())
    # SW-track — llm_think.v1. The bridge tool that turns Forest
    # agents into entities you can actually ask things of. Wraps
    # provider.complete() inside the dispatcher so every LLM call
    # gets governance-pipeline gating, an audit row, and tokens
    # reported. Side_effects=read_only — runnable inside Guardian
    # (Reviewer) agents without per-call human approval. Honors the
    # T2.2b usage_cap_tokens task_cap (clips max_tokens down).
    registry.register(LlmThinkTool())
    # SW-track A.5 — code-side tools so the coding triune can DO work
    # on this repo, not just discuss it.
    #   code_read.v1   — read_only;  Architect+Engineer+Reviewer
    #   code_edit.v1   — filesystem; Engineer only (gated by genre rule)
    #   shell_exec.v1  — external;   Engineer only (gated by genre rule)
    # Per-agent allowed_paths + allowed_commands constraints (in the
    # constitution YAML's tool constraints block) cap blast radius;
    # see each tool's docstring for the safety semantics.
    registry.register(CodeReadTool())
    registry.register(CodeEditTool())
    registry.register(ShellExecTool())
    # Phase G.1.A — programming primitives. ruff_lint.v1 is the first
    # to land (Burst 53). Read-only subprocess invocation of the ruff
    # linter; gated only by allowed_paths constraint.
    registry.register(RuffLintTool())
    # Phase G.1.A — pytest_run.v1 (Burst 54). Filesystem-tier (writes
    # .pytest_cache); required_initiative_level L4. SW-track Engineer
    # (Actuator L5/L5) is the primary kit consumer.
    registry.register(PytestRunTool())
    # Phase G.1.A — git_log_read.v1 (Burst 55). Read-only subprocess
    # invocation of `git log` with structured output (delim-separated
    # fields, robust against arbitrary commit-message content).
    # SW-track Architect+Engineer+Reviewer all reach (read_only).
    registry.register(GitLogReadTool())
    # Phase G.1.A — git_diff_read.v1 (Burst 56). Read-only subprocess
    # invocation of `git diff` with structured per-file output.
    # Three modes: refs (between two refs), staged (--cached), working.
    # SW-track Reviewer is the primary consumer — diffing a feature
    # branch against main is the canonical entry point of code review.
    registry.register(GitDiffReadTool())
    # Phase G.1.A — git_blame_read.v1 (Burst 57). Read-only subprocess
    # invocation of `git blame --porcelain` with per-line attribution
    # (sha + author + date + summary + content). Optional line_range
    # narrows to a hot section. SW-track Reviewer + Architect both
    # reach (read_only).
    registry.register(GitBlameReadTool())
    # Phase G.1.A — mypy_typecheck.v1 (Burst 58). Read-only subprocess
    # invocation of mypy with --no-incremental (so no .mypy_cache is
    # written; honest read_only contract). Parses mypy's text output
    # into structured findings (file/line/column/severity/code/msg).
    # SW-track Engineer + Reviewer reach.
    registry.register(MypyTypecheckTool())
    # Phase G.1.A — semgrep_scan.v1 (Burst 59). Read-only subprocess
    # invocation of semgrep with JSON output. Catches the class of
    # bugs that come from "this looks like it could be exploited" —
    # SQL injection patterns, unsafe deserialization, hard-coded
    # secrets, taint-propagation issues. SW-track Reviewer (Guardian
    # genre L3) is the primary consumer.
    registry.register(SemgrepScanTool())
    # Phase G.1.A — tree_sitter_query.v1 (Burst 60). Read-only AST-
    # level structural queries via tree-sitter S-expressions. Lazy-
    # imports tree_sitter + tree_sitter_languages so daemon boots
    # without the optional dep. SW-track Architect's primary tool
    # for "find every callsite that matches this structural pattern"
    # questions.
    registry.register(TreeSitterQueryTool())
