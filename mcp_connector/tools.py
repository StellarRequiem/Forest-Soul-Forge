"""Forest Soul Forge connector tools — a thin HTTP client over the daemon's
read / analyze / safe-run API (ADR-0095 synaptic layer + ADR-0096 task exchange).

The MCP server (``server.py``) maps each MCP tool to one function here. Read-only +
safe by design: nothing here births, grants, force-closes, or deletes — those
operations stay operator-gated in the dashboard. Pure ``httpx`` (no ``mcp``
import), so this module is unit-testable without the FastMCP dependency.

Daemon URL via ``FSF_DAEMON_URL`` (default http://127.0.0.1:7423); optional bearer
token via ``FSF_API_TOKEN``; per-call timeout via ``FSF_MCP_TIMEOUT_S``.
"""
from __future__ import annotations

import os
from typing import Any

import httpx


def _base() -> str:
    return (os.environ.get("FSF_DAEMON_URL") or "http://127.0.0.1:7423").rstrip("/")


def _headers() -> dict:
    tok = os.environ.get("FSF_API_TOKEN")
    return {"X-FSF-Token": tok} if tok else {}


def _timeout() -> float:
    return float(os.environ.get("FSF_MCP_TIMEOUT_S") or "30")


def _get(path: str, params: dict | None = None) -> Any:
    r = httpx.get(_base() + path, params=params, headers=_headers(), timeout=_timeout())
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict | None = None) -> Any:
    r = httpx.post(_base() + path, json=body, headers=_headers(), timeout=_timeout())
    r.raise_for_status()
    return r.json()


# ----------------------------- read / analyze -----------------------------

def health() -> Any:
    """Daemon liveness + posture (GET /healthz)."""
    return _get("/healthz")


def trust(node: str | None = None, problem_class: str | None = None) -> Any:
    """Synaptic trust scores, optionally filtered (GET /synapse/trust)."""
    p: dict = {}
    if node:
        p["node"] = node
    if problem_class:
        p["problem_class"] = problem_class
    return _get("/synapse/trust", p or None)


def route(problem_class: str, candidates: str | None = None,
          seed: int | None = None) -> Any:
    """Trust-based routing recommendation (GET /synapse/route)."""
    p: dict = {"problem_class": problem_class}
    if candidates:
        p["candidates"] = candidates
    if seed is not None:
        p["seed"] = seed
    return _get("/synapse/route", p)


def bounties(min_n: float = 0.0, top: int = 10) -> Any:
    """Uncertainty-ranked bounty board (GET /synapse/bounties)."""
    return _get("/synapse/bounties", {"min_n": min_n, "top": top})


def quarantined(threshold: float = 0.4, min_n: float = 5.0) -> Any:
    """Quarantine candidates (GET /synapse/quarantined)."""
    return _get("/synapse/quarantined", {"threshold": threshold, "min_n": min_n})


def verify() -> Any:
    """Trust-ledger hash-chain integrity (GET /synapse/verify)."""
    return _get("/synapse/verify")


def why(node: str, problem_class: str) -> Any:
    """Provenance behind a trust value (GET /synapse/why)."""
    return _get("/synapse/why", {"node": node, "problem_class": problem_class})


def nodes() -> Any:
    """All tracked nodes + problem_classes (GET /synapse/nodes)."""
    return _get("/synapse/nodes")


def agents() -> Any:
    """The live agent fleet (GET /agents)."""
    return _get("/agents")


def training_tasks() -> Any:
    """The tiered training-ladder catalog (GET /training/tasks)."""
    return _get("/training/tasks")


# -------------------------------- safe run --------------------------------

def run_training() -> Any:
    """Run the full tiered self-test ladder, deterministic + read-only, and return
    the report (POST /training/run). No live side effects."""
    return _post("/training/run")
