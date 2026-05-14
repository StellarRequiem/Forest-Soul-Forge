"""writes/voice.py — voice-regeneration trust surface.

ADR-0040 T3.3 extraction (Burst 79, 2026-05-02). Owns the
``/agents/{instance_id}/regenerate-voice`` endpoint — re-runs the
LLM Voice renderer for an existing agent without touching its
identity (dna, instance_id, constitution_hash). Useful for
prompt-iteration and provider-comparison workflows (ADR-0017
follow-up); not a creation path.

Trust-surface scope (per ADR-0040 §1):
This file owns voice-regeneration governance — soul.md frontmatter
parsing, trait-profile reconstruction from disk, voice render
dispatch, soul.md in-place update, and the ``voice_regenerated``
audit event. An agent given ``allowed_paths`` to ``writes/voice.py``
can extend voice-iteration logic — alternate frontmatter formats,
multi-provider voice racing, regression diffing — without
inheriting the ability to create new agents (``writes/birth.py``)
or archive them (``writes/archive.py``). That separation is the
file-grained governance value ADR-0040 §1 was filed to deliver.

What lives here:
- ``regenerate_voice`` route handler.

What lives elsewhere (don't reach for it):
- ``_maybe_render_voice`` — voice renderer bridge, in ``_shared.py``
  because both this and birth route through it.
- DTO + chain adapters (``to_agent_out``, ``chain_entry_to_parsed``)
  — in ``birth_pipeline.py`` from Phase C.2 (2026-04-30).
- Frontmatter regex / parser — in
  ``forest_soul_forge.registry.ingest`` (imported as ``_ingest``).
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.core.dna import Lineage
from forest_soul_forge.core.genre_engine import GenreEngine
from forest_soul_forge.core.trait_engine import (
    InvalidTraitValueError,
    SchemaError as TraitSchemaError,
    TraitEngine,
    UnknownRoleError,
    UnknownTraitError,
)
from forest_soul_forge.daemon.config import DaemonSettings
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_genre_engine,
    get_provider_registry,
    get_registry,
    get_settings,
    get_trait_engine,
    get_write_lock,
)
from forest_soul_forge.daemon.providers import ProviderRegistry
from forest_soul_forge.daemon.schemas import AgentOut
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry import ingest as _ingest
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.soul.voice_renderer import update_soul_voice

from forest_soul_forge.daemon.routers.birth_pipeline import (
    chain_entry_to_parsed as _chain_entry_to_parsed,
    to_agent_out as _to_agent_out,
)

# ADR-0040 T3.2 — _maybe_render_voice was promoted to _shared.py
# in Burst 78 because both birth and voice surfaces dispatch
# through it.
from forest_soul_forge.daemon.routers.writes._shared import _maybe_render_voice


# Sub-router. The package facade (writes/__init__.py) declares the
# governance dependencies (require_writes_enabled + require_api_token);
# this router carries no deps of its own so include_router doesn't
# double-stack them on the included routes.
router = APIRouter()


@router.post("/agents/{instance_id}/regenerate-voice", response_model=AgentOut)
def regenerate_voice(
    instance_id: str,
    request: Request,
    registry: Registry = Depends(get_registry),
    engine: TraitEngine = Depends(get_trait_engine),
    audit: AuditChain = Depends(get_audit_chain),
    lock: threading.Lock = Depends(get_write_lock),
    settings: DaemonSettings = Depends(get_settings),
    providers: ProviderRegistry = Depends(get_provider_registry),
    genre_engine: GenreEngine = Depends(get_genre_engine),
):
    """Re-run the Voice renderer for an existing agent (ADR-0017 follow-up).

    The agent's identity (dna, instance_id, constitution_hash) is
    unchanged. Only the soul.md ``## Voice`` body and the three
    narrative_* frontmatter fields are rewritten in-place. An audit
    event ``voice_regenerated`` records the before/after provider+model
    so the chain captures the prompt-iteration history.

    Useful for tuning prompts or comparing output across providers
    without spinning up new agents and polluting the registry.
    """
    try:
        row = registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(status_code=404, detail=f"unknown agent: {e}") from e

    soul_path = Path(row.soul_path)

    # ADR-0050 T5 (B271) — file-encryption pass-through. The registry
    # records the canonical plaintext path; the on-disk file may be
    # at <soul_path>.enc if the agent was birthed under encryption.
    # Build the encryption config once for both the read here and the
    # update_soul_voice rewrite at the bottom of the function.
    _master_key = getattr(request.app.state, "master_key", None)
    _enc_config = None
    if _master_key is not None:
        from forest_soul_forge.core.at_rest_encryption import (
            EncryptionConfig as _EncryptionConfig,
        )
        _enc_config = _EncryptionConfig(master_key=_master_key)

    soul_enc_path = soul_path.with_name(soul_path.name + ".enc")
    if not soul_path.exists() and not soul_enc_path.exists():
        # Disk drift — registry has the row but neither variant of
        # the file is present. Refuse to regenerate; rebuild-from-
        # artifacts is the right repair path.
        raise HTTPException(
            status_code=409,
            detail=f"soul file missing on disk: {soul_path}",
        )

    # Reconstruct the trait profile from the existing soul.md frontmatter.
    # Pulls trait_values + role + domain_weight_overrides directly so we
    # don't need them on the registry row.
    from forest_soul_forge.daemon.routers.birth_pipeline import read_soul_md
    parsed_text = read_soul_md(soul_path, encryption_config=_enc_config)
    fm_match = _ingest._FRONTMATTER_RE.match(parsed_text)
    if fm_match is None:
        raise HTTPException(
            status_code=409,
            detail=f"soul file has no parseable frontmatter: {soul_path}",
        )
    frontmatter = _ingest._parse_frontmatter_block(fm_match.group(1))
    # The tolerant frontmatter parser returns scalars for inline `{}`
    # placeholders (e.g. SoulGenerator emits `domain_weight_overrides: {}`
    # literally). Coerce non-dict values to empty maps so build_profile
    # gets the shape it expects.
    trait_values = frontmatter.get("trait_values")
    if not isinstance(trait_values, dict):
        trait_values = {}
    domain_weight_overrides = frontmatter.get("domain_weight_overrides")
    if not isinstance(domain_weight_overrides, dict):
        domain_weight_overrides = {}

    try:
        profile = engine.build_profile(
            role=row.role,
            overrides={k: int(v) for k, v in trait_values.items()},
            domain_weight_overrides={
                k: float(v) for k, v in domain_weight_overrides.items()
            },
        )
    except (UnknownRoleError, UnknownTraitError, InvalidTraitValueError, TraitSchemaError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"could not reconstruct profile from soul.md: {e}",
        ) from e

    # Reconstruct lineage from registry — same shape /spawn uses.
    if row.parent_instance:
        try:
            parent_row = registry.get_agent(row.parent_instance)
            ancestors_rows = registry.get_ancestors(row.parent_instance)
            root_first = list(reversed(ancestors_rows))
            parent_lineage_ancestors = tuple(r.dna for r in root_first)
            parent_lineage = Lineage(
                parent_dna=parent_row.dna,
                ancestors=parent_lineage_ancestors,
                spawned_by=parent_row.agent_name,
            )
            lineage = Lineage.from_parent(
                parent_dna=parent_row.dna,
                parent_lineage=parent_lineage,
                parent_agent_name=parent_row.agent_name,
            )
        except UnknownAgentError:
            lineage = Lineage.root()
    else:
        lineage = Lineage.root()

    # Render the new voice. Renderer catches provider errors internally
    # and returns a templated VoiceText, so this never raises — same
    # contract as /birth.
    new_voice = _maybe_render_voice(
        enrich=True,
        providers=providers,
        profile=profile,
        engine=engine,
        lineage=lineage,
        settings=settings,
        genre_engine=genre_engine,
    )
    if new_voice is None:
        # _maybe_render_voice only returns None when enrich=False, which
        # we never pass here. Defensive guard.
        raise HTTPException(status_code=500, detail="voice renderer returned None")

    with lock:
        # Patch the soul.md in place. Audit event records before/after.
        prev_provider = frontmatter.get("narrative_provider")
        prev_model = frontmatter.get("narrative_model")
        try:
            update_soul_voice(soul_path, new_voice, encryption_config=_enc_config)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"soul.md voice update failed: {e}",
            ) from e

        event_data = {
            "instance_id": instance_id,
            "agent_name": row.agent_name,
            "role": row.role,
            "previous_provider": prev_provider,
            "previous_model": prev_model,
            "narrative_provider": new_voice.provider,
            "narrative_model": new_voice.model,
            "narrative_generated_at": new_voice.generated_at,
            "soul_path": str(soul_path),
        }
        try:
            entry = audit.append("voice_regenerated", event_data, agent_dna=row.dna)
        except Exception as e:
            # Soul.md was updated but the audit append failed. Don't try
            # to roll back the file — auditors can detect the drift via
            # rebuild-from-artifacts if needed. Surface the error.
            raise HTTPException(
                status_code=500,
                detail=f"audit append failed: {e}",
            ) from e
        registry.register_audit_event(
            _chain_entry_to_parsed(entry), instance_id=instance_id
        )

    return _to_agent_out(registry.get_agent(instance_id))
