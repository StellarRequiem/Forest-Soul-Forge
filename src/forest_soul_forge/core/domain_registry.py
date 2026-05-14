"""Domain registry — ADR-0067 T1 (B279).

The ten-domain platform arc needs a single place that says
"these are the domains that exist, here's what each does, here's
which agents are the entry points, here are the operator-utterance
patterns that route here." This module IS that place.

## Surface

  - :class:`Domain` — frozen dataclass for one domain manifest
  - :class:`DomainRegistry` — collection-with-lookup container
  - :func:`load_domain_registry(path=None)` — read + validate the
    `config/domains/*.yaml` directory; returns
    ``(registry, errors)``. Errors are non-fatal config-level
    problems (one bad manifest doesn't kill the registry).

## Why YAML files, not a registry table

- Operator can author / edit manifests by hand. Matches the
  pattern of genres.yaml, ground_truth.yaml, security_iocs.yaml.
- Domain rollout is independent — drop a new file under
  `config/domains/`, mark status=live when ready, no code change.
- Version control friendly — domain definitions are public-config,
  belong in the repo.

## Status field — three-state rollout discipline

- ``planned`` — registered but no entry agents alive yet. Router
  acknowledges the intent, refuses to dispatch, surfaces "this
  domain is planned, not yet live."
- ``partial`` — some entry agents alive; some capabilities work.
- ``live`` — fully birthed swarm.

T1 ships all ten domains as planned (or partial where existing
substrate covers some capabilities). Subsequent tranches and
ADRs flip the status as each domain's agents get birthed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Default location for domain manifests. Resolved relative to
# repo root; override via FSF_DOMAINS_PATH env var or the explicit
# ``path`` argument to :func:`load_domain_registry`.
DEFAULT_DOMAINS_DIR = Path("config/domains")

ENV_VAR = "FSF_DOMAINS_PATH"

VALID_STATUSES = ("planned", "partial", "live")


class DomainRegistryError(RuntimeError):
    """Raised on hard-fatal registry problems — directory missing,
    duplicate domain_id across manifests, etc. Soft per-manifest
    problems surface as entries in the ``errors`` return value
    rather than as exceptions."""


@dataclass(frozen=True)
class EntryAgent:
    """One entry point into a domain. The orchestrator routes a
    sub-intent to the entry agent whose ``capability`` matches the
    sub-intent's decomposed capability tag.
    """
    role: str
    capability: str


@dataclass(frozen=True)
class Domain:
    """One domain manifest, frozen so the orchestrator can't
    accidentally mutate during routing.
    """
    domain_id: str
    name: str
    status: str
    description: str
    entry_agents: tuple[EntryAgent, ...]
    capabilities: tuple[str, ...]
    example_intents: tuple[str, ...]
    depends_on_substrate: tuple[str, ...] = ()
    depends_on_connectors: tuple[str, ...] = ()
    handoff_targets: tuple[str, ...] = ()
    notes: str = ""

    @property
    def is_dispatchable(self) -> bool:
        """True iff a routing decision targeting this domain can
        actually dispatch (status in {partial, live}).

        Planned domains acknowledge intents but don't route.
        """
        return self.status in ("partial", "live")


@dataclass(frozen=True)
class DomainRegistry:
    """The collection of all loaded domains, with lookup by id."""
    domains: tuple[Domain, ...]

    def by_id(self, domain_id: str) -> Optional[Domain]:
        for d in self.domains:
            if d.domain_id == domain_id:
                return d
        return None

    def domain_ids(self) -> tuple[str, ...]:
        return tuple(d.domain_id for d in self.domains)

    def dispatchable_ids(self) -> tuple[str, ...]:
        """domain_ids of every domain in partial or live status."""
        return tuple(d.domain_id for d in self.domains if d.is_dispatchable)

    def by_capability(self, capability: str) -> tuple[Domain, ...]:
        """Return every domain that lists ``capability`` in its
        capabilities or via one of its entry_agents. Routing
        decomposition uses this to enumerate candidates."""
        out: list[Domain] = []
        for d in self.domains:
            if capability in d.capabilities:
                out.append(d)
                continue
            if any(ea.capability == capability for ea in d.entry_agents):
                out.append(d)
        return tuple(out)


def load_domain_registry(
    path: Path | None = None,
) -> tuple[DomainRegistry, list[str]]:
    """Read every ``*.yaml`` in the domains directory.

    Returns ``(registry, errors)``. Errors are non-fatal config-level
    problems — one bad manifest doesn't kill the registry; the bad
    file's errors surface so the operator can fix them without
    inspecting daemon logs.

    Missing directory raises :class:`DomainRegistryError` (hard
    failure — the orchestrator can't run without any domains).
    Empty directory is benign: returns an empty registry + a single
    informational error.

    Cross-reference validation: every ``handoff_targets`` entry must
    point at another loaded domain. Dangling references surface as
    soft errors.
    """
    import os as _os
    resolved = (
        path if path is not None
        else Path(_os.environ.get(ENV_VAR, str(DEFAULT_DOMAINS_DIR)))
    )

    if not resolved.exists():
        raise DomainRegistryError(
            f"domains directory not found: {resolved}. "
            f"Create the directory and add manifest files (one .yaml "
            f"per domain). See docs/decisions/ADR-0067-cross-domain-"
            f"orchestrator.md for the format."
        )
    if not resolved.is_dir():
        raise DomainRegistryError(
            f"domains path is not a directory: {resolved}"
        )

    errors: list[str] = []
    seen_ids: set[str] = set()
    loaded: list[Domain] = []

    yaml_files = sorted(resolved.glob("*.yaml"))
    if not yaml_files:
        errors.append(
            f"no domain manifests found in {resolved}; "
            f"orchestrator will refuse to route until at least one "
            f"manifest exists."
        )
        return DomainRegistry(domains=()), errors

    for manifest_path in yaml_files:
        domain, file_errors = _load_one_manifest(manifest_path)
        errors.extend(file_errors)
        if domain is None:
            continue
        if domain.domain_id in seen_ids:
            errors.append(
                f"duplicate domain_id {domain.domain_id!r} in "
                f"{manifest_path}; first occurrence kept"
            )
            continue
        seen_ids.add(domain.domain_id)
        loaded.append(domain)

    # Cross-reference: handoff_targets must point at loaded domains.
    valid_ids = {d.domain_id for d in loaded}
    for d in loaded:
        for target in d.handoff_targets:
            if target not in valid_ids:
                errors.append(
                    f"domain {d.domain_id} declares handoff_target "
                    f"{target!r} but no such domain is loaded"
                )

    return DomainRegistry(domains=tuple(loaded)), errors


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_one_manifest(
    path: Path,
) -> tuple[Domain | None, list[str]]:
    """Parse one manifest YAML. Returns ``(domain, errors)``.

    Per-field validation surfaces precise error messages so an
    operator-edited bad value (typo in status, missing capability,
    etc.) points right at the problem.
    """
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, [f"{path}: cannot read: {e}"]

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return None, [f"{path}: malformed YAML: {e}"]

    if not isinstance(raw, dict):
        return None, [f"{path}: top-level must be a YAML mapping"]

    required = {"domain_id", "name", "status", "description",
                "entry_agents", "capabilities"}
    missing = required - set(raw.keys())
    if missing:
        return None, [
            f"{path}: missing required fields: {sorted(missing)}"
        ]

    status = str(raw["status"])
    if status not in VALID_STATUSES:
        return None, [
            f"{path}: status {status!r} not in "
            f"{list(VALID_STATUSES)}"
        ]

    entry_agents_raw = raw.get("entry_agents") or []
    if not isinstance(entry_agents_raw, list):
        return None, [f"{path}: entry_agents must be a list"]
    entry_agents: list[EntryAgent] = []
    for idx, ea_raw in enumerate(entry_agents_raw):
        if not isinstance(ea_raw, dict):
            errors.append(
                f"{path}: entry_agents[{idx}] must be a mapping"
            )
            continue
        role = ea_raw.get("role")
        capability = ea_raw.get("capability")
        if not role or not capability:
            errors.append(
                f"{path}: entry_agents[{idx}] missing role or capability"
            )
            continue
        entry_agents.append(
            EntryAgent(role=str(role), capability=str(capability))
        )

    if not entry_agents and status != "planned":
        errors.append(
            f"{path}: status={status!r} requires at least one valid "
            f"entry_agent; planned domains may have empty entry_agents"
        )

    capabilities = raw.get("capabilities") or []
    if not isinstance(capabilities, list):
        return None, [f"{path}: capabilities must be a list"]

    example_intents = raw.get("example_intents") or []
    if not isinstance(example_intents, list):
        errors.append(f"{path}: example_intents must be a list; ignoring")
        example_intents = []

    domain = Domain(
        domain_id=str(raw["domain_id"]),
        name=str(raw["name"]),
        status=status,
        description=str(raw["description"]).strip(),
        entry_agents=tuple(entry_agents),
        capabilities=tuple(str(c) for c in capabilities),
        example_intents=tuple(str(s) for s in example_intents),
        depends_on_substrate=tuple(
            str(s) for s in (raw.get("depends_on_substrate") or [])
        ),
        depends_on_connectors=tuple(
            str(s) for s in (raw.get("depends_on_connectors") or [])
        ),
        handoff_targets=tuple(
            str(s) for s in (raw.get("handoff_targets") or [])
        ),
        notes=str(raw.get("notes", "")),
    )

    return domain, errors
