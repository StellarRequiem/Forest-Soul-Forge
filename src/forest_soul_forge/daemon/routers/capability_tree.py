"""``GET /agents/{instance_id}/capability-tree`` — per-agent
capability tree (ADR-0080 T1, B380).

Returns a dependency-shaped view of a single agent's effective
capabilities. Composes from four sources in strict precedence:

  1. Constitution `tools` list (hard-wired, immutable at birth).
  2. Genre `risk_profile.max_side_effects` ceiling (genre invariant).
  3. Per-agent posture (ADR-0036) — gates what the operator can
     toggle on/off. Today the posture surface is yellow/green/red;
     toggles at finer granularity land in T3.
  4. Runtime availability — is the tool actually registered in
     /tools/registered? Is the LLM provider alive for llm_* tools?

Three visual states per node (computed in the response):

  * live              — capability is callable right now.
  * broken            — known by the constitution but absent from
                        registry, or its provider is offline.
  * in_progress       — staged via forge pipeline but not installed
                        (skills only, today).

Two binding modes:

  * hard_wired        — required by role + genre + constitution.
                        Operator cannot toggle off at this layer;
                        rebirth required to remove.
  * operator_toggleable — gateable via posture (future T3 will land
                          the toggle endpoint).

Tree shape: skills declare `requires`, so the response carries
`skill -> required_tools` edges. Inferred tool->tool edges (e.g.
code_edit requires code_read) ship in T4 if it lands; not today.

This endpoint is the substrate for the new frontend
'Agent Capabilities' tab (T2). The toggle endpoint
(POST /agents/{id}/capability-toggle) lands in T3.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_tool_registry,
    get_write_lock,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError


router = APIRouter(prefix="/agents", tags=["capability-tree"])


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class AgentSummary(BaseModel):
    """Minimal agent metadata for the tree's header."""
    instance_id: str
    role: str
    genre: str | None
    agent_name: str | None
    posture: str | None


class ToolNode(BaseModel):
    """One tool in the agent's tree."""
    key: str                      # name.vN
    side_effects: str | None
    status: str                   # live | broken | in_progress
    binding: str                  # hard_wired | operator_toggleable
    reason: str                   # human-readable explanation
    constraints: dict[str, Any] | None = None


class SkillNode(BaseModel):
    """One skill in the agent's tree."""
    name: str
    version: str
    status: str                   # live | broken | in_progress
    binding: str
    reason: str
    requires_tools: list[str]
    missing_tools: list[str]      # subset of requires_tools not in agent's kit
    description: str | None = None


class McpPluginNode(BaseModel):
    """Synthetic parent for MCP-installed tools. Empty today;
    populated when ADR-0043 plugin grants per-agent ship."""
    name: str
    status: str
    binding: str
    tools: list[str] = []


class CapabilityTree(BaseModel):
    """The composed tree."""
    tools: list[ToolNode]
    skills: list[SkillNode]
    mcp_plugins: list[McpPluginNode]


class CapabilityTreeOut(BaseModel):
    schema_version: int = 1
    agent: AgentSummary
    tree: CapabilityTree
    # Operator-readable summary counts so a thin renderer (e.g. a
    # status badge) can show "3/12 live" without iterating the tree.
    summary: dict[str, int]


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------

def _read_constitution_tools(const_path: str | None) -> list[dict[str, Any]]:
    """Pull the tools list out of a constitution YAML. Returns [] on
    any failure (missing file, parse error). The endpoint surfaces
    the failure as an empty tools list with a degraded summary count
    rather than failing the request — a broken constitution doesn't
    make the agent's metadata unavailable."""
    if not const_path:
        return []
    p = Path(const_path)
    if not p.exists():
        return []
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    tools = doc.get("tools") or []
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, dict)]


def _tool_key(t: dict[str, Any]) -> str:
    name = t.get("name", "")
    version = t.get("version", "1")
    return f"{name}.v{version}"


def _genre_for_role(genres, role: str):
    """Best-effort genre lookup. genres may be None (engine missing).
    Returns the Genre object or None."""
    if genres is None:
        return None
    try:
        return genres.genre_for(role)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/{instance_id}/capability-tree",
    response_model=CapabilityTreeOut,
)
async def get_capability_tree(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
    tool_registry=Depends(get_tool_registry),
) -> CapabilityTreeOut:
    # 1. Agent metadata (404 if unknown).
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent: {e}",
        ) from e

    role = getattr(agent, "role", "")
    posture = getattr(agent, "posture", None)
    agent_name = getattr(agent, "agent_name", None)
    const_path = getattr(agent, "constitution_path", None)

    # 2. Genre + ceiling.
    genres = getattr(request.app.state, "genre_engine", None)
    genre = _genre_for_role(genres, role)
    genre_name = getattr(genre, "name", None) if genre else None

    # 3. Constitution tools (the agent's allowed_tools set).
    const_tools = _read_constitution_tools(const_path)
    allowed_keys = {_tool_key(t) for t in const_tools}

    # 4. Live availability via tool registry.
    #    Tool registry exposes has(name, version) per its protocol.
    def _is_registered(key: str) -> bool:
        if "." not in key:
            return False
        try:
            name, vpart = key.rsplit(".v", 1)
        except ValueError:
            return False
        try:
            return bool(tool_registry.has(name, vpart))
        except Exception:
            return False

    # ----- Compose tool nodes -----
    tool_nodes: list[ToolNode] = []
    for t in const_tools:
        key = _tool_key(t)
        side_effects = t.get("side_effects")
        if _is_registered(key):
            status_ = "live"
            reason = "in constitution + registered in /tools/registered"
        else:
            status_ = "broken"
            reason = (
                "in constitution but missing from /tools/registered "
                "(provider/registration drift)"
            )
        tool_nodes.append(ToolNode(
            key=key,
            side_effects=side_effects,
            status=status_,
            binding="hard_wired",  # constitution binding = hard_wired
            reason=reason,
            constraints=t.get("constraints"),
        ))

    # ----- Compose skill nodes -----
    # B392 — skill status uses "unavailable" (not "broken") when this
    # specific agent's kit doesn't have all required tools. The skill
    # itself works on the substrate; THIS agent simply can't run it.
    # "broken" stays reserved for actual substrate corruption (e.g.
    # tool registered in constitution but not in /tools/registered).
    # Operator-readable distinction: tool-level broken = substrate
    # problem; skill-level unavailable = agent kit gap (rebirth fixes
    # it via the proper pipeline).
    skill_catalog = getattr(request.app.state, "skill_catalog", None)
    skill_nodes: list[SkillNode] = []
    if skill_catalog is not None and hasattr(skill_catalog, "skills"):
        for key in sorted(skill_catalog.skills):
            sd = skill_catalog.skills[key]
            requires = list(getattr(sd, "requires", []) or [])
            # Missing tools = required but not in this agent's
            # allowed_tools.
            missing = [r for r in requires if r not in allowed_keys]
            if not missing:
                status_ = "live"
                reason = (
                    f"installed; all {len(requires)} required tools "
                    f"available in agent's kit"
                )
            else:
                status_ = "unavailable"
                reason = (
                    f"installed on substrate, but agent's kit is "
                    f"missing {len(missing)}/{len(requires)} required "
                    f"tools. Skill works on the substrate — agent "
                    f"needs the missing tools added (via rebirth, per "
                    f"the constitution-hash invariant)."
                )
            skill_nodes.append(SkillNode(
                name=sd.name,
                version=sd.version,
                status=status_,
                binding="operator_toggleable",
                reason=reason,
                requires_tools=requires,
                missing_tools=missing,
                description=getattr(sd, "description", None),
            ))

    # ----- MCP plugins (placeholder; T2 frontend may surface differently) -----
    mcp_nodes: list[McpPluginNode] = []
    # Future: read plugin grants for this agent from
    # plugin_grants table; surface as a synthetic node tree.

    # ----- Summary -----
    summary = {
        "tools_total":         len(tool_nodes),
        "tools_live":          sum(1 for n in tool_nodes if n.status == "live"),
        "tools_broken":        sum(1 for n in tool_nodes if n.status == "broken"),
        "skills_total":        len(skill_nodes),
        "skills_live":         sum(1 for n in skill_nodes if n.status == "live"),
        # B392 — `unavailable` is the per-agent kit gap (skill works
        # on substrate; this agent's kit is missing required tools).
        # Distinct from `broken` which stays reserved for substrate
        # corruption (catalog/registration drift).
        "skills_unavailable":  sum(1 for n in skill_nodes if n.status == "unavailable"),
        "skills_broken":       sum(1 for n in skill_nodes if n.status == "broken"),
        "mcp_plugins_total":   len(mcp_nodes),
    }

    return CapabilityTreeOut(
        schema_version=1,
        agent=AgentSummary(
            instance_id=instance_id,
            role=role,
            genre=genre_name,
            agent_name=agent_name,
            posture=posture,
        ),
        tree=CapabilityTree(
            tools=tool_nodes,
            skills=skill_nodes,
            mcp_plugins=mcp_nodes,
        ),
        summary=summary,
    )


# ---------------------------------------------------------------------------
# T3 (B382) — capability-toggle endpoint
# ---------------------------------------------------------------------------

class CapabilityToggleIn(BaseModel):
    """Body for POST /agents/{id}/capability-toggle.

    capability_key is the same key the tree returns:
      tools  -> "name.vN"
      skills -> "name.vN"   (the skill's catalog key)
      mcp    -> the plugin name (no version suffix yet; placeholder)

    enabled=True asks to enable a previously-disabled capability;
    enabled=False asks to disable. Hard_wired capabilities reject
    with 409 regardless of the value — rebirth is the only path
    to remove a constitution-bound tool, per ADR-0080 D5 +
    CLAUDE.md constitution-hash invariant.
    """
    capability_key: str = Field(..., min_length=1, max_length=200)
    enabled: bool


class CapabilityToggleOut(BaseModel):
    """Response shape — operator gets back the audit trail."""
    instance_id: str
    capability_key: str
    binding: str             # hard_wired | operator_toggleable
    requested_enabled: bool
    accepted: bool           # True if the toggle was recorded
    audit_event_seq: int | None
    detail: str


def _classify_capability(
    capability_key: str,
    const_tools: list[dict[str, Any]],
    skill_catalog: Any,
) -> tuple[str, str]:
    """Map a capability_key to (binding, kind).

    binding   - hard_wired | operator_toggleable | unknown
    kind      - tool | skill | mcp | unknown

    constitution-bound tools => hard_wired/tool.
    catalog skills           => operator_toggleable/skill.
    everything else          => unknown/unknown (the caller
                                returns 404 with detail).
    """
    constitution_keys = {_tool_key(t) for t in const_tools}
    if capability_key in constitution_keys:
        return ("hard_wired", "tool")
    if skill_catalog is not None and hasattr(skill_catalog, "skills"):
        if capability_key in skill_catalog.skills:
            return ("operator_toggleable", "skill")
    # mcp plugins live elsewhere; T1 placeholder doesn't classify them.
    return ("unknown", "unknown")


@router.post(
    "/{instance_id}/capability-toggle",
    response_model=CapabilityToggleOut,
)
async def toggle_capability(
    instance_id: str,
    body: CapabilityToggleIn,
    request: Request,
    registry: Registry = Depends(get_registry),
    chain=Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> CapabilityToggleOut:
    """T3 (B382) — operator toggle for a per-agent capability.

    Validates the (agent, capability) pair against the tree's
    composition rules. Hard_wired tools reject with 409. Operator-
    toggleable skills record a `capability_toggled` audit chain
    event with the requested state. Actual runtime gating of
    toggled-off skills lands in T3b (a small per-agent overrides
    table; ships in its own burst with a schema bump). For now
    the audit trail IS the durable record — operators can replay
    intent from the chain even before the runtime enforces it.

    Why audit-first / enforcement-later:
      - The audit chain is append-only and tamper-evident; it's
        the source of truth in every other Forest mutation.
      - Schema bumps require migration discipline. Shipping the
        endpoint contract + audit emission first lets T2's
        frontend mature against a stable shape before T3b adds
        the storage layer.
      - Operators see the toggle land immediately (the event
        appears in the audit tail). T3b makes the runtime
        listen to it.
    """
    # 1. Agent exists.
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent: {e}",
        ) from e

    # 2. Resolve capability binding from the tree's composition
    # rules. Read constitution tools + skill catalog same way the
    # GET endpoint does so the toggle's view of the tree stays
    # consistent with what the operator sees.
    const_tools = _read_constitution_tools(getattr(agent, "constitution_path", None))
    skill_catalog = getattr(request.app.state, "skill_catalog", None)
    binding, kind = _classify_capability(
        body.capability_key, const_tools, skill_catalog,
    )

    if binding == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"capability_key {body.capability_key!r} is not in agent "
                f"{instance_id!r}'s capability tree. Check spelling + "
                f"that the skill/tool is in the catalog."
            ),
        )

    if binding == "hard_wired":
        # Per CLAUDE.md identity-hash invariant + ADR-0080 D5:
        # constitution-bound tools cannot be toggled off without
        # rebirth. The 409 (Conflict) tells the operator the
        # request is well-formed but rejected by the model.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"capability_key {body.capability_key!r} is hard_wired by "
                f"agent {instance_id!r}'s constitution. Rebirth is the only "
                f"path to remove a constitution-bound tool (see ADR-0080 "
                f"D5 + the constitution-hash immutability invariant)."
            ),
        )

    # 3. Emit the audit event. The write_lock ensures single-writer
    # discipline; same posture as agent_posture_changed.
    operator_id = getattr(request.state, "operator_id", None)
    with write_lock:
        entry = chain.append(
            "capability_toggled",
            {
                "instance_id":      instance_id,
                "capability_key":   body.capability_key,
                "kind":              kind,
                "binding":           binding,
                "requested_enabled": body.enabled,
                "set_by":            operator_id,
                # prior_state is "unknown" today — T3b adds the
                # storage table that lets us record actual prior
                # state. Audit reader can reconstruct intent by
                # walking the chain for this (instance_id,
                # capability_key) pair.
                "prior_state":       "unknown",
            },
            agent_dna=getattr(agent, "dna", None),
        )

    return CapabilityToggleOut(
        instance_id=instance_id,
        capability_key=body.capability_key,
        binding=binding,
        requested_enabled=body.enabled,
        accepted=True,
        audit_event_seq=getattr(entry, "seq", None),
        detail=(
            f"toggle recorded in audit chain (seq={getattr(entry, 'seq', '?')}). "
            f"Runtime enforcement lands in T3b; until then, the audit "
            f"trail is the durable record. Operator can replay intent "
            f"via memory/audit query for this (agent, capability) pair."
        ),
    )
