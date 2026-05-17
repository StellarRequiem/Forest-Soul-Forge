"""ADR-0064 telemetry pipeline substrate.

Telemetry events describe what HAPPENED in the world the operator's
machine observes; audit chain events describe what the AGENT KERNEL
DID. Different provenance, different signing keys, different
retention, different consumer.

Public surface:
  - TelemetryEvent       : canonical event shape
  - TelemetryStore       : storage interface
  - SqliteTelemetryStore : reference SQLite-backed impl
  - RetentionPolicy      : retention class + TTL contract
  - classify_retention   : default rule-table classifier
  - Adapter              : base class for ingestion adapters (T2)
  - AdapterIngestor      : subprocess-driver for one adapter (T2)
  - SourcesConfig        : allowlist loader for telemetry_sources.yaml (T2)
"""
from __future__ import annotations

from .adapter import Adapter, AdapterError
from .events import (
    EVENT_TYPES,
    RETENTION_CLASSES,
    SEVERITIES,
    TelemetryEvent,
    TelemetryEventError,
    canonical_form,
    compute_integrity_hash,
)
from .ingestor import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_FLUSH_INTERVAL_S,
    AdapterIngestor,
    IngestorError,
    IngestorStats,
)
from .retention import (
    DEFAULT_RETENTION_TTLS,
    RetentionPolicy,
    classify_retention,
)
from .sources import (
    SCHEMA_VERSION,
    SourceSpec,
    SourcesConfig,
    instantiate_adapters,
    load_sources,
    resolve_adapter_class,
)
from .store import (
    SQLITE_SCHEMA_V1,
    SqliteTelemetryStore,
    TelemetryStore,
    TelemetryStoreError,
)

__all__ = [
    # events
    "EVENT_TYPES", "RETENTION_CLASSES", "SEVERITIES",
    "TelemetryEvent", "TelemetryEventError",
    "canonical_form", "compute_integrity_hash",
    # store
    "SQLITE_SCHEMA_V1", "SqliteTelemetryStore",
    "TelemetryStore", "TelemetryStoreError",
    # retention
    "DEFAULT_RETENTION_TTLS", "RetentionPolicy", "classify_retention",
    # adapter (T2)
    "Adapter", "AdapterError",
    # ingestor (T2)
    "DEFAULT_BATCH_SIZE", "DEFAULT_FLUSH_INTERVAL_S",
    "AdapterIngestor", "IngestorError", "IngestorStats",
    # sources (T2)
    "SCHEMA_VERSION", "SourceSpec", "SourcesConfig",
    "instantiate_adapters", "load_sources", "resolve_adapter_class",
]
