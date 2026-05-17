"""ADR-0064 T1 — Retention policy + classification.

Three retention classes from ADR-0064 Decision 4:
  ephemeral          7 days   (process_spawn noise, info-level log lines)
  standard          90 days   (default for unclassified)
  security_relevant 365 days  (auth, policy_decision, severity=critical)

The classify_retention() function applies the rule table. Adapters
can override per-event by passing an explicit retention_class to
the store, but the default path uses this classifier so the
retention shape is consistent across adapters.

Retention sweeps run via TelemetryStore.retention_sweep(now). The
sweep itself lives in store.py; this module owns the policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


# ADR-0064 Decision 4 TTL table. Values in DAYS.
# Centralized so operators can audit / adjust in one place
# (a future operator-config tranche may make this YAML-driven;
# for T1 it's hardcoded so the contract is unambiguous).
DEFAULT_RETENTION_TTLS: dict[str, int] = {
    "ephemeral": 7,
    "standard": 90,
    "security_relevant": 365,
}


@dataclass(frozen=True)
class RetentionPolicy:
    """Bundle of retention TTLs. Defaults to ADR-0064's table.

    Override in tests or for operator customization by passing a
    different ttls dict at construction.
    """

    ttls: dict[str, int]  # class_name → days

    @classmethod
    def default(cls) -> "RetentionPolicy":
        return cls(ttls=dict(DEFAULT_RETENTION_TTLS))

    def cutoff_for(self, retention_class: str, *, now: datetime) -> datetime:
        """Compute the timestamp ABOVE which events of the given
        class are still live. Anything with timestamp BELOW the
        returned datetime is eligible for sweep.

        Raises KeyError on an unknown class — better to crash loud
        than to silently delete everything because we typoed a name.
        """
        ttl_days = self.ttls[retention_class]
        return now - timedelta(days=ttl_days)


def classify_retention(
    *,
    event_type: str,
    severity: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Default classifier per ADR-0064 Decision 4.

    Rule order (first match wins):
      1. severity == "critical"  → security_relevant
      2. event_type in {auth_event, policy_decision} → security_relevant
      3. event_type == "process_spawn" AND severity == "info"
         → ephemeral  (high-volume noise on a busy host)
      4. event_type == "log_line" AND severity == "info"
         → ephemeral  (likewise)
      5. otherwise → standard

    `payload` is accepted but not used today; future per-event
    classifier extensions (e.g., distinguish login-shell vs auth-keychain)
    can read from it without changing the signature.
    """
    # Rule 1 — anything critical gets the long retention.
    if severity == "critical":
        return "security_relevant"

    # Rule 2 — auth and policy events are forensically valuable
    # regardless of severity (a "warn" auth attempt may be the
    # first sign of credential brute-force).
    if event_type in ("auth_event", "policy_decision"):
        return "security_relevant"

    # Rules 3-4 — high-volume types at info level become ephemeral.
    # On a busy macOS host these can hit 1K+/min during boot.
    if event_type == "process_spawn" and severity == "info":
        return "ephemeral"
    if event_type == "log_line" and severity == "info":
        return "ephemeral"

    # Rule 5 — default.
    return "standard"


def utc_now() -> datetime:
    """Convenience wrapper. The store calls retention_sweep(now=...)
    so tests can inject a controlled clock; production code passes
    utc_now() to keep TZ unambiguous (telemetry timestamps are ALWAYS
    timezone-aware ISO 8601 — never naive)."""
    return datetime.now(timezone.utc)
