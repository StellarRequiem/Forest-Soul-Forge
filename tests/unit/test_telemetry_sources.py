"""ADR-0064 T2 (B349) — sources.yaml allowlist loader tests.

Coverage:
  load_sources:
    - happy path returns SourcesConfig + empty errors
    - missing file raises AdapterError
    - non-mapping top-level raises
    - wrong schema_version raises
    - sources not a list raises
    - per-entry: missing name → soft error, skipped
    - per-entry: malformed adapter_class → soft error
    - per-entry: duplicate name → soft error, first kept
    - per-entry: non-dict config → soft error, defaulted to {}

  resolve_adapter_class:
    - happy path returns the class
    - missing separator raises
    - unknown module raises
    - missing attribute raises
    - attribute not a class raises
    - attribute is class but not Adapter subclass raises

  instantiate_adapters:
    - happy path: enabled-only by default, returns instances
    - SOURCE mismatch is soft error + entry skipped
    - constructor TypeError is soft error + entry skipped
    - only_enabled=False returns disabled entries too
    - SourcesConfig.by_name() finds entries

  SourcesConfig:
    - enabled() filters to enabled-only
    - by_name() returns matching or None
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.security.telemetry.adapter import Adapter, AdapterError
from forest_soul_forge.security.telemetry.events import TelemetryEvent
from forest_soul_forge.security.telemetry.sources import (
    SCHEMA_VERSION,
    SourceSpec,
    SourcesConfig,
    instantiate_adapters,
    load_sources,
    resolve_adapter_class,
)


# ---------------------------------------------------------------------------
# Fixtures: test adapters defined at module scope so they're importable
# via "module:ClassName" paths in YAML.
# ---------------------------------------------------------------------------


class FixtureAdapterAlpha(Adapter):
    """Test adapter A. SOURCE matches the YAML name 'alpha'."""
    SOURCE = "alpha"

    def __init__(self, *, threshold: int = 5) -> None:
        self.threshold = threshold

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        return None


class FixtureAdapterMismatch(Adapter):
    """Test adapter whose SOURCE intentionally disagrees with the
    name we'll use in YAML — to exercise the mismatch path."""
    SOURCE = "actual_name"

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        return None


class FixtureAdapterRequiredArg(Adapter):
    """Adapter with a required init arg, used to test the
    constructor-mismatch error path."""
    SOURCE = "needs_arg"

    def __init__(self, *, required_arg: str) -> None:
        self.required_arg = required_arg

    def command(self) -> list[str]:
        return ["true"]

    def parse(self, line: str) -> TelemetryEvent | None:
        return None


# Sentinel used for the "attribute is not a class" test.
NOT_A_CLASS = 42


# ---------------------------------------------------------------------------
# load_sources
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_happy_path(tmp_path):
    p = _write_yaml(tmp_path, """
schema_version: 1
sources:
  - name: alpha
    adapter_class: test_telemetry_sources:FixtureAdapterAlpha
    enabled: true
    config: {threshold: 9}
""")
    cfg, errors = load_sources(p)
    assert errors == []
    assert cfg.schema_version == SCHEMA_VERSION
    assert len(cfg.sources) == 1
    s = cfg.sources[0]
    assert s.name == "alpha"
    assert s.enabled is True
    assert s.config == {"threshold": 9}


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(AdapterError, match="does not exist"):
        load_sources(tmp_path / "missing.yaml")


def test_load_non_mapping_top_level_raises(tmp_path):
    p = _write_yaml(tmp_path, "- just\n- a list\n")
    with pytest.raises(AdapterError, match="YAML mapping at top level"):
        load_sources(p)


def test_load_wrong_schema_version_raises(tmp_path):
    p = _write_yaml(tmp_path, "schema_version: 99\nsources: []\n")
    with pytest.raises(AdapterError, match="schema_version"):
        load_sources(p)


def test_load_sources_not_a_list_raises(tmp_path):
    p = _write_yaml(tmp_path, "schema_version: 1\nsources: not_a_list\n")
    with pytest.raises(AdapterError, match="must be a list"):
        load_sources(p)


def test_load_missing_name_is_soft_error(tmp_path):
    p = _write_yaml(tmp_path, """
schema_version: 1
sources:
  - adapter_class: foo:Bar
    enabled: true
""")
    cfg, errors = load_sources(p)
    assert len(errors) == 1
    assert "name missing" in errors[0]
    assert cfg.sources == ()


def test_load_malformed_adapter_class_is_soft_error(tmp_path):
    p = _write_yaml(tmp_path, """
schema_version: 1
sources:
  - name: alpha
    adapter_class: not_a_valid_path
    enabled: true
""")
    cfg, errors = load_sources(p)
    assert len(errors) == 1
    assert "module:ClassName" in errors[0]


def test_load_duplicate_name_is_soft_error(tmp_path):
    p = _write_yaml(tmp_path, """
schema_version: 1
sources:
  - name: alpha
    adapter_class: test_telemetry_sources:FixtureAdapterAlpha
    enabled: true
  - name: alpha
    adapter_class: test_telemetry_sources:FixtureAdapterAlpha
    enabled: false
""")
    cfg, errors = load_sources(p)
    assert len(errors) == 1
    assert "duplicates" in errors[0]
    # First entry kept.
    assert len(cfg.sources) == 1
    assert cfg.sources[0].enabled is True


def test_load_non_dict_config_is_soft_error(tmp_path):
    p = _write_yaml(tmp_path, """
schema_version: 1
sources:
  - name: alpha
    adapter_class: test_telemetry_sources:FixtureAdapterAlpha
    enabled: true
    config: not_a_dict
""")
    cfg, errors = load_sources(p)
    assert len(errors) == 1
    assert "config must be a mapping" in errors[0]
    # Entry retained with config defaulted to {}.
    assert cfg.sources[0].config == {}


# ---------------------------------------------------------------------------
# resolve_adapter_class
# ---------------------------------------------------------------------------


def test_resolve_happy_path():
    cls = resolve_adapter_class(
        "test_telemetry_sources:FixtureAdapterAlpha"
    )
    assert cls is FixtureAdapterAlpha


def test_resolve_missing_separator_raises():
    with pytest.raises(AdapterError, match="module:ClassName"):
        resolve_adapter_class("no_separator")


def test_resolve_unknown_module_raises():
    with pytest.raises(AdapterError, match="not importable"):
        resolve_adapter_class("forest_soul_forge.nope_nope_nope:X")


def test_resolve_missing_attribute_raises():
    with pytest.raises(AdapterError, match="not found in module"):
        resolve_adapter_class(
            "test_telemetry_sources:NoSuchClass"
        )


def test_resolve_attribute_not_a_class_raises():
    with pytest.raises(AdapterError, match="not a subclass of Adapter"):
        resolve_adapter_class(
            "test_telemetry_sources:NOT_A_CLASS"
        )


def test_resolve_class_not_adapter_subclass_raises():
    """A class that isn't an Adapter subclass — e.g., dict — must be
    rejected. The error message names the path so the operator can
    fix the YAML."""
    with pytest.raises(AdapterError, match="not a subclass of Adapter"):
        resolve_adapter_class("builtins:dict")


# ---------------------------------------------------------------------------
# instantiate_adapters
# ---------------------------------------------------------------------------


def _spec(
    name: str,
    cls_path: str,
    *,
    enabled: bool = True,
    config: dict | None = None,
) -> SourceSpec:
    return SourceSpec(
        name=name,
        adapter_class_path=cls_path,
        enabled=enabled,
        config=config or {},
    )


def test_instantiate_happy_path():
    cfg = SourcesConfig(
        schema_version=1,
        sources=(_spec(
            "alpha",
            "test_telemetry_sources:FixtureAdapterAlpha",
            config={"threshold": 7},
        ),),
    )
    adapters, errors = instantiate_adapters(cfg)
    assert errors == []
    assert len(adapters) == 1
    assert isinstance(adapters[0], FixtureAdapterAlpha)
    assert adapters[0].threshold == 7


def test_instantiate_source_mismatch_soft_error():
    """allowlist name 'pretend_name' but the resolved class's SOURCE
    is 'actual_name'. The loader must refuse to instantiate."""
    cfg = SourcesConfig(
        schema_version=1,
        sources=(_spec(
            "pretend_name",
            "test_telemetry_sources:FixtureAdapterMismatch",
        ),),
    )
    adapters, errors = instantiate_adapters(cfg)
    assert adapters == []
    assert len(errors) == 1
    assert "SOURCE attr" in errors[0]
    assert "disagrees" in errors[0]


def test_instantiate_constructor_mismatch_soft_error():
    """FixtureAdapterRequiredArg needs required_arg but we omit it
    from config. TypeError gets caught + surfaced as a soft error."""
    cfg = SourcesConfig(
        schema_version=1,
        sources=(_spec(
            "needs_arg",
            "test_telemetry_sources:FixtureAdapterRequiredArg",
            # No config — constructor will TypeError on missing kwarg.
        ),),
    )
    adapters, errors = instantiate_adapters(cfg)
    assert adapters == []
    assert len(errors) == 1
    assert "constructor call failed" in errors[0]


def test_instantiate_only_enabled_default_filters_disabled():
    cfg = SourcesConfig(
        schema_version=1,
        sources=(
            _spec(
                "alpha",
                "test_telemetry_sources:FixtureAdapterAlpha",
                enabled=True,
            ),
            _spec(
                "alpha2",
                "test_telemetry_sources:FixtureAdapterAlpha",
                enabled=False,
            ),
        ),
    )
    adapters, errors = instantiate_adapters(cfg)
    # alpha2 disabled → not instantiated. But alpha2's SOURCE is
    # "alpha" which would mismatch its allowlist name, so we use
    # only_enabled to confirm filtering happens BEFORE that check.
    assert errors == []
    assert len(adapters) == 1


def test_instantiate_only_enabled_false_includes_disabled():
    """Operator-debug path: instantiate everything to verify the
    full allowlist loads cleanly. Mismatches still get surfaced."""
    cfg = SourcesConfig(
        schema_version=1,
        sources=(
            _spec(
                "alpha",
                "test_telemetry_sources:FixtureAdapterAlpha",
                enabled=False,
            ),
        ),
    )
    adapters, errors = instantiate_adapters(cfg, only_enabled=False)
    assert errors == []
    assert len(adapters) == 1


# ---------------------------------------------------------------------------
# SourcesConfig helpers
# ---------------------------------------------------------------------------


def test_sources_config_enabled_filters():
    cfg = SourcesConfig(
        schema_version=1,
        sources=(
            _spec("a", "x:A", enabled=True),
            _spec("b", "x:B", enabled=False),
            _spec("c", "x:C", enabled=True),
        ),
    )
    en = cfg.enabled()
    assert {s.name for s in en} == {"a", "c"}


def test_sources_config_by_name():
    cfg = SourcesConfig(
        schema_version=1,
        sources=(_spec("a", "x:A"), _spec("b", "x:B")),
    )
    assert cfg.by_name("b").name == "b"
    assert cfg.by_name("nope") is None


def test_real_telemetry_sources_yaml_loads_cleanly():
    """Smoke test against the real config/telemetry_sources.yaml that
    ships in B349. If a typo lands the loader catches it here."""
    real_path = (
        Path(__file__).resolve().parents[2]
        / "config" / "telemetry_sources.yaml"
    )
    cfg, errors = load_sources(real_path)
    assert errors == [], f"real config has soft errors: {errors}"
    assert cfg.schema_version == 1
    # The shipped allowlist includes the macos_unified_log entry.
    names = {s.name for s in cfg.sources}
    assert "macos_unified_log" in names
