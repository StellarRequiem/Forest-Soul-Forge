"""``/marketplace/index`` — ADR-0055 M1 (Burst 184) browse endpoint.

Aggregates every configured marketplace registry into a single
deduplicated index that the frontend Browse pane consumes.

Per ADR-0055 Decision 4, this is one of TWO marketplace endpoints
the kernel ever owns. The other (POST /marketplace/install) is M3.
Everything else — UI, registry schema, signing tools, ratings —
lives in the ``forest-marketplace`` sibling repo.

Caching strategy:

  - First call after lifespan boot fetches every configured
    registry, parses, validates, merges, stores the result + a
    timestamp on app.state. Subsequent calls within
    ``marketplace_cache_ttl_s`` return the cached value.
  - On TTL expiry the next call re-fetches synchronously. If a
    registry now fails (network down, file deleted, malformed
    YAML), the kernel returns the last-known-good entries from
    that registry alongside the entries from registries that
    still succeeded. The response carries ``stale: true`` so the
    UI can surface a soft warning.
  - The empty-registries case ('operator hasn't opted in yet')
    returns an empty entries list with ``stale: false`` so the
    UI distinguishes 'no registries configured' from 'fetch
    failed.'

Trust posture:

  - M1 reports ``trusted: false`` on every entry. M6 will compute
    this from manifest_signature + the configured
    marketplace_trusted_keys.
  - The frontend renders an 'untrusted' badge on every entry
    until M6; install (M3) is still permitted with operator
    confirmation in the UI.

Read-only endpoint; only the standard FSF_API_TOKEN is required.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover — daemon extra
    httpx = None  # type: ignore

from fastapi import APIRouter, Depends, HTTPException, Request

from forest_soul_forge.daemon.deps import require_api_token
from forest_soul_forge.daemon.schemas import (
    MarketplaceContributes,
    MarketplaceContributesTool,
    MarketplaceEntryOut,
    MarketplaceIndexOut,
    MarketplaceReview,
)


router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# ---------------------------------------------------------------------------
# Cache shape — kept on app.state.marketplace_cache.
# ---------------------------------------------------------------------------
# A small dict so the lifespan can pre-populate / reset without
# importing this module's internals. Touched only via the helpers
# below; no direct reads from the endpoint.
def _empty_cache() -> dict[str, Any]:
    return {
        "fetched_at_monotonic": 0.0,   # time.monotonic() of last fetch
        "fetched_at_iso":       "",    # ISO timestamp shown to UI
        "entries":              [],    # list[MarketplaceEntryOut]
        "failed_registries":    [],    # list[str]
        "per_registry_lkg":     {},    # registry_url -> last-known-good
                                       # list[MarketplaceEntryOut]
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Per-registry fetcher — file:// + http(s):// supported.
# ---------------------------------------------------------------------------
async def _fetch_registry_yaml(url: str, *, timeout_s: float = 10.0) -> str:
    """Return the raw YAML text for one registry URL.

    Raises ``RuntimeError`` with a short reason on any failure. The
    aggregator catches this and records the URL in the failed-
    registries list rather than propagating.
    """
    parsed = urlparse(url)
    if parsed.scheme == "file":
        # file:///abs/path → read directly. Keeps tests trivial
        # (no httpx stubbing for local fixtures).
        path = Path(parsed.path)
        if not path.exists():
            raise RuntimeError(f"file not found: {path}")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"file read error: {e}") from e
    elif parsed.scheme in ("http", "https"):
        if httpx is None:
            raise RuntimeError(
                "httpx not installed; install the [daemon] extra "
                "to fetch remote registries"
            )
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except httpx.RequestError as e:
            raise RuntimeError(f"network error: {e}") from e
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"HTTP {e.response.status_code}: {e.response.text[:120]}"
            ) from e
    else:
        raise RuntimeError(
            f"unsupported scheme {parsed.scheme!r} in {url!r}; "
            f"only file:// and http(s):// are supported"
        )


# ---------------------------------------------------------------------------
# YAML → MarketplaceEntryOut.
#
# The marketplace.yaml file is one of two shapes:
#
#   1. A flat list of full entries (inline).
#   2. A document with `schema_version: 1` + `entries: [...]` where each
#      entry is a full inline entry.
#   3. A document with `schema_version: 1` + `entries:` listing
#      filenames under registry/entries/ that the kernel resolves.
#
# v0.1 of forest-marketplace ships shape 3 (per-file entries). We
# resolve the references by reading siblings of the marketplace.yaml
# path. Pure-list (shapes 1+2) stay supported so a flat single-file
# registry is also legal — operators publishing a quick prototype
# don't need the entries/ directory.
# ---------------------------------------------------------------------------
def _parse_registry(
    raw_yaml: str,
    *,
    source_registry: str,
) -> list[MarketplaceEntryOut]:
    """Parse one registry's raw YAML into a list of validated
    MarketplaceEntryOut. Raises ``RuntimeError`` on any structural
    problem. Per-entry validation errors skip THAT entry but
    don't fail the whole registry — operators publishing a
    multi-entry registry don't get blocked by one malformed row.
    """
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise RuntimeError(f"YAML parse error: {e}") from e

    raw_entries: list[dict[str, Any]] = []

    if isinstance(data, list):
        # Shape 1: flat list.
        raw_entries = [e for e in data if isinstance(e, dict)]
    elif isinstance(data, dict):
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise RuntimeError(
                "registry doc must have an `entries:` list "
                "or be a flat list at the top level"
            )
        # Shape 2 (inline) vs shape 3 (filename references) is
        # decided per-element. A string element is interpreted as
        # a path RELATIVE to the registry source. Loading those
        # requires the source_registry to be a file:// URL — for
        # https:// the only supported shape is fully inlined
        # entries (so the kernel doesn't make N+1 HTTP calls per
        # browse refresh).
        for elem in entries:
            if isinstance(elem, dict):
                raw_entries.append(elem)
            elif isinstance(elem, str):
                raw_entries.append(_resolve_filename_entry(
                    elem, source_registry,
                ))
            # Other types silently dropped — schema enforces dict
            # or string.
    else:
        raise RuntimeError(
            f"registry root must be a dict or list, got {type(data).__name__}"
        )

    parsed: list[MarketplaceEntryOut] = []
    for raw in raw_entries:
        try:
            entry = _coerce_entry(raw, source_registry=source_registry)
        except (ValueError, KeyError, TypeError):
            # Bad entry — skip; the rest of the registry is fine.
            # M6 will surface per-entry errors via a structured
            # /marketplace/diagnostics endpoint. M1 just drops them.
            continue
        parsed.append(entry)
    return parsed


def _resolve_filename_entry(
    relative_path: str, source_registry: str,
) -> dict[str, Any]:
    """Read a per-entry YAML file referenced from marketplace.yaml.

    Only supported when the source_registry is a file:// URL —
    HTTP fetches don't follow filename references (the registry
    must inline its entries to publish over HTTPS). Raises
    ``RuntimeError`` on any I/O problem.
    """
    parsed = urlparse(source_registry)
    if parsed.scheme != "file":
        raise RuntimeError(
            f"filename references only supported for file:// "
            f"registries; {source_registry!r} uses {parsed.scheme!r}"
        )
    base = Path(parsed.path).parent  # registry/marketplace.yaml's
                                     # directory
    target = (base / relative_path).resolve()
    # Defense against ../ escape — target must stay under base.
    try:
        target.relative_to(base.resolve())
    except ValueError as e:
        raise RuntimeError(
            f"entry path {relative_path!r} escapes registry root"
        ) from e
    if not target.exists():
        raise RuntimeError(
            f"entry file {target} referenced by registry not found"
        )
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"entry file read error: {e}") from e
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RuntimeError(f"entry YAML parse error: {e}") from e
    if not isinstance(loaded, dict):
        raise RuntimeError(
            f"entry file {target} must contain a YAML mapping"
        )
    return loaded


def _coerce_entry(
    raw: dict[str, Any], *, source_registry: str,
) -> MarketplaceEntryOut:
    """Turn a raw dict from the YAML into a validated
    MarketplaceEntryOut, attaching ``source_registry`` and the
    M1-default ``trusted=false`` flag.
    """
    contributes_raw = raw.get("contributes") or {}
    if not isinstance(contributes_raw, dict):
        raise ValueError("contributes must be a mapping")
    tools_raw = contributes_raw.get("tools") or []
    tools: list[MarketplaceContributesTool] = []
    for t in tools_raw:
        if not isinstance(t, dict):
            continue
        tools.append(MarketplaceContributesTool(
            name=str(t.get("name", "")),
            version=str(t.get("version", "")),
            side_effects=str(t.get("side_effects", "read_only")),
        ))
    contributes = MarketplaceContributes(
        tools=tools,
        skills=[str(s) for s in (contributes_raw.get("skills") or []) if s],
        mcp_servers=[
            str(s) for s in (contributes_raw.get("mcp_servers") or []) if s
        ],
    )

    reviewed_raw = raw.get("reviewed_by") or []
    reviews: list[MarketplaceReview] = []
    for r in reviewed_raw:
        if not isinstance(r, dict):
            continue
        try:
            reviews.append(MarketplaceReview(
                reviewer=str(r.get("reviewer", "")),
                date=str(r.get("date", "")),
                verdict=str(r.get("verdict", "approved")),
                audit_url=r.get("audit_url"),
                notes=r.get("notes"),
            ))
        except Exception:
            # Drop malformed individual reviews; entry stays.
            continue

    return MarketplaceEntryOut(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        version=str(raw.get("version", "")),
        author=str(raw.get("author", "")),
        source_url=str(raw.get("source_url", "")),
        download_url=str(raw.get("download_url", "")),
        download_sha256=str(raw.get("download_sha256", "")),
        manifest_signature=raw.get("manifest_signature"),
        description=str(raw.get("description", "")),
        permissions_summary=str(raw.get("permissions_summary", "")),
        contributes=contributes,
        archetype_tags=[
            str(t) for t in (raw.get("archetype_tags") or []) if t
        ],
        highest_side_effect_tier=str(
            raw.get("highest_side_effect_tier", "read_only")
        ),
        required_secrets=[
            str(s) for s in (raw.get("required_secrets") or []) if s
        ],
        minimum_kernel_version=raw.get("minimum_kernel_version"),
        reviewed_by=reviews,
        source_registry=source_registry,
        trusted=False,   # M6 will compute from signature + keys
    )


# ---------------------------------------------------------------------------
# Aggregator — fans out across configured registries, merges, caches.
# ---------------------------------------------------------------------------
async def _refresh_index(
    registries: list[str],
    *,
    cache: dict[str, Any],
) -> None:
    """Fan out across every configured registry, merge entries,
    and update the cache in place. Per-registry failures populate
    failed_registries + reuse the per-registry last-known-good.
    """
    fresh_per_registry: dict[str, list[MarketplaceEntryOut]] = {}
    failed: list[str] = []

    # Sequential fetch — registries are typically 1-3, no point
    # adding asyncio.gather complexity. If a future operator
    # configures dozens, switch to gather for parallelism.
    for url in registries:
        try:
            text = await _fetch_registry_yaml(url)
            entries = _parse_registry(text, source_registry=url)
            fresh_per_registry[url] = entries
            cache["per_registry_lkg"][url] = entries
        except RuntimeError:
            failed.append(url)

    # Build the merged list. For each configured registry, prefer
    # the fresh entries; on failure, fall back to last-known-good.
    merged: list[MarketplaceEntryOut] = []
    seen_ids: set[tuple[str, str]] = set()
    for url in registries:
        entries = fresh_per_registry.get(url) or cache["per_registry_lkg"].get(url, [])
        for e in entries:
            # Dedup by (id, source_registry) so the same id from
            # two registries shows twice (operator can compare),
            # but the same id from one registry shows once.
            key = (e.id, e.source_registry)
            if key in seen_ids:
                continue
            seen_ids.add(key)
            merged.append(e)

    cache["fetched_at_monotonic"] = time.monotonic()
    cache["fetched_at_iso"] = _now_iso()
    cache["entries"] = merged
    cache["failed_registries"] = failed


def _get_or_init_cache(request: Request) -> dict[str, Any]:
    """Return app.state.marketplace_cache, initializing the dict
    on first access. The lifespan COULD pre-populate; deferring
    to first use keeps the lifespan slim and means a daemon that
    never calls /marketplace/index never spends a fetch budget.
    """
    state = request.app.state
    cache = getattr(state, "marketplace_cache", None)
    if cache is None:
        cache = _empty_cache()
        state.marketplace_cache = cache
    return cache


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.get(
    "/index",
    response_model=MarketplaceIndexOut,
    dependencies=[Depends(require_api_token)],
)
async def get_marketplace_index(request: Request) -> MarketplaceIndexOut:
    settings = request.app.state.settings
    registries = list(settings.marketplace_registries or [])
    ttl_s = int(settings.marketplace_cache_ttl_s or 0)
    cache = _get_or_init_cache(request)

    # Refresh if cache is empty or TTL expired.
    age_s = (
        time.monotonic() - cache["fetched_at_monotonic"]
        if cache["fetched_at_monotonic"] else None
    )
    needs_refresh = (
        age_s is None
        or (ttl_s == 0)
        or (age_s >= ttl_s)
    )

    if needs_refresh and registries:
        await _refresh_index(registries, cache=cache)
    elif not registries:
        # Operator hasn't opted in. Empty entries, NOT stale.
        cache["entries"] = []
        cache["failed_registries"] = []
        if not cache["fetched_at_iso"]:
            cache["fetched_at_iso"] = _now_iso()

    return MarketplaceIndexOut(
        schema_version=1,
        entries=list(cache["entries"]),
        fetched_at=cache["fetched_at_iso"] or _now_iso(),
        cache_ttl_s=ttl_s,
        # ``stale`` only meaningful when at least one registry was
        # configured — if none, we're empty-by-design, not stale.
        stale=bool(registries) and bool(cache["failed_registries"]),
        failed_registries=list(cache["failed_registries"]),
        configured_registries=list(registries),
    )


# ---------------------------------------------------------------------------
# ADR-0055 M3 (B227) — POST /marketplace/install
# ---------------------------------------------------------------------------
# The other half of the marketplace loop. Looks up an entry in the
# cached index, downloads its payload, SHA-verifies, extracts (if a
# tarball) or copies (if a directory), and installs into the local
# plugin repo. Emits ``marketplace_plugin_installed`` so the audit
# chain captures provenance.
#
# M6 signing enforcement is queued — for now the manifest_signature
# field is recorded in the chain event but not verified against
# marketplace_trusted_keys. Operators see an "untrusted" badge on
# every entry in the browse pane until M6.

import hashlib
import shutil
import tarfile
import tempfile
import threading
from pydantic import BaseModel, Field as _PydanticField


class MarketplaceInstallIn(BaseModel):
    registry_id: str = _PydanticField(
        ...,
        description=(
            "The registry URL the entry came from (matches the "
            "source_registry field on the index entry). Distinguishes "
            "the same entry_id across multiple registries."
        ),
    )
    entry_id: str = _PydanticField(
        ..., min_length=1, max_length=128,
        description="The entry's id field (typically the plugin name).",
    )
    version: str | None = _PydanticField(
        None, max_length=32,
        description=(
            "Optional explicit version. Defaults to the entry's "
            "current version in the cached index — use this to pin to "
            "a specific historical release."
        ),
    )
    force: bool = _PydanticField(
        False,
        description=(
            "Overwrite an existing plugin at the target path. Without "
            "this, install fails 409 when the plugin is already there."
        ),
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def _download_to(url: str, target: Path, *, timeout_s: float = 60.0) -> None:
    """Fetch the URL into ``target``. Supports file:// and http(s)://.
    Raises RuntimeError on any failure (caller converts to HTTPException)."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        src = Path(parsed.path)
        if not src.exists():
            raise RuntimeError(f"file not found: {src}")
        if src.is_file():
            shutil.copy2(src, target)
        else:
            # Directory source — caller handles this differently via
            # _install_from_url. Shouldn't hit here.
            raise RuntimeError(f"source is a directory, not a file: {src}")
        return
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(
            f"unsupported download scheme {parsed.scheme!r} in {url!r}"
        )
    if httpx is None:
        raise RuntimeError(
            "httpx not installed; install the [daemon] extra to fetch "
            "remote payloads"
        )
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(target, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
    except httpx.RequestError as e:
        raise RuntimeError(f"network error: {e}") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"HTTP {e.response.status_code} fetching payload"
        ) from e


@router.post(
    "/install",
    dependencies=[Depends(require_api_token)],
)
async def install_marketplace_entry(
    body: MarketplaceInstallIn,
    request: Request,
) -> dict[str, Any]:
    """Install a marketplace entry into the local plugin repo.

    Lookup → download → SHA verify → install → audit. Each step
    raises an HTTPException with a descriptive detail so an operator
    debugging from the SoulUX response panel can see exactly which
    step failed.
    """
    settings = request.app.state.settings
    if not getattr(settings, "allow_write_endpoints", True):
        raise HTTPException(
            status_code=403,
            detail="writes are disabled (allow_write_endpoints=False)",
        )

    cache = _get_or_init_cache(request)
    # Match by (source_registry, id). Don't trust the operator's
    # registry_id blindly — verify it matches an actual cached entry,
    # so a stale or hallucinated registry URL refuses cleanly.
    matches = [
        e for e in cache.get("entries") or []
        if getattr(e, "id", None) == body.entry_id
        and getattr(e, "source_registry", None) == body.registry_id
    ]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=(
                f"entry {body.entry_id!r} not found in registry "
                f"{body.registry_id!r}. Refresh the index (the cached "
                "entries may be stale) or check the registry URL."
            ),
        )
    if len(matches) > 1:
        # Should never happen — the index aggregator dedupes — but
        # surface it explicitly so an auditor can diagnose later.
        raise HTTPException(
            status_code=500,
            detail=(
                f"{len(matches)} entries match {body.entry_id!r} in "
                f"{body.registry_id!r}; index aggregator has a bug."
            ),
        )
    entry = matches[0]

    # Pin version: prefer the explicit body.version, else the entry's
    # current version. Mismatch is operator error.
    expected_version = body.version or getattr(entry, "version", None)
    if not expected_version:
        raise HTTPException(
            status_code=400,
            detail=f"entry {body.entry_id!r} has no version field",
        )
    if body.version and body.version != getattr(entry, "version", None):
        raise HTTPException(
            status_code=409,
            detail=(
                f"requested version {body.version} doesn't match "
                f"the cached entry's version "
                f"{getattr(entry, 'version', None)!r}. The marketplace "
                "has updated since the operator's last refresh."
            ),
        )

    download_url = getattr(entry, "download_url", None)
    if not download_url:
        raise HTTPException(
            status_code=400,
            detail=f"entry {body.entry_id!r} has no download_url",
        )
    expected_sha = getattr(entry, "download_sha256", None)

    # Get the plugin repo for the install target.
    runtime = getattr(request.app.state, "plugin_runtime", None)
    if runtime is None or runtime.repository is None:
        raise HTTPException(
            status_code=503,
            detail="plugin runtime not initialized",
        )
    repo = runtime.repository

    audit_chain = getattr(request.app.state, "audit_chain", None)
    if audit_chain is None:
        raise HTTPException(
            status_code=503,
            detail="audit chain not initialized",
        )

    write_lock = getattr(request.app.state, "write_lock", None) or threading.RLock()

    # Two payload shapes supported:
    #   1. file:// pointing at a directory — direct install_from_dir
    #   2. file:// or http(s):// pointing at a tarball — download +
    #      SHA verify + extract + install_from_dir
    parsed = urlparse(download_url)
    parsed_path = Path(parsed.path) if parsed.scheme == "file" else None
    is_directory = (
        parsed.scheme == "file"
        and parsed_path is not None
        and parsed_path.is_dir()
    )

    with tempfile.TemporaryDirectory(prefix="fsf-marketplace-") as tmpdir:
        tmp_root = Path(tmpdir)
        if is_directory:
            # Skip the SHA check on directory installs — the operator
            # is pointing at a local dev source, and there's no file
            # to hash. The marketplace SHOULD still publish a SHA
            # over the tarball form; this branch is dev convenience.
            staging = parsed_path
        else:
            payload = tmp_root / "payload.plugin"
            try:
                await _download_to(download_url, payload)
            except RuntimeError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"download failed: {e}",
                ) from e
            if expected_sha:
                actual_sha = _sha256_file(payload)
                if actual_sha != expected_sha:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"sha256 mismatch: payload {actual_sha} != "
                            f"manifest {expected_sha}. Refusing to install "
                            "a tampered or wrong-version payload."
                        ),
                    )
            extract_dir = tmp_root / "extract"
            extract_dir.mkdir()
            try:
                with tarfile.open(payload, "r:*") as tf:
                    # Defense in depth — refuse absolute paths and
                    # parent-directory traversal in archive members.
                    for m in tf.getmembers():
                        if m.name.startswith("/") or ".." in Path(m.name).parts:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"refusing tarball with unsafe member "
                                    f"path {m.name!r}"
                                ),
                            )
                    tf.extractall(extract_dir)
            except tarfile.TarError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"payload is not a valid tar archive: {e}",
                ) from e
            # Tarball may include either the plugin dir at the root
            # OR the plugin's contents directly. Normalize by finding
            # the plugin.yaml.
            yaml_paths = list(extract_dir.rglob("plugin.yaml"))
            if not yaml_paths:
                raise HTTPException(
                    status_code=400,
                    detail="extracted tarball contains no plugin.yaml",
                )
            staging = yaml_paths[0].parent

        try:
            with write_lock:
                info = repo.install_from_dir(staging, force=body.force)
        except Exception as e:
            # Includes PluginAlreadyInstalled, PluginValidationError.
            raise HTTPException(
                status_code=409,
                detail=f"install failed: {e}",
            ) from e

    # Reload the plugin runtime so the new plugin's tools are
    # registered without a daemon restart.
    try:
        runtime.reload()
    except Exception:
        # Reload failure is non-fatal — the file is on disk; next
        # restart picks it up. Log via audit anyway (below).
        pass

    operator = getattr(request.state, "operator_id", None)
    entry_payload = {
        "registry_id": body.registry_id,
        "entry_id": body.entry_id,
        "version": expected_version,
        "plugin_name": info.name,
        "download_url": download_url,
        "download_sha256": expected_sha,
        "manifest_signature": getattr(entry, "manifest_signature", None),
        "installed_by": operator,
        "trusted": False,  # M6 will flip this when signature verification ships
    }
    with write_lock:
        audit_chain.append("marketplace_plugin_installed", entry_payload)

    return {
        "ok": True,
        "plugin_name": info.name,
        "version": info.manifest.version,
        "directory": str(info.directory),
        "trusted": False,
        "audit_event": "marketplace_plugin_installed",
    }
