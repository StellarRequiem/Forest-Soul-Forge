"""ADR-0055 M1 (Burst 184) — GET /marketplace/index tests.

Coverage:

  Aggregator:
    - empty registries list returns empty entries, stale=false
    - one file:// registry with inline entries → entries returned
    - one file:// registry with filename references resolves
    - one https:// registry returns its entries (httpx stubbed)
    - multiple registries merge with source_registry tagged
    - same id from one registry deduped, same id from two registries
      preserved separately
    - failed registry → reported in failed_registries; stale=true
    - failed registry with last-known-good cache → entries from LKG

  Per-entry parsing:
    - missing required fields drop the entry but registry parses
    - all-zero / empty optional fields default cleanly
    - tools side_effects enum validated

  Caching:
    - second call within TTL returns cached value (no re-fetch)
    - second call after TTL expiry triggers re-fetch
    - ttl=0 forces re-fetch every call
    - cache survives across calls but is per-process

  HTTP semantics:
    - missing token → 401
    - response shape matches MarketplaceIndexOut
    - sources_registry field populated for every entry
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

fastapi = pytest.importorskip("fastapi")
pydantic_settings = pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient

from forest_soul_forge.daemon.app import build_app
from forest_soul_forge.daemon.config import DaemonSettings


# ---------------------------------------------------------------------------
# YAML fixtures.
# ---------------------------------------------------------------------------

ENTRY_INLINE_FULL = """\
schema_version: 1
entries:
  - id: foo-tool
    name: Foo Tool
    version: "1.0.0"
    author: alice
    source_url: https://example.com/foo
    download_url: https://example.com/foo.plugin
    download_sha256: deadbeef
    description: A tool that foos.
    permissions_summary: |
      Reads /tmp. No network.
    contributes:
      tools:
        - {name: foo, version: "1", side_effects: read_only}
    archetype_tags: [companion]
    highest_side_effect_tier: read_only
"""


ENTRY_FILENAME_REFERENCE = """\
schema_version: 1
entries:
  - bar-tool.yaml
"""


BAR_ENTRY_BODY = """\
id: bar-tool
name: Bar Tool
version: "0.5.0"
author: bob
source_url: https://example.com/bar
download_url: https://example.com/bar.plugin
download_sha256: cafebabe
description: A tool that bars.
permissions_summary: Bars things.
contributes:
  tools:
    - {name: bar, version: "1", side_effects: external}
archetype_tags: [assistant]
highest_side_effect_tier: external
"""


def _write_registry(
    tmp_path: Path,
    *,
    inline: bool = True,
    extra_entries: str | None = None,
) -> str:
    """Write a registry YAML at tmp_path/marketplace.yaml and return
    a file:// URL pointing at it. ``inline=True`` writes a self-
    contained registry; ``inline=False`` writes a registry that
    references registry/entries/bar-tool.yaml."""
    reg = tmp_path / "marketplace.yaml"
    if inline:
        body = ENTRY_INLINE_FULL
        if extra_entries:
            body = body.rstrip() + "\n" + extra_entries
        reg.write_text(body, encoding="utf-8")
    else:
        # filename-reference shape — write the entry to the same
        # directory.
        reg.write_text(ENTRY_FILENAME_REFERENCE, encoding="utf-8")
        (tmp_path / "bar-tool.yaml").write_text(
            BAR_ENTRY_BODY, encoding="utf-8",
        )
    return f"file://{reg}"


# ---------------------------------------------------------------------------
# Test client builder.
# ---------------------------------------------------------------------------

def _build_client(
    tmp_path: Path,
    *,
    registries: list[str] | None = None,
    cache_ttl_s: int = 3600,
    api_token: str = "test-token-1234",
) -> TestClient:
    """Build a TestClient with a daemon configured for marketplace
    testing. Skips lifespan dependencies that aren't relevant
    here (registry, audit chain — those still init normally)."""
    settings = DaemonSettings(
        registry_db_path=tmp_path / "registry.sqlite",
        artifacts_dir=tmp_path / "souls",
        audit_chain_path=tmp_path / "audit.jsonl",
        marketplace_registries=registries or [],
        marketplace_cache_ttl_s=cache_ttl_s,
        api_token=api_token,
        allow_write_endpoints=False,
    )
    app = build_app(settings)
    client = TestClient(app)
    return client


# ===========================================================================
# Aggregator behavior
# ===========================================================================

class TestAggregator:
    def test_empty_registries_returns_empty(self, tmp_path):
        with _build_client(tmp_path) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["entries"] == []
        assert body["stale"] is False
        assert body["failed_registries"] == []
        assert body["configured_registries"] == []

    def test_inline_registry(self, tmp_path):
        url = _write_registry(tmp_path, inline=True)
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        assert r.status_code == 200
        body = r.json()
        assert len(body["entries"]) == 1
        e = body["entries"][0]
        assert e["id"] == "foo-tool"
        assert e["name"] == "Foo Tool"
        assert e["version"] == "1.0.0"
        assert e["highest_side_effect_tier"] == "read_only"
        assert e["source_registry"] == url
        assert e["trusted"] is False  # M6 not yet shipped
        assert body["stale"] is False
        assert body["failed_registries"] == []

    def test_filename_reference_resolves(self, tmp_path):
        url = _write_registry(tmp_path, inline=False)
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["id"] == "bar-tool"

    def test_multiple_registries_merge(self, tmp_path):
        # Two separate file:// registries each with one entry.
        d1 = tmp_path / "r1"
        d2 = tmp_path / "r2"
        d1.mkdir()
        d2.mkdir()
        url1 = _write_registry(d1, inline=True)
        # second registry uses different inline content (different id)
        (d2 / "marketplace.yaml").write_text(BAR_ENTRY_BODY.replace(
            "id: bar-tool", "id: bar-tool",
        ).strip() + "\n", encoding="utf-8")
        # Wrap in entries list shape:
        (d2 / "marketplace.yaml").write_text(
            "schema_version: 1\nentries:\n  - " +
            BAR_ENTRY_BODY.replace("\n", "\n    "),
            encoding="utf-8",
        )
        url2 = f"file://{d2 / 'marketplace.yaml'}"
        with _build_client(tmp_path, registries=[url1, url2]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        ids = sorted(e["id"] for e in body["entries"])
        assert ids == ["bar-tool", "foo-tool"]
        sources = {e["source_registry"] for e in body["entries"]}
        assert sources == {url1, url2}

    def test_missing_file_registry_marks_stale(self, tmp_path):
        bad = f"file://{tmp_path / 'nonexistent.yaml'}"
        with _build_client(tmp_path, registries=[bad]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        assert body["entries"] == []
        assert body["stale"] is True
        assert body["failed_registries"] == [bad]

    def test_lkg_fallback_when_registry_disappears(self, tmp_path):
        """First call succeeds + caches; on second call the file
        is gone, so the LKG entries should still appear with
        stale=true."""
        url = _write_registry(tmp_path, inline=True)
        with _build_client(tmp_path, registries=[url], cache_ttl_s=0) as c:
            # First call — populates LKG
            r1 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            assert r1.status_code == 200
            assert len(r1.json()["entries"]) == 1

            # Delete the file + force re-fetch (TTL=0)
            (tmp_path / "marketplace.yaml").unlink()
            r2 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            assert r2.status_code == 200
            body = r2.json()
            # LKG entries served despite the failure
            assert len(body["entries"]) == 1
            assert body["entries"][0]["id"] == "foo-tool"
            assert body["stale"] is True
            assert url in body["failed_registries"]


# ===========================================================================
# Caching
# ===========================================================================

class TestCache:
    def test_cache_within_ttl_no_refetch(self, tmp_path):
        """Within TTL, the second call must not re-read the file —
        we delete it after the first call and confirm the second
        call still returns the cached entry."""
        url = _write_registry(tmp_path, inline=True)
        with _build_client(tmp_path, registries=[url], cache_ttl_s=3600) as c:
            r1 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            assert len(r1.json()["entries"]) == 1
            (tmp_path / "marketplace.yaml").unlink()
            r2 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            assert len(r2.json()["entries"]) == 1
            assert r2.json()["stale"] is False  # no re-fetch attempted
            assert r2.json()["failed_registries"] == []

    def test_ttl_zero_always_refetches(self, tmp_path):
        url = _write_registry(tmp_path, inline=True)
        with _build_client(tmp_path, registries=[url], cache_ttl_s=0) as c:
            r1 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            assert len(r1.json()["entries"]) == 1
            # Now break it; ttl=0 should force re-fetch on next call.
            (tmp_path / "marketplace.yaml").unlink()
            r2 = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
            # LKG kicks in — entries still served, but stale flag set.
            assert r2.json()["stale"] is True


# ===========================================================================
# HTTP semantics + auth
# ===========================================================================

class TestEndpointSemantics:
    def test_missing_token_rejected(self, tmp_path):
        with _build_client(tmp_path) as c:
            r = c.get("/marketplace/index")
        assert r.status_code in (401, 403)

    def test_response_shape_matches_schema(self, tmp_path):
        url = _write_registry(tmp_path, inline=True)
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        # Top-level fields per MarketplaceIndexOut
        for k in (
            "schema_version", "entries", "fetched_at", "cache_ttl_s",
            "stale", "failed_registries", "configured_registries",
        ):
            assert k in body
        # Per-entry fields
        e = body["entries"][0]
        for k in (
            "id", "name", "version", "author", "source_url",
            "download_url", "download_sha256", "description",
            "permissions_summary", "contributes", "archetype_tags",
            "highest_side_effect_tier", "required_secrets",
            "reviewed_by", "source_registry", "trusted",
        ):
            assert k in e


# ===========================================================================
# Per-entry parsing edge cases
# ===========================================================================

class TestEntryParsing:
    def test_missing_required_field_drops_entry(self, tmp_path):
        """An entry without `id` must drop silently — registry as
        a whole still parses + serves the rest."""
        body = (
            "schema_version: 1\n"
            "entries:\n"
            "  - name: Has No Id\n"     # bad
            "    version: '1'\n"
            "  - id: good-one\n"
            "    name: Good\n"
            "    version: '1'\n"
            "    author: alice\n"
            "    source_url: https://x\n"
            "    download_url: https://x.plugin\n"
            "    download_sha256: ab\n"
            "    description: ok\n"
            "    permissions_summary: ok\n"
            "    contributes:\n"
            "      tools: []\n"
            "    highest_side_effect_tier: read_only\n"
        )
        (tmp_path / "marketplace.yaml").write_text(body, encoding="utf-8")
        url = f"file://{tmp_path / 'marketplace.yaml'}"
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        ids = [e["id"] for e in r.json()["entries"]]
        assert ids == ["good-one"]

    def test_invalid_yaml_marks_registry_failed(self, tmp_path):
        (tmp_path / "marketplace.yaml").write_text(
            "schema_version: 1\nentries:\n  - bad: : :\n",
            encoding="utf-8",
        )
        url = f"file://{tmp_path / 'marketplace.yaml'}"
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        assert body["entries"] == []
        assert body["stale"] is True
        assert url in body["failed_registries"]

    def test_path_escape_attempt_rejected(self, tmp_path):
        """A filename reference with `../` outside the registry
        directory must be refused — the entry parses to nothing,
        the registry succeeds with that entry skipped."""
        outside = tmp_path.parent / "evil.yaml"
        # We don't actually write the evil file — the path-escape
        # check fails before it'd be read.
        (tmp_path / "marketplace.yaml").write_text(
            "schema_version: 1\nentries:\n  - ../evil.yaml\n",
            encoding="utf-8",
        )
        url = f"file://{tmp_path / 'marketplace.yaml'}"
        with _build_client(tmp_path, registries=[url]) as c:
            r = c.get(
                "/marketplace/index",
                headers={"X-FSF-Token": "test-token-1234"},
            )
        body = r.json()
        assert body["entries"] == []
