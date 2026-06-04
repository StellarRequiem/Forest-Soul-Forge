"""In-process training harness (ADR-0096) — builds a real dispatcher + audit chain
+ trust graph + a read-only training agent, and the ``dispatch`` fn the runner
drives against actual tools.

Single-writer-safe: it owns its own audit chain + trust ledger under a workspace
dir; it never writes the live daemon's DB. This is the headless, repeatable
self-test form. (The autonomous form is a daemon scheduler task-type that reuses
the daemon's own dispatcher + trust graph in-process — the *same* runner.)

Two flavors:
  - the pure-function training ladder (TRAINING_TOOLS, no provider) — CI-safe;
  - the LLM benchmark (BENCHMARK_TOOLS = llm_think, with a local provider) — needs
    a live model (ollama), so operator-machine only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.synapse import TrustGraph
from forest_soul_forge.tools.base import ToolRegistry
from forest_soul_forge.tools.builtin import register_builtins
from forest_soul_forge.tools.dispatcher import (
    DispatchFailed,
    DispatchRefused,
    DispatchSucceeded,
    ToolDispatcher,
)
from forest_soul_forge.training.runner import StepOutcome

# Tools the shipped ladders use; the training constitution grants exactly these,
# read-only, no approval. Keep in sync with config/tasks/*.yaml.
TRAINING_TOOLS = (("timestamp_window", "1"), ("audit_chain_verify", "1"))
BENCHMARK_TOOLS = (("llm_think", "1"),)
TRAINING_AGENT_ID = "training_harness"
TRAINING_AGENT_DNA = "trn0harness0"   # 12 chars — synthetic, never a real birth


@dataclass
class TrainingEnv:
    dispatcher: ToolDispatcher
    audit: AuditChain
    trust_graph: TrustGraph
    constitution_path: Path
    agent_id: str
    provider: Any = None   # LocalProvider/etc. — threaded into provider-backed tools

    async def dispatch(self, agent_id: str, tool: str, version: str, args: dict) -> StepOutcome:
        """The runner's injected dispatch — drives the real ToolDispatcher and
        normalizes its outcome. The runner is sequential, so no write lock is
        needed (this process is the sole writer of its audit + trust files)."""
        outcome = await self.dispatcher.dispatch(
            instance_id=agent_id, agent_dna=TRAINING_AGENT_DNA,
            role="network_watcher", genre="observer", session_id="training",
            constitution_path=self.constitution_path,
            tool_name=tool, tool_version=version, args=args,
            provider=self.provider)
        if isinstance(outcome, DispatchSucceeded):
            return StepOutcome("succeeded", outcome.result.output, outcome.audit_seq)
        if isinstance(outcome, DispatchFailed):
            return StepOutcome("failed", None, outcome.audit_seq, outcome.exception_type)
        if isinstance(outcome, DispatchRefused):
            return StepOutcome("refused", None, outcome.audit_seq, outcome.reason)
        return StepOutcome("error", None, None)


def _write_training_constitution(path: Path, tools_spec) -> None:
    tools = [{"name": n, "version": v, "side_effects": "read_only",
              "constraints": {"max_calls_per_session": 100000,
                              "requires_human_approval": False},
              "applied_rules": []} for n, v in tools_spec]
    path.write_text(yaml.safe_dump(
        {"schema_version": 1, "agent": {"role": "network_watcher"}, "tools": tools},
        sort_keys=False), encoding="utf-8")


def build_env(workspace: str | Path, *, agent_id: str = TRAINING_AGENT_ID,
              tools: tuple = TRAINING_TOOLS, provider: Any = None) -> TrainingEnv:
    """Construct an isolated, real, in-process training environment under
    ``workspace``. ``tools`` grants the constitution; ``provider`` (a
    LocalProvider/etc.) is threaded into every dispatch so provider-backed tools
    like llm_think work (the benchmark). None for the pure-function ladder."""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)
    audit = AuditChain(ws / "training_audit.jsonl")
    trust = TrustGraph.load_or_create(ws / "training_trust.jsonl")
    registry = ToolRegistry()
    register_builtins(registry)
    counters: dict[tuple[str, str], int] = {}

    def get_count(iid: str, sid: str) -> int:
        return counters.get((iid, sid), 0)

    def inc_count(iid: str, sid: str, when: str) -> int:
        counters[(iid, sid)] = counters.get((iid, sid), 0) + 1
        return counters[(iid, sid)]

    dispatcher = ToolDispatcher(
        registry=registry, audit=audit,
        counter_get=get_count, counter_inc=inc_count,
        trust_graph=trust)  # ADR-0095 — tool dispatches also feed trust
    const = ws / "training_constitution.yaml"
    _write_training_constitution(const, tools)
    return TrainingEnv(dispatcher, audit, trust, const, agent_id, provider)


def build_local_provider():
    """Construct the configured local (ollama) provider — for benchmark tasks that
    dispatch llm_think. Reads base_url + the model map from settings, so it
    benchmarks whatever model FSF is configured to use."""
    from forest_soul_forge.daemon.config import DaemonSettings
    from forest_soul_forge.daemon.providers.local import LocalProvider
    s = DaemonSettings()
    return LocalProvider(base_url=s.local_base_url, models=s.local_model_map())
