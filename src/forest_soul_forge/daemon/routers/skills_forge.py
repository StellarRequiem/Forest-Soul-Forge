"""``/skills/forge`` + ``/skills/install`` + ``/skills/staged`` —
operator-direct skill creation surface (ADR-0057, B201).

The Skill Forge engine itself (``forge.skill_forge.forge_skill``) and
the install path (``cli/install.py::run_skill``) already exist as CLI
flows. This router is a thin HTTP wrapper around them so the SoulUX
frontend can drive the same propose-then-install loop without
shelling out to the CLI.

Endpoints:

  POST   /skills/forge                       — propose stage
  POST   /skills/install                     — install stage
  GET    /skills/staged                      — list pending staged manifests
  DELETE /skills/staged/{name}/{version}     — discard a staged proposal

Auth posture mirrors the rest of the writes surface (``require_writes_enabled``
+ ``require_api_token``). Audit emit policy follows the CLI's
discipline: ``forge_skill_proposed`` on successful propose,
``forge_skill_installed`` on successful install. Both fire under the
daemon's ``app.state.write_lock`` per ADR-0005 single-writer
discipline (B199 audit-chain fork incident).

Smith experimenter (ADR-0056) calls these same endpoints from the
agent side; the chain is shape-compatible regardless of caller.
"""
from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from forest_soul_forge.daemon.deps import (
    get_active_provider,
    get_audit_chain,
    get_settings,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(tags=["skills-forge"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ForgeSkillIn(BaseModel):
    """Input for POST /skills/forge."""

    description: str = Field(
        ...,
        min_length=10,
        max_length=4000,
        description=(
            "Plain-English description of the workflow. The LLM converts this "
            "into a SkillDef YAML manifest. Be specific about inputs, outputs, "
            "and which tools should chain — the propose stage doesn't have "
            "the full tool catalog yet (T1 limitation per ADR-0031), so "
            "vague descriptions yield manifests that reference tools by "
            "common name rather than versioned id."
        ),
    )
    name: str | None = Field(
        None,
        max_length=64,
        description=(
            "Optional override for the skill name. Defaults to a slug derived "
            "from the LLM's manifest output. Use snake_case."
        ),
    )
    version: str = Field(
        "1",
        max_length=16,
        description="Manifest version. Defaults to '1' for first-time forges.",
    )


class ForgedSkillOut(BaseModel):
    """Result of POST /skills/forge."""

    ok: bool
    staged_path: str
    manifest_path: str
    name: str
    version: str
    skill_hash: str
    requires: list[str]
    step_count: int
    audit_seq: int | None
    forge_log_excerpt: str = Field(
        ...,
        description="Last ~600 chars of the forge log — useful for surfacing "
        "LLM warnings or partial-parse notes in the modal.",
    )


class InstallSkillIn(BaseModel):
    """Input for POST /skills/install."""

    staged_path: str = Field(
        ...,
        description=(
            "Absolute path to the staged dir returned by /skills/forge "
            "(the dir that contains manifest.yaml + forge.log). Server-side "
            "validates the path is inside settings.skill_staged_dir to "
            "prevent arbitrary file reads."
        ),
    )
    overwrite: bool = Field(
        False,
        description="If a skill at the target name+version already exists, "
        "overwrite it. Default False rejects the install with 409.",
    )
    force_unknown_tools: bool = Field(
        False,
        description=(
            "B204: if the manifest's `requires` list references tools that "
            "aren't in the live catalog (a hallucinated tool name from the "
            "propose stage, or a tool that was uninstalled since the forge "
            "completed), install refuses with 422 by default. Set to true "
            "to install anyway — the skill won't dispatch successfully "
            "until the missing tools land, but the operator may want to "
            "land a partial skill ahead of installing those tools."
        ),
    )


class InstalledSkillOut(BaseModel):
    """Result of POST /skills/install."""

    ok: bool
    installed_path: str
    name: str
    version: str
    skill_hash: str
    audit_seq: int


class StagedManifestSummary(BaseModel):
    """One row of GET /skills/staged."""

    name: str
    version: str
    staged_path: str
    description_preview: str
    requires: list[str]
    step_count: int
    skill_hash: str
    forged_at: str | None


class StagedManifestsOut(BaseModel):
    count: int
    staged: list[StagedManifestSummary]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_staged_root(settings: Any) -> Path:
    """Resolve the staged-manifests root from settings, with a sane default."""
    root = getattr(settings, "skill_staged_dir", None)
    if root is None:
        # Match the engine's default per forge/skill_forge.py:8.
        root = Path("data/forge/skills/staged")
    return Path(root).resolve()


def _resolve_install_root(settings: Any) -> Path:
    """Resolve the installed-manifests root. B211 uses this to filter
    staged-list entries that have a corresponding installed yaml so
    operators don't see duplicates in the Approvals 'Forged proposals'
    panel after a successful install."""
    root = getattr(settings, "skill_install_dir", None)
    if root is None:
        root = Path("data/forge/skills/installed")
    return Path(root).resolve()


def _ensure_under(parent: Path, child: Path) -> None:
    """Refuse path-traversal attempts. ``child`` must resolve inside ``parent``."""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"path {child} is not under staged root {parent}",
        ) from e


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _operator_label(request: Request) -> str:
    """Best-effort operator label for audit attribution.

    The CLI uses ``resolve_operator()`` which reads $USER. The daemon
    doesn't have that context — it's a long-running process whose
    'operator' is whoever holds the API token. We label the chain
    entry with ``http_api`` so an auditor can distinguish UI-driven
    forges from CLI-driven ones, and include the request's
    User-Agent for context. The X-FSF-Token has already been
    validated by the time this runs.
    """
    ua = request.headers.get("user-agent", "")[:60]
    return f"http_api:{ua}" if ua else "http_api"


# ---------------------------------------------------------------------------
# POST /skills/forge — propose stage
# ---------------------------------------------------------------------------
@router.post(
    "/skills/forge",
    response_model=ForgedSkillOut,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def forge_skill_endpoint(
    body: ForgeSkillIn,
    request: Request,
    settings=Depends(get_settings),
    provider=Depends(get_active_provider),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
) -> ForgedSkillOut:
    """Propose a skill manifest from a description.

    Calls the existing ``forge.skill_forge.forge_skill`` async engine
    (NOT the sync wrapper — we're already in an async route handler,
    so wrapping in asyncio.run would deadlock the loop).

    Emits ``forge_skill_proposed`` on success. The propose stage may
    take multiple seconds (LLM call). The endpoint deliberately does
    not wrap that in write_lock — chain.append is self-protected
    (B199) and the LLM call should not block HTTP routes for the
    duration. The audit emit at the end of this handler is the
    short critical section.
    """
    from forest_soul_forge.forge.skill_forge import forge_skill
    from forest_soul_forge.forge.skill_manifest import ManifestError

    staged_root = _resolve_staged_root(settings)
    staged_root.mkdir(parents=True, exist_ok=True)

    try:
        # B204: pass the live tool_catalog so the propose prompt
        # includes the actual tool inventory. Without this the LLM
        # invents tool names — the B203 smoke produced a manifest
        # referencing the hallucinated text_summarizer.v1 because
        # the engine had no catalog context.
        result = await forge_skill(
            description=body.description,
            provider=provider,
            out_dir=staged_root,
            forged_by=_operator_label(request),
            name_override=body.name,
            version=body.version,
            tool_catalog=getattr(request.app.state, "tool_catalog", None),
        )
    except ManifestError as e:
        # Validation failed at the parser — the LLM produced YAML
        # that doesn't match the SkillDef schema. Surface meaningfully
        # so the modal can show what went wrong rather than a 500.
        # B207: also surface the quarantine dir + forge.log excerpt
        # so the operator can read what the LLM produced. Pre-B207
        # this diagnostic data evaporated on parse failure; the engine
        # now writes the raw output + log BEFORE parsing so it
        # survives. The quarantine dir naming uses either
        # name_override or a timestamp-keyed fallback.
        quarantine_log_excerpt = ""
        quarantine_raw_path = ""
        try:
            # Find the most recent quarantine in staged_root (engine
            # just wrote it). Best-effort — don't fail the error
            # response if the quarantine isn't where we expect.
            from datetime import datetime as _dt
            candidates = sorted(
                staged_root.iterdir(),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
            for cand in candidates[:3]:
                log_f = cand / "forge.log"
                raw_f = cand / "manifest_raw.yaml"
                if log_f.exists() and raw_f.exists():
                    quarantine_log_excerpt = log_f.read_text(encoding="utf-8")[-1200:]
                    quarantine_raw_path = str(cand)
                    break
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "manifest_validation_failed",
                "path": e.path,
                "detail": e.detail,
                "quarantine_dir": quarantine_raw_path,
                "forge_log_excerpt": quarantine_log_excerpt,
            },
        ) from e
    except Exception as e:
        # Provider down, network error, etc. — bucket as 502 so the
        # frontend can distinguish "your input was bad" (422) from
        # "the substrate failed" (502).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "forge_engine_failed",
                "kind": type(e).__name__,
                "message": str(e)[:500],
            },
        ) from e

    skill = result.skill
    forge_log_text = result.log_path.read_text(encoding="utf-8") if result.log_path.exists() else ""

    # Audit emit. Wrapped in write_lock for cross-resource discipline
    # (chain + filesystem advance together); chain.append is itself
    # thread-safe per B199.
    audit_seq: int | None = None
    try:
        with write_lock:
            entry = audit_chain.append(
                "forge_skill_proposed",
                {
                    "skill_name": skill.name,
                    "skill_version": skill.version,
                    "skill_hash": skill.skill_hash,
                    "staged_dir": str(result.staged_dir),
                    "forged_by": _operator_label(request),
                    "mode": "http_api",
                    "step_count": len(skill.steps),
                    "requires": list(skill.requires),
                },
            )
            audit_seq = entry.seq
    except Exception:
        # Best-effort: the engine succeeded and the manifest is
        # staged on disk. A failed audit emit shouldn't lose the
        # operator's work. Surfaces in audit_seq=None so the UI
        # can show a warning if needed.
        pass

    return ForgedSkillOut(
        ok=True,
        staged_path=str(result.staged_dir),
        manifest_path=str(result.manifest_path),
        name=skill.name,
        version=skill.version,
        skill_hash=skill.skill_hash,
        requires=list(skill.requires),
        step_count=len(skill.steps),
        audit_seq=audit_seq,
        forge_log_excerpt=forge_log_text[-600:] if forge_log_text else "",
    )


# ---------------------------------------------------------------------------
# POST /skills/install — install stage
# ---------------------------------------------------------------------------
@router.post(
    "/skills/install",
    response_model=InstalledSkillOut,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def install_skill_endpoint(
    body: InstallSkillIn,
    request: Request,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
) -> InstalledSkillOut:
    """Install a previously-forged staged manifest.

    Mirrors ``cli/install.py::run_skill``. Refuses staged paths
    outside ``settings.skill_staged_dir`` to prevent arbitrary
    file reads from the daemon process. Emits
    ``forge_skill_installed`` on success.
    """
    from forest_soul_forge.core.skill_catalog import load_catalog
    from forest_soul_forge.forge.skill_manifest import (
        ManifestError,
        parse_manifest,
    )

    staged_root = _resolve_staged_root(settings)
    staged_dir = Path(body.staged_path).resolve()
    _ensure_under(staged_root, staged_dir)

    if not staged_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"staged dir not found: {staged_dir}",
        )
    manifest_path = staged_dir / "manifest.yaml"
    if not manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"no manifest.yaml in {staged_dir} — is this a Skill Forge "
                "staged folder?"
            ),
        )

    # Validate before copying — same loader the daemon uses.
    try:
        skill = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except ManifestError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "manifest_validation_failed",
                "path": e.path,
                "detail": e.detail,
            },
        ) from e

    # B204: cross-check requires[] against the live tool catalog.
    # Pre-B204 the install path validated only the manifest schema,
    # not whether the referenced tools actually exist. The LLM
    # propose stage hallucinated tool names (B203 smoke produced
    # text_summarizer.v1 which doesn't exist) and install would
    # have happily landed an unrunnable manifest. Catch that here.
    catalog = getattr(request.app.state, "tool_catalog", None)
    if catalog is not None and not body.force_unknown_tools:
        catalog_keys = set(getattr(catalog, "tools", {}).keys())
        unknown = [
            t for t in skill.requires
            if t not in catalog_keys
        ]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "unknown_tools_referenced",
                    "unknown_tools": sorted(unknown),
                    "skill_name": skill.name,
                    "skill_version": skill.version,
                    "hint": (
                        "The manifest references tools that aren't in the "
                        "live catalog. This usually means the propose stage "
                        "hallucinated a tool name. Either edit the manifest "
                        "to use a real tool (llm_think.v1 is the closest "
                        "general-purpose match) or pass force_unknown_tools=true "
                        "to install anyway (the skill won't dispatch until the "
                        "missing tools land)."
                    ),
                },
            )

    install_dir = Path(getattr(settings, "skill_install_dir", "data/forge/skills/installed")).resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / f"{skill.name}.v{skill.version}.yaml"

    if target.exists() and not body.overwrite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{target.name} already installed. Pass overwrite=true to "
                "replace, or discard the staged manifest if this was a "
                "duplicate forge."
            ),
        )

    # Copy + audit + reload — all under the write_lock so the
    # in-memory catalog can't be observed half-swapped by a
    # concurrent /skills/run.
    with write_lock:
        shutil.copyfile(manifest_path, target)

        entry = audit_chain.append(
            "forge_skill_installed",
            {
                "skill_name": skill.name,
                "skill_version": skill.version,
                "skill_hash": skill.skill_hash,
                "installed_from": str(staged_dir),
                "installed_to": str(target),
                "installed_by": _operator_label(request),
                "mode": "http_api",
            },
        )

        # Reload the in-memory catalog so subsequent /skills GET +
        # /agents/{id}/skills/run see the new skill without needing
        # a daemon restart. Best-effort — a failed reload doesn't
        # roll back the install (the manifest is on disk).
        try:
            catalog, _errors = load_catalog(install_dir)
            request.app.state.skill_catalog = catalog
        except Exception:
            pass

    return InstalledSkillOut(
        ok=True,
        installed_path=str(target),
        name=skill.name,
        version=skill.version,
        skill_hash=skill.skill_hash,
        audit_seq=entry.seq,
    )


# ---------------------------------------------------------------------------
# GET /skills/staged — list pending proposals
# ---------------------------------------------------------------------------
@router.get(
    "/skills/staged",
    response_model=StagedManifestsOut,
    dependencies=[Depends(require_api_token)],
)
async def list_staged_skills(
    settings=Depends(get_settings),
) -> StagedManifestsOut:
    """List staged skill proposals awaiting install.

    Read-only. Returns an empty list if the staged dir doesn't
    exist or has no manifests. Malformed manifests are skipped
    silently — they're logged at forge time, no point in failing
    a list query because of one bad propose.
    """
    from forest_soul_forge.forge.skill_manifest import (
        ManifestError,
        parse_manifest,
    )

    staged_root = _resolve_staged_root(settings)
    if not staged_root.exists():
        return StagedManifestsOut(count=0, staged=[])

    # B211: filter out entries that have a corresponding installed
    # manifest. Pre-B211 the Approvals 'Forged proposals' panel kept
    # showing artifacts after install, since the staged dir stays on
    # disk (deliberately — it's the propose audit trail). Operators
    # only want to see PENDING proposals in that panel; the install
    # registry is browsed elsewhere.
    install_root = _resolve_install_root(settings)

    rows: list[StagedManifestSummary] = []
    for staged_dir in sorted(staged_root.iterdir()):
        if not staged_dir.is_dir():
            continue
        manifest_path = staged_dir / "manifest.yaml"
        if not manifest_path.exists():
            continue
        try:
            skill = parse_manifest(manifest_path.read_text(encoding="utf-8"))
        except ManifestError:
            continue
        # B211: skip if already installed. Match on canonical
        # name.vversion.yaml filename per /skills/install convention.
        installed_path = install_root / f"{skill.name}.v{skill.version}.yaml"
        if installed_path.exists():
            continue
        # Heuristic: forged_at = manifest mtime, ISO-formatted.
        try:
            mtime = datetime.fromtimestamp(
                manifest_path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            mtime = None
        rows.append(StagedManifestSummary(
            name=skill.name,
            version=skill.version,
            staged_path=str(staged_dir),
            description_preview=(skill.description or "")[:200].strip(),
            requires=list(skill.requires),
            step_count=len(skill.steps),
            skill_hash=skill.skill_hash,
            forged_at=mtime,
        ))

    return StagedManifestsOut(count=len(rows), staged=rows)


# ---------------------------------------------------------------------------
# DELETE /skills/staged/{name}/{version} — discard
# ---------------------------------------------------------------------------
@router.delete(
    "/skills/staged/{name}/{version}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def discard_staged_skill(
    name: str,
    version: str,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
    request: Request = None,
) -> dict[str, Any]:
    """Discard a staged proposal.

    Removes the staged dir and emits a ``forge_skill_proposed`` event
    with ``mode: discarded`` so the chain records the operator's
    rejection rather than just a silent rm. Future filter-by-mode
    queries can distinguish 'this was abandoned' from 'this is
    awaiting install'.
    """
    staged_root = _resolve_staged_root(settings)
    staged_dir = (staged_root / f"{name}.v{version}").resolve()
    _ensure_under(staged_root, staged_dir)

    if not staged_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"staged proposal not found: {name} v{version}",
        )

    with write_lock:
        try:
            shutil.rmtree(staged_dir)
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to remove staged dir: {e}",
            ) from e

        # Distinct mode field so an auditor reading the chain can
        # filter discarded proposals separately from in-flight ones.
        # The original forge_skill_proposed event from the propose
        # call still stands; this is a deliberate second event
        # rather than a retraction.
        audit_chain.append(
            "forge_skill_proposed",
            {
                "skill_name": name,
                "skill_version": version,
                "staged_dir": str(staged_dir),
                "mode": "discarded",
                "discarded_by": _operator_label(request) if request else "http_api",
                "discarded_at": _now_iso(),
            },
        )

    return {"ok": True, "discarded": f"{name}.v{version}"}


# ---------------------------------------------------------------------------
# DELETE /skills/installed/{name}/{version} — uninstall (B212)
# ---------------------------------------------------------------------------
@router.delete(
    "/skills/installed/{name}/{version}",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def uninstall_skill(
    name: str,
    version: str,
    settings=Depends(get_settings),
    audit_chain=Depends(get_audit_chain),
    write_lock: threading.RLock = Depends(get_write_lock),
    request: Request = None,
) -> dict[str, Any]:
    """Uninstall an installed skill.

    Removes the canonical ``data/forge/skills/installed/<name>.v<version>.yaml``
    and emits a ``forge_skill_uninstalled`` chain event. Pre-B212 the
    only path was ``rm + daemon restart``, which left the audit chain
    silent about who removed what and when.

    The in-memory ``app.state.skill_catalog`` is reloaded best-effort
    after the file is gone so subsequent GET /skills sees the new
    state without a restart. Currently-dispatching skills aren't
    cancelled; the runtime decision was made at start-of-dispatch.

    Returns 404 when the skill isn't installed. The staged proposal
    (if any) survives this operation — discard it separately via
    DELETE /skills/staged/{name}/{version} if desired.
    """
    install_root = _resolve_install_root(settings)
    target = (install_root / f"{name}.v{version}.yaml").resolve()
    _ensure_under(install_root, target)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"installed skill not found: {name} v{version}",
        )

    with write_lock:
        try:
            target.unlink()
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to remove installed manifest: {e}",
            ) from e

        audit_chain.append(
            "forge_skill_uninstalled",
            {
                "skill_name": name,
                "skill_version": version,
                "uninstalled_from": str(target),
                "uninstalled_by": _operator_label(request) if request else "http_api",
                "uninstalled_at": _now_iso(),
                "mode": "http_api",
            },
        )

        # Best-effort reload of the in-memory skill catalog so
        # subsequent /skills GET reflects the removal. Failure to
        # reload doesn't roll back the unlink — the file is gone.
        try:
            from forest_soul_forge.core.skill_catalog import load_catalog
            catalog, _errors = load_catalog(install_root)
            request.app.state.skill_catalog = catalog
        except Exception:
            pass

    return {"ok": True, "uninstalled": f"{name}.v{version}"}
