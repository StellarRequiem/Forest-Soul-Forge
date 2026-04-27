"""``/agents/{instance_id}/character-sheet`` — derived view (ADR-0020).

Composes the eight-section descriptor from the canonical artifacts:

    registry row     → identity (mostly)
    soul.md          → trait_values, voice text, narrative_*, tools list
    constitution.yaml→ policies, risk_thresholds, drift_monitoring,
                       per-tool constraints
    genre engine     → genre risk profile, trait_emphasis, spawn rules
    audit chain      → event count, last entry hash

ADR-0006 invariant preserved: this endpoint NEVER writes anything. The
character sheet is a pure derivation — re-rendering with the same source
artifacts yields the same JSON modulo ``rendered_at``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.genre_engine import GenreEngine, GenreEngineError
from forest_soul_forge.core.tool_catalog import ToolCatalog
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_genre_engine,
    get_registry,
    get_tool_catalog,
)
from forest_soul_forge.daemon.schemas import (
    CharacterBenchmarks,
    CharacterCapabilities,
    CharacterIdentity,
    CharacterLoadout,
    CharacterLoadoutTool,
    CharacterMemory,
    CharacterPersonality,
    CharacterPolicySummary,
    CharacterProvenance,
    CharacterSheetOut,
    CharacterStats,
    CharacterStatsPerTool,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.ingest import _parse_frontmatter_block, _FRONTMATTER_RE
from forest_soul_forge.registry.registry import UnknownAgentError


router = APIRouter(tags=["character-sheet"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_soul_frontmatter(path: Path) -> dict[str, Any]:
    """Return the parsed frontmatter dict, or 409 if the file is missing
    or malformed. The error shape matches /agents/{id}/regenerate-voice
    for consistency."""
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"soul file missing on disk: {path}",
        )
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"soul file missing YAML frontmatter: {path}",
        )
    return _parse_frontmatter_block(m.group(1))


def _read_voice_section(text: str) -> str | None:
    """Pull the body of the ``## Voice`` section, if present.

    Returns the markdown between the ``## Voice`` heading and the next
    ``##`` heading (exclusive). None when the section is absent
    (templated-only birth that didn't run the LLM).
    """
    lines = text.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "## voice":
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip() or None


def _read_constitution(path: Path) -> dict[str, Any]:
    """Read the constitution.yaml; return parsed dict or empty dict if
    the file is missing. Missing constitution doesn't 409 — the registry
    row could still hold a hash (older artifact format)."""
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def _summarize_policies(constitution: dict[str, Any]) -> CharacterPolicySummary:
    policies = constitution.get("policies") or []
    rule_counts: dict[str, int] = {}
    for p in policies:
        if isinstance(p, dict):
            rule = str(p.get("rule", "unknown"))
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
    rt = constitution.get("risk_thresholds") or {}
    risk = {}
    for k in ("auto_halt_risk", "escalate_risk", "min_confidence_to_act"):
        v = rt.get(k)
        if isinstance(v, (int, float)):
            risk[k] = float(v)
    return CharacterPolicySummary(
        constitution_hash=str(constitution.get("constitution_hash") or "") or None,
        policy_count=len(policies),
        policy_count_by_rule=rule_counts,
        risk_thresholds=risk,
        drift_monitoring=dict(constitution.get("drift_monitoring") or {}),
        out_of_scope=list(constitution.get("out_of_scope") or []),
        operator_duties=list(constitution.get("operator_duties") or []),
    )


def _build_loadout(
    constitution: dict[str, Any],
    catalog: ToolCatalog,
    tool_catalog_version: str | None,
) -> CharacterLoadout:
    """Compose the loadout from the constitution.yaml ``tools:`` block.
    Joins each entry against the catalog so the operator sees the
    description without a second click."""
    tool_entries = constitution.get("tools") or []
    tools_out: list[CharacterLoadoutTool] = []
    for t in tool_entries:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "")
        version = str(t.get("version") or "")
        # Catalog join — best-effort. Pre-T4 souls might reference a
        # tool the running catalog no longer has; degrade gracefully.
        td = catalog.tools.get(f"{name}.v{version}")
        tools_out.append(
            CharacterLoadoutTool(
                name=name,
                version=version,
                side_effects=str(t.get("side_effects") or "") or (td.side_effects if td else None),
                description=td.description if td else None,
                constraints=dict(t.get("constraints") or {}),
                applied_rules=list(t.get("applied_rules") or []),
            )
        )
    return CharacterLoadout(
        tools=tools_out,
        tool_catalog_version=tool_catalog_version,
    )


def _build_capabilities(
    genre_name: str | None,
    genre_engine: GenreEngine,
    constitution: dict[str, Any],
) -> CharacterCapabilities:
    """Pull genre metadata + risk floor + spawn-compat from the loaded
    engine. ``constitution`` is consulted for the genre_description
    fallback when the engine doesn't have the genre any more (e.g.
    after a genres.yaml edit)."""
    if genre_name is None:
        agent_block = constitution.get("agent") or {}
        genre_from_const = agent_block.get("genre")
        genre_name = str(genre_from_const) if genre_from_const else None

    if genre_name is None:
        return CharacterCapabilities()

    # Direct lookup by genre name — the engine indexes by name in `.genres`.
    gd = genre_engine.genres.get(genre_name)

    if gd is None:
        # Genre is named in the artifact but the engine doesn't know it
        # (genres.yaml edited since this agent was born). Surface the
        # name + description from the artifact itself as a best-effort.
        agent_block = constitution.get("agent") or {}
        return CharacterCapabilities(
            genre=genre_name,
            genre_description=str(agent_block.get("genre_description") or "") or None,
        )

    return CharacterCapabilities(
        genre=gd.name,
        genre_description=gd.description,
        max_side_effects=gd.risk_profile.max_side_effects,
        provider_constraint=gd.risk_profile.provider_constraint,
        trait_emphasis=list(gd.trait_emphasis),
        spawn_compatibility=list(gd.spawn_compatibility),
    )


def _build_provenance(
    soul_path: Path,
    constitution_path: Path,
    audit: AuditChain,
    agent_dna: str,
) -> CharacterProvenance:
    """Audit-chain pointers. Counts events for this agent and returns
    the most recent entry hash so an inspector can chase the chain.

    AuditChain doesn't expose a per-agent index (one chain, scanned
    linearly). For most deployments the chain is small enough that
    this is fine; if a deployment grows past the comfort limit, this
    would move to a precomputed index.
    """
    events = [e for e in audit.read_all() if e.agent_dna == agent_dna]
    last_hash = events[-1].entry_hash if events else None
    return CharacterProvenance(
        soul_path=str(soul_path),
        constitution_path=str(constitution_path),
        audit_event_count=len(events),
        audit_chain_entry_hash=last_hash,
    )


def _build_memory(registry: Registry, instance_id: str) -> CharacterMemory:
    """Aggregate per-layer memory counts (ADR-0022 v0.1).

    Empty agent → not_yet_measured=True. Walking the registry
    connection directly (rather than via the Memory class) keeps
    the character-sheet endpoint a pure read — no Memory instance
    leakage to a read-only path."""
    layers = {"episodic": 0, "semantic": 0, "procedural": 0}
    total = 0
    rows = registry._conn.execute(  # noqa: SLF001 — internal access by design
        """
        SELECT layer, COUNT(*) AS n
        FROM memory_entries
        WHERE instance_id=? AND deleted_at IS NULL
        GROUP BY layer;
        """,
        (instance_id,),
    ).fetchall()
    for row in rows:
        layer = str(row["layer"])
        n = int(row["n"])
        if layer in layers:
            layers[layer] = n
        total += n
    if total == 0:
        return CharacterMemory()
    return CharacterMemory(
        not_yet_measured=False,
        total_entries=total,
        layers=layers,
    )


def _build_stats(registry: Registry, instance_id: str) -> CharacterStats:
    """Aggregate the registry's ``tool_calls`` table into character-sheet
    stats (ADR-0019 T4). Empty agent → ``not_yet_measured=True``."""
    agg = registry.aggregate_tool_calls(instance_id)
    total = int(agg.get("total_invocations") or 0)
    if total == 0:
        return CharacterStats()
    per_tool = [
        CharacterStatsPerTool(
            tool_key=str(item["tool_key"]),
            count=int(item.get("count") or 0),
            tokens=item.get("tokens"),
            cost=item.get("cost"),
        )
        for item in (agg.get("per_tool") or [])
    ]
    return CharacterStats(
        not_yet_measured=False,
        total_invocations=total,
        failed_invocations=int(agg.get("failed_invocations") or 0),
        total_tokens_used=agg.get("total_tokens_used"),
        total_cost_usd=agg.get("total_cost_usd"),
        last_active_at=agg.get("last_active_at"),
        per_tool=per_tool,
    )


@router.get(
    "/agents/{instance_id}/character-sheet",
    response_model=CharacterSheetOut,
)
async def get_character_sheet(
    instance_id: str,
    registry: Registry = Depends(get_registry),
    genre_engine: GenreEngine = Depends(get_genre_engine),
    tool_catalog: ToolCatalog = Depends(get_tool_catalog),
    audit: AuditChain = Depends(get_audit_chain),
) -> CharacterSheetOut:
    try:
        row = registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    soul_path = Path(row.soul_path)
    constitution_path = Path(row.constitution_path)

    fm = _read_soul_frontmatter(soul_path)
    soul_text = soul_path.read_text(encoding="utf-8")
    voice_text = _read_voice_section(soul_text)
    constitution = _read_constitution(constitution_path)

    # Genre name comes from soul.md frontmatter when present; falls back
    # to the constitution.yaml `agent.genre` field (T3 emits both).
    genre_name = fm.get("genre")
    if not genre_name:
        agent_block = constitution.get("agent") or {}
        genre_name = agent_block.get("genre")
    genre_name = str(genre_name) if genre_name else None

    # Lineage from frontmatter (root-first list of ancestor DNAs).
    lineage = []
    for x in (fm.get("lineage") or []):
        lineage.append(str(x))

    identity = CharacterIdentity(
        instance_id=row.instance_id,
        dna=row.dna,
        dna_full=row.dna_full,
        sibling_index=int(fm.get("sibling_index") or row.sibling_index or 1),
        agent_name=row.agent_name,
        agent_version=str(fm.get("agent_version") or "v1"),
        role=row.role,
        genre=genre_name,
        parent_instance=row.parent_instance,
        lineage=lineage,
        lineage_depth=int(fm.get("lineage_depth") or 0),
        created_at=row.created_at,
        status=row.status,
        owner_id=row.owner_id,
    )

    # Trait values from frontmatter — already a dict per the parser.
    trait_values_raw = fm.get("trait_values") or {}
    trait_values: dict[str, int] = {}
    for k, v in trait_values_raw.items():
        try:
            trait_values[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    domain_overrides_raw = fm.get("domain_weight_overrides") or {}
    domain_overrides: dict[str, float] = {}
    if isinstance(domain_overrides_raw, dict):
        for k, v in domain_overrides_raw.items():
            try:
                domain_overrides[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    personality = CharacterPersonality(
        trait_values=trait_values,
        domain_weight_overrides=domain_overrides,
        voice_text=voice_text,
        narrative_provider=str(fm.get("narrative_provider") or "") or None,
        narrative_model=str(fm.get("narrative_model") or "") or None,
        narrative_generated_at=str(fm.get("narrative_generated_at") or "") or None,
    )

    tool_catalog_version = str(fm.get("tool_catalog_version") or "") or None
    loadout = _build_loadout(constitution, tool_catalog, tool_catalog_version)
    capabilities = _build_capabilities(genre_name, genre_engine, constitution)
    policies = _summarize_policies(constitution)
    provenance = _build_provenance(soul_path, constitution_path, audit, row.dna)
    stats = _build_stats(registry, instance_id)
    memory_summary = _build_memory(registry, instance_id)

    return CharacterSheetOut(
        schema_version=1,
        rendered_at=_now_iso(),
        identity=identity,
        personality=personality,
        loadout=loadout,
        capabilities=capabilities,
        policies=policies,
        stats=stats,
        memory=memory_summary,
        benchmarks=CharacterBenchmarks(),
        provenance=provenance,
    )
