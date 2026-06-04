"""Training-task catalog (ADR-0096) — the tiered, repeatable self-test ladder.

A catalog is a YAML list of deterministic, read-only training tasks, each tagged
with a tier (0 = Baseline … 4) and a ``problem_class`` (shared with the trust
graph). A task is one or more steps (tool dispatches), each with a deterministic
acceptance spec. Pure loader — no dispatch, no daemon.

The loader ENFORCES ``side_effects: read_only`` on every task: training tasks may
auto-run (ADR-0096 §1), so anything side-effectful is rejected at load time rather
than trusted to behave at run time.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "fsf.training.v1"


@dataclass(frozen=True)
class TrainingStep:
    tool: str
    version: str
    args: dict[str, Any]
    expect: dict[str, Any]  # deterministic acceptance spec — see acceptance.py


@dataclass(frozen=True)
class TrainingTask:
    id: str
    tier: int
    problem_class: str
    description: str
    side_effects: str
    steps: tuple[TrainingStep, ...]


def load_catalog(path: str | Path) -> list[TrainingTask]:
    """Load + validate a training catalog. Raises ValueError on a malformed
    catalog, an unknown schema, a duplicate id, or a non-read_only task."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("schema") != SCHEMA:
        raise ValueError(
            f"training catalog schema must be {SCHEMA!r}; got {data.get('schema')!r}")
    tasks: list[TrainingTask] = []
    seen: set[str] = set()
    for raw in data.get("tasks", []):
        t = _parse_task(raw)
        if t.id in seen:
            raise ValueError(f"duplicate training task id: {t.id!r}")
        seen.add(t.id)
        tasks.append(t)
    return tasks


def _parse_task(raw: dict) -> TrainingTask:
    for k in ("id", "tier", "problem_class", "steps"):
        if k not in raw:
            raise ValueError(
                f"training task missing required field {k!r}: {raw.get('id', raw)!r}")
    side = raw.get("side_effects", "read_only")
    if side != "read_only":
        # ADR-0096 §1: training tasks auto-run, so they MUST be read-only.
        raise ValueError(
            f"training task {raw['id']!r} has side_effects={side!r}; training "
            "tasks must be read_only (ADR-0096 auto-run rail)")
    steps = tuple(
        TrainingStep(
            tool=str(s["tool"]), version=str(s.get("version", "1")),
            args=dict(s.get("args", {})), expect=dict(s.get("expect", {})))
        for s in raw["steps"])
    if not steps:
        raise ValueError(f"training task {raw['id']!r} has no steps")
    return TrainingTask(
        id=str(raw["id"]), tier=int(raw["tier"]),
        problem_class=str(raw["problem_class"]),
        description=str(raw.get("description", "")),
        side_effects=side, steps=steps)
