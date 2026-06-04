"""MCP server — exposes the Forest Soul Forge daemon's read / analyze / safe-run
tools over stdio. A thin shell: each MCP tool maps to a function in ``tools.py``
that calls the daemon's HTTP API. Read-only + safe by design (no birth / grant /
force / delete). FastMCP infers each tool's schema from the type hints + docstring.

Run (or via .mcp.json):  PYTHONPATH=<FSF repo> python -m mcp_connector.server
Daemon URL via FSF_DAEMON_URL (default http://127.0.0.1:7423); token via FSF_API_TOKEN.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools as T

mcp = FastMCP("fsf")


@mcp.tool()
def fsf_health() -> dict:
    """Is the Forest daemon up? Returns /healthz (status, posture, agent + chain counts)."""
    return T.health()


@mcp.tool()
def fsf_trust(node: str | None = None, problem_class: str | None = None) -> dict:
    """Synaptic trust scores per (agent, problem_class). Optionally filter by `node`
    and/or `problem_class`. Each score carries a credible interval + observation
    count — 0.80 at n=3 (uncertain) is never confused with 0.80 at n=300 (sharp)."""
    return T.trust(node, problem_class)


@mcp.tool()
def fsf_route(problem_class: str, candidates: str | None = None,
             seed: int | None = None) -> dict:
    """Trust-based routing RECOMMENDATION for a problem_class (Thompson-sampled from
    the posteriors). `candidates` is an optional comma-separated node list (default:
    every node with a track record); `seed` makes the ranking reproducible. INFORMS
    only — converting trust into capability stays human-gated (ADR-0095)."""
    return T.route(problem_class, candidates, seed)


@mcp.tool()
def fsf_bounties(min_n: float = 0.0, top: int = 10) -> dict:
    """The bounty board: (agent, problem_class) pairs ranked by trust UNCERTAINTY
    (credible-interval width) — the highest-value things to test next (ADR-0096).
    Surfacing a bounty is analysis; running it stays a human decision."""
    return T.bounties(min_n, top)


@mcp.tool()
def fsf_quarantined(threshold: float = 0.4, min_n: float = 5.0) -> dict:
    """Nodes confidently below `threshold` trust with >= `min_n` observations —
    quarantine candidates the mesh may autonomously isolate. Release is human-gated."""
    return T.quarantined(threshold, min_n)


@mcp.tool()
def fsf_verify() -> dict:
    """Hash-chain integrity of the trust ledger — proves no past outcome was forged
    or silently rewritten (the audit-chain discipline applied to trust itself)."""
    return T.verify()


@mcp.tool()
def fsf_why(node: str, problem_class: str) -> dict:
    """Provenance behind a trust value: every audited outcome that shaped it, each
    cross-linked to its audit-chain seq. The answer to 'why is this node trusted X?'"""
    return T.why(node, problem_class)


@mcp.tool()
def fsf_nodes() -> dict:
    """All nodes + problem_classes currently tracked by the synaptic layer."""
    return T.nodes()


@mcp.tool()
def fsf_agents() -> dict:
    """The live agent fleet (read-only)."""
    return T.agents()


@mcp.tool()
def fsf_training_tasks() -> dict:
    """The tiered training-ladder catalog (Baseline + L1-4 deterministic self-test tasks)."""
    return T.training_tasks()


@mcp.tool()
def fsf_run_training() -> dict:
    """Run the full tiered self-test ladder (deterministic + read-only) in an
    isolated workspace and return the report — per-tier pass/fail + audit & trust
    integrity. Safe to run anytime; the live daemon's data is untouched."""
    return T.run_training()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
