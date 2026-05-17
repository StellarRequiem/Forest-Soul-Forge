"""ADR-0064 T2 — Allowlist loader for telemetry sources.

``config/telemetry_sources.yaml`` is the operator's load-bearing
list of which telemetry sources are permitted to ingest. Engineer-
edited via PR; code-reviewed before merge. The AdapterIngestor
refuses to start any adapter whose source isn't in this list, even
if the operator manually instantiated the class.

YAML shape (v1):

    schema_version: 1
    sources:
      - name: macos_unified_log
        adapter_class: forest_soul_forge.security.telemetry.adapters.macos_unified_log:MacosUnifiedLogAdapter
        enabled: true
        config:
          predicate: 'subsystem == "com.apple.securityd"'

The ``adapter_class`` field is a "module:ClassName" import path.
The loader resolves it at allowlist load time + verifies the
class's SOURCE attribute matches the YAML ``name``. Mismatch is
a configuration error, not an authentication one.

What's NOT in this loader:
  - Live reload (cron-driven swaps). Adapters are static today;
    operator restarts daemon to pick up YAML changes.
  - Per-adapter secret resolution (e.g., remote-feed credentials).
    Adapters that need secrets read them from their config block
    via the Keychain backend, same as everything else.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .adapter import Adapter, AdapterError


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SourceSpec:
    """One entry from telemetry_sources.yaml after parsing."""

    name: str
    adapter_class_path: str
    enabled: bool
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcesConfig:
    """The whole allowlist, post-load + post-validation."""

    schema_version: int
    sources: tuple[SourceSpec, ...]

    def enabled(self) -> tuple[SourceSpec, ...]:
        return tuple(s for s in self.sources if s.enabled)

    def by_name(self, name: str) -> SourceSpec | None:
        for s in self.sources:
            if s.name == name:
                return s
        return None


def load_sources(path: Path | str) -> tuple[SourcesConfig, list[str]]:
    """Load + validate the allowlist.

    Returns ``(config, errors)``. Errors is a list of warning strings
    for soft validation issues (unknown adapter_class, mismatched
    SOURCE attribute) so the operator gets a full punch list rather
    than crashing on the first problem.

    Hard errors (bad schema_version, malformed YAML, sources not a
    list) raise AdapterError — the file is fundamentally unusable.
    """
    p = Path(path)
    if not p.exists():
        raise AdapterError(f"sources file does not exist: {p}")

    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AdapterError(
            f"sources file must be a YAML mapping at top level; got "
            f"{type(raw).__name__}"
        )

    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise AdapterError(
            f"unsupported schema_version {sv!r}; expected {SCHEMA_VERSION}"
        )

    sources_raw = raw.get("sources") or []
    if not isinstance(sources_raw, list):
        raise AdapterError(
            f"sources field must be a list; got {type(sources_raw).__name__}"
        )

    errors: list[str] = []
    specs: list[SourceSpec] = []
    seen_names: set[str] = set()

    for i, entry in enumerate(sources_raw):
        if not isinstance(entry, dict):
            errors.append(f"sources[{i}] not a mapping; skipped")
            continue
        name = entry.get("name")
        cls_path = entry.get("adapter_class")
        enabled = bool(entry.get("enabled", False))
        config = entry.get("config") or {}

        if not isinstance(name, str) or not name.strip():
            errors.append(f"sources[{i}].name missing or empty; skipped")
            continue
        if name in seen_names:
            errors.append(f"sources[{i}].name {name!r} duplicates earlier entry")
            continue
        seen_names.add(name)
        if not isinstance(cls_path, str) or ":" not in cls_path:
            errors.append(
                f"sources[{name}].adapter_class must be 'module:ClassName'; "
                f"got {cls_path!r}"
            )
            continue
        if not isinstance(config, dict):
            errors.append(
                f"sources[{name}].config must be a mapping; got "
                f"{type(config).__name__}"
            )
            config = {}

        specs.append(SourceSpec(
            name=name,
            adapter_class_path=cls_path,
            enabled=enabled,
            config=dict(config),  # defensive copy so the frozen instance stays so
        ))

    return SourcesConfig(schema_version=sv, sources=tuple(specs)), errors


def resolve_adapter_class(adapter_class_path: str) -> type[Adapter]:
    """Import + return the Adapter subclass named by 'module:ClassName'.

    Validates:
      - module is importable
      - attribute exists on the module
      - attribute is a class
      - class is a subclass of Adapter

    Each failure raises AdapterError with a precise message so the
    operator can fix the YAML rather than chase tracebacks.
    """
    if ":" not in adapter_class_path:
        raise AdapterError(
            f"adapter_class_path missing 'module:ClassName' separator: "
            f"{adapter_class_path!r}"
        )
    module_name, _, class_name = adapter_class_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise AdapterError(
            f"adapter_class module {module_name!r} not importable: {e}"
        ) from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AdapterError(
            f"adapter_class {class_name!r} not found in module {module_name!r}"
        )
    if not isinstance(cls, type) or not issubclass(cls, Adapter):
        raise AdapterError(
            f"adapter_class {adapter_class_path!r} is not a subclass of Adapter"
        )
    return cls


def instantiate_adapters(
    config: SourcesConfig,
    *,
    only_enabled: bool = True,
) -> tuple[list[Adapter], list[str]]:
    """Walk the allowlist and instantiate each adapter class.

    Returns ``(adapters, errors)``. Errors collects any per-spec
    instantiation failures (import error, constructor mismatch,
    SOURCE attr disagrees with allowlist name) so the operator gets
    the full list. Failed entries are silently omitted from the
    returned adapters list — the manager can run the survivors.
    """
    adapters: list[Adapter] = []
    errors: list[str] = []
    specs = config.enabled() if only_enabled else config.sources
    for spec in specs:
        try:
            cls = resolve_adapter_class(spec.adapter_class_path)
        except AdapterError as e:
            errors.append(f"{spec.name}: {e}")
            continue
        if cls.SOURCE != spec.name:
            errors.append(
                f"{spec.name}: adapter class SOURCE attr {cls.SOURCE!r} "
                f"disagrees with allowlist name; refusing to instantiate"
            )
            continue
        try:
            adapter = cls(**spec.config)
        except TypeError as e:
            errors.append(
                f"{spec.name}: constructor call failed (config keys may "
                f"not match): {e}"
            )
            continue
        adapters.append(adapter)
    return adapters, errors
