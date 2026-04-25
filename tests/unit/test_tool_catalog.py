"""Unit tests for the ADR-0018 tool catalog loader.

Verifies: structural validation rejects malformed catalogs, integrity
checks fail closed on duplicate keys / missing tool refs / bad enum
values, ToolRef.from_key parses both 'name.v1' and 'name.1' shapes,
and resolve_kit composes the standard kit + tools_add/tools_remove
exactly as ADR-0018 §"resolve_kit" specifies.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Skip the module if pyyaml isn't available (loader uses yaml.safe_load).
yaml = pytest.importorskip("yaml")

from forest_soul_forge.core.tool_catalog import (  # noqa: E402
    ArchetypeBundle,
    SIDE_EFFECT_VALUES,
    ToolCatalog,
    ToolCatalogError,
    ToolDef,
    ToolRef,
    empty_catalog,
    load_catalog,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "config" / "tool_catalog.yaml"


# ---------------------------------------------------------------------------
# ToolRef parsing
# ---------------------------------------------------------------------------
class TestToolRef:
    def test_from_key_with_v_prefix(self):
        ref = ToolRef.from_key("packet_query.v1")
        assert ref.name == "packet_query"
        assert ref.version == "1"
        assert ref.key == "packet_query.v1"

    def test_from_key_without_v_prefix(self):
        ref = ToolRef.from_key("packet_query.1")
        assert ref.name == "packet_query"
        assert ref.version == "1"

    def test_from_key_handles_multipart_names(self):
        # Tool names can contain dots eventually if we want — rpartition
        # ensures the LAST dot is the version separator.
        ref = ToolRef.from_key("namespace.tool_name.v3")
        assert ref.name == "namespace.tool_name"
        assert ref.version == "3"

    def test_from_key_rejects_missing_separator(self):
        with pytest.raises(ToolCatalogError):
            ToolRef.from_key("packet_query")

    def test_from_key_rejects_empty_parts(self):
        with pytest.raises(ToolCatalogError):
            ToolRef.from_key(".v1")
        with pytest.raises(ToolCatalogError):
            ToolRef.from_key("name.")

    def test_to_dict_round_trips(self):
        ref = ToolRef(name="x", version="2")
        assert ref.to_dict() == {"name": "x", "version": "2"}
        assert ToolRef.from_dict(ref.to_dict()) == ref


# ---------------------------------------------------------------------------
# Loading the real shipped catalog
# ---------------------------------------------------------------------------
class TestRealCatalog:
    """Sanity-check that the catalog committed at config/tool_catalog.yaml
    actually loads and has the entries we expect."""

    def test_real_catalog_loads(self):
        if not CATALOG_PATH.exists():
            pytest.skip(f"catalog missing at {CATALOG_PATH}")
        cat = load_catalog(CATALOG_PATH)
        assert cat.version  # non-empty
        assert "packet_query.v1" in cat.tools
        assert "log_grep.v1" in cat.tools
        assert "baseline_compare.v1" in cat.tools
        assert "network_watcher" in cat.archetypes
        assert "log_analyst" in cat.archetypes
        assert "anomaly_investigator" in cat.archetypes

    def test_real_catalog_archetype_refs_resolve(self):
        """Every ToolRef in every archetype's standard_tools must resolve
        to a real catalog entry. The loader enforces this; this test
        protects against regressions when the YAML is edited."""
        if not CATALOG_PATH.exists():
            pytest.skip(f"catalog missing at {CATALOG_PATH}")
        cat = load_catalog(CATALOG_PATH)
        for role, bundle in cat.archetypes.items():
            for ref in bundle.standard_tools:
                assert ref.key in cat.tools, (
                    f"archetype {role} references missing tool {ref.key}"
                )

    def test_real_catalog_side_effects_are_known(self):
        if not CATALOG_PATH.exists():
            pytest.skip(f"catalog missing at {CATALOG_PATH}")
        cat = load_catalog(CATALOG_PATH)
        for td in cat.tools.values():
            assert td.side_effects in SIDE_EFFECT_VALUES, (
                f"unknown side_effects {td.side_effects!r} on {td.key}"
            )


# ---------------------------------------------------------------------------
# Synthetic catalog YAML — error path tests
# ---------------------------------------------------------------------------
def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(body, encoding="utf-8")
    return p


class TestLoadErrors:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ToolCatalogError):
            load_catalog(tmp_path / "no_such_catalog.yaml")

    def test_root_must_be_mapping(self, tmp_path: Path):
        p = _write_yaml(tmp_path, "- not_a_mapping\n")
        with pytest.raises(ToolCatalogError):
            load_catalog(p)

    def test_missing_version_raises(self, tmp_path: Path):
        p = _write_yaml(tmp_path, "tools: {}\narchetypes: {}\n")
        with pytest.raises(ToolCatalogError):
            load_catalog(p)

    def test_tool_entry_missing_required_fields(self, tmp_path: Path):
        p = _write_yaml(tmp_path, """
version: "0.1"
tools:
  bad_tool.v1:
    name: bad_tool
    version: "1"
    # missing description, input_schema, side_effects
archetypes: {}
""")
        with pytest.raises(ToolCatalogError):
            load_catalog(p)

    def test_unknown_side_effects_value_rejected(self, tmp_path: Path):
        p = _write_yaml(tmp_path, """
version: "0.1"
tools:
  weird.v1:
    name: weird
    version: "1"
    description: "x"
    input_schema: { type: object }
    side_effects: makes_pancakes
    archetype_tags: []
archetypes: {}
""")
        with pytest.raises(ToolCatalogError) as ei:
            load_catalog(p)
        assert "side_effects" in str(ei.value)

    def test_key_disagrees_with_name_version(self, tmp_path: Path):
        # Composite key must match {name}.{version}.
        p = _write_yaml(tmp_path, """
version: "0.1"
tools:
  packet_query.v1:
    name: packet_query
    version: "2"  # disagrees with key's v1
    description: x
    input_schema: { type: object }
    side_effects: read_only
    archetype_tags: []
archetypes: {}
""")
        with pytest.raises(ToolCatalogError) as ei:
            load_catalog(p)
        assert "disagrees" in str(ei.value)

    def test_archetype_references_unknown_tool(self, tmp_path: Path):
        p = _write_yaml(tmp_path, """
version: "0.1"
tools:
  packet_query.v1:
    name: packet_query
    version: "1"
    description: x
    input_schema: { type: object }
    side_effects: read_only
    archetype_tags: [network_watcher]
archetypes:
  network_watcher:
    standard_tools:
      - packet_query.v1
      - flow_summary.v99   # not in tools
""")
        with pytest.raises(ToolCatalogError) as ei:
            load_catalog(p)
        assert "flow_summary.v99" in str(ei.value)


# ---------------------------------------------------------------------------
# resolve_kit semantics
# ---------------------------------------------------------------------------
def _toy_catalog() -> ToolCatalog:
    """Minimal catalog used by the resolve_kit tests — independent of
    the real shipped catalog so changes there can't shift these
    semantics tests."""
    tools = {
        "alpha.v1": ToolDef(
            name="alpha", version="1", description="a",
            input_schema={"type": "object"},
            side_effects="read_only", archetype_tags=("watcher",),
        ),
        "alpha.v2": ToolDef(
            name="alpha", version="2", description="a2",
            input_schema={"type": "object"},
            side_effects="read_only", archetype_tags=("watcher",),
        ),
        "beta.v1": ToolDef(
            name="beta", version="1", description="b",
            input_schema={"type": "object"},
            side_effects="read_only", archetype_tags=("watcher",),
        ),
        "gamma.v1": ToolDef(
            name="gamma", version="1", description="g",
            input_schema={"type": "object"},
            side_effects="network", archetype_tags=("watcher",),
        ),
    }
    archetypes = {
        "watcher": ArchetypeBundle(
            role="watcher",
            standard_tools=(
                ToolRef("alpha", "1"),
                ToolRef("beta", "1"),
            ),
        ),
        "no_default_kit": ArchetypeBundle(
            role="no_default_kit",
            standard_tools=(),
        ),
    }
    return ToolCatalog(
        version="0", tools=tools, archetypes=archetypes, source_path=None
    )


class TestResolveKit:
    def test_default_kit_no_overrides(self):
        cat = _toy_catalog()
        kit = cat.resolve_kit("watcher")
        names = [r.name for r in kit]
        assert names == ["alpha", "beta"]
        assert all(r.version == "1" for r in kit)

    def test_unknown_role_returns_empty_kit(self):
        cat = _toy_catalog()
        # No archetype for 'ghost' — empty kit, no exception.
        assert cat.resolve_kit("ghost") == ()

    def test_role_with_no_default_kit(self):
        cat = _toy_catalog()
        assert cat.resolve_kit("no_default_kit") == ()

    def test_tools_add_appends_new_tool(self):
        cat = _toy_catalog()
        kit = cat.resolve_kit(
            "watcher",
            tools_add=[ToolRef("gamma", "1")],
        )
        names = [r.name for r in kit]
        assert names == ["alpha", "beta", "gamma"]

    def test_tools_remove_drops_by_name(self):
        cat = _toy_catalog()
        kit = cat.resolve_kit("watcher", tools_remove=["alpha"])
        names = [r.name for r in kit]
        assert names == ["beta"]

    def test_tools_add_overrides_standard_version(self):
        """When tools_add provides a different version of a name in the
        standard kit, the override wins, kit ordering is preserved."""
        cat = _toy_catalog()
        kit = cat.resolve_kit(
            "watcher",
            tools_add=[ToolRef("alpha", "2")],
        )
        names_versions = [(r.name, r.version) for r in kit]
        # alpha is upgraded to v2 in the same slot, beta unchanged.
        assert names_versions == [("alpha", "2"), ("beta", "1")]

    def test_tools_add_unknown_tool_raises(self):
        cat = _toy_catalog()
        with pytest.raises(ToolCatalogError) as ei:
            cat.resolve_kit(
                "watcher",
                tools_add=[ToolRef("nonexistent", "1")],
            )
        assert "nonexistent.v1" in str(ei.value)

    def test_tools_remove_unknown_name_raises(self):
        cat = _toy_catalog()
        with pytest.raises(ToolCatalogError) as ei:
            cat.resolve_kit("watcher", tools_remove=["does_not_exist"])
        assert "does_not_exist" in str(ei.value)


class TestEmptyCatalog:
    def test_empty_catalog_resolves_to_empty_kit(self):
        empty = empty_catalog()
        assert empty.resolve_kit("anything") == ()

    def test_empty_catalog_rejects_unknown_add(self):
        empty = empty_catalog()
        with pytest.raises(ToolCatalogError):
            empty.resolve_kit(
                "anything",
                tools_add=[ToolRef("x", "1")],
            )
