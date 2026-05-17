"""Reference adapter implementations for ADR-0064.

Adapters live one-per-file under this package. The allowlist in
config/telemetry_sources.yaml references them by import path.

Available in T2:
  - macos_unified_log.MacosUnifiedLogAdapter

Planned (later tranches or operator-added):
  - lsof_adapter
  - fsevents_adapter
  - pf_adapter
  - syslog_adapter
"""
