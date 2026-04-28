"""``lateral_movement_detect.v1`` — graph analysis over connection tuples.

ADR-0033 Phase B2. NetNinja's primary surface: given a list of
(source, destination) connection records (typically the output of
``traffic_flow_local.v1`` once that lands), build a directed graph
and surface patterns associated with lateral movement:

  * **fan_out** — sources connecting to many distinct destinations
  * **fan_in**  — destinations receiving from many distinct sources
  * **new_edges** — (src, dst) pairs absent from a baseline edge set
                    the caller passes in. The baseline is typically
                    a list of (src, dst) tuples persisted from a
                    prior healthy snapshot via memory_write.
  * **distinct_ports** — sources hitting many distinct dst ports
                          (port-scan signature when concentrated
                          on a single dst)

Each pattern is reported with its triggering nodes/edges and a
score (count divided by the operator-supplied threshold). Patterns
that don't trigger return empty arrays — not absence in the output.

side_effects=read_only — pure graph analysis over the input.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_EDGES = 100000
_MAX_BASELINE_EDGES = 100000


class LateralMovementDetectTool:
    """Find lateral-movement-shaped patterns in a connection graph.

    Args:
      edges (list[object], required): connection records. Each must
        have ``src`` and ``dst`` (strings); optional ``port`` (int)
        for port-fan-out detection. Extra fields are passed through
        to the matched-edge detail.

      baseline_edges (list[object], optional): list of objects with
        ``src``/``dst`` representing the known-good connection graph.
        Edges in the input that aren't in the baseline are reported
        as new_edges. Omit to skip new-edge detection.

      thresholds (object, optional): override the default trigger
        thresholds:
          - fan_out_min: int (default 10)
          - fan_in_min: int (default 10)
          - distinct_ports_min: int (default 20)

      max_examples (int, optional): cap on examples returned per
        pattern. Default 25.

    Output:
      {
        "edge_count":     int,
        "node_count":     int,
        "fan_out":        [{"src": str, "distinct_dsts": int, "examples": [str, ...]}, ...],
        "fan_in":         [{"dst": str, "distinct_srcs": int, "examples": [str, ...]}, ...],
        "new_edges":      [{"src": str, "dst": str}, ...],
        "distinct_ports": [{"src": str, "dst": str, "ports": int, "examples": [int, ...]}, ...],
        "thresholds":     {fan_out_min, fan_in_min, distinct_ports_min}
      }
    """

    name = "lateral_movement_detect"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        edges = args.get("edges")
        if not isinstance(edges, list):
            raise ToolValidationError("edges must be a list of dicts")
        if len(edges) > _MAX_EDGES:
            raise ToolValidationError(
                f"edges must be ≤ {_MAX_EDGES}; got {len(edges)}"
            )
        for i, e in enumerate(edges):
            if not isinstance(e, dict):
                raise ToolValidationError(
                    f"edges[{i}] must be a dict; got {type(e).__name__}"
                )
            if not isinstance(e.get("src"), str) or not isinstance(e.get("dst"), str):
                raise ToolValidationError(
                    f"edges[{i}] must have string 'src' and 'dst'"
                )
        baseline = args.get("baseline_edges")
        if baseline is not None:
            if not isinstance(baseline, list):
                raise ToolValidationError(
                    "baseline_edges must be a list of dicts when provided"
                )
            if len(baseline) > _MAX_BASELINE_EDGES:
                raise ToolValidationError(
                    f"baseline_edges must be ≤ {_MAX_BASELINE_EDGES}; "
                    f"got {len(baseline)}"
                )
        thresholds = args.get("thresholds")
        if thresholds is not None:
            if not isinstance(thresholds, dict):
                raise ToolValidationError(
                    "thresholds must be a mapping when provided"
                )
            for k in ("fan_out_min", "fan_in_min", "distinct_ports_min"):
                v = thresholds.get(k)
                if v is not None and (not isinstance(v, int) or v < 1):
                    raise ToolValidationError(
                        f"thresholds.{k} must be a positive integer; got {v!r}"
                    )
        max_examples = args.get("max_examples")
        if max_examples is not None:
            if not isinstance(max_examples, int) or max_examples < 1 or max_examples > 1000:
                raise ToolValidationError(
                    f"max_examples must be 1..1000; got {max_examples!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        edges: list[dict] = args["edges"]
        baseline_raw: list[dict] = args.get("baseline_edges") or []
        thresholds = args.get("thresholds") or {}
        fan_out_min = int(thresholds.get("fan_out_min") or 10)
        fan_in_min = int(thresholds.get("fan_in_min") or 10)
        distinct_ports_min = int(thresholds.get("distinct_ports_min") or 20)
        max_examples = int(args.get("max_examples") or 25)

        # Build the directed graph + per-(src,dst) port set.
        out_neighbors: dict[str, set] = defaultdict(set)
        in_neighbors: dict[str, set] = defaultdict(set)
        pair_ports: dict[tuple, set] = defaultdict(set)
        for e in edges:
            src, dst = e["src"], e["dst"]
            out_neighbors[src].add(dst)
            in_neighbors[dst].add(src)
            port = e.get("port")
            if isinstance(port, int):
                pair_ports[(src, dst)].add(port)

        nodes = set(out_neighbors.keys()) | set(in_neighbors.keys())

        # fan_out: sources with > fan_out_min distinct destinations.
        fan_out = []
        for src, dsts in sorted(
            out_neighbors.items(),
            key=lambda kv: -len(kv[1]),
        ):
            if len(dsts) >= fan_out_min:
                fan_out.append({
                    "src":           src,
                    "distinct_dsts": len(dsts),
                    "examples":      sorted(dsts)[:max_examples],
                })

        # fan_in: dests with > fan_in_min distinct sources.
        fan_in = []
        for dst, srcs in sorted(
            in_neighbors.items(),
            key=lambda kv: -len(kv[1]),
        ):
            if len(srcs) >= fan_in_min:
                fan_in.append({
                    "dst":           dst,
                    "distinct_srcs": len(srcs),
                    "examples":      sorted(srcs)[:max_examples],
                })

        # new_edges: in input but not in baseline.
        baseline_set: set = set()
        for be in baseline_raw:
            if isinstance(be, dict) and isinstance(be.get("src"), str) and isinstance(be.get("dst"), str):
                baseline_set.add((be["src"], be["dst"]))
        new_edges = []
        seen_input: set = set()
        if baseline_set:
            for e in edges:
                pair = (e["src"], e["dst"])
                if pair in seen_input:
                    continue
                seen_input.add(pair)
                if pair not in baseline_set:
                    new_edges.append({"src": e["src"], "dst": e["dst"]})

        # distinct_ports: per-(src,dst) port count above threshold.
        distinct_ports = []
        for (src, dst), ports in sorted(
            pair_ports.items(),
            key=lambda kv: -len(kv[1]),
        ):
            if len(ports) >= distinct_ports_min:
                distinct_ports.append({
                    "src":      src,
                    "dst":      dst,
                    "ports":    len(ports),
                    "examples": sorted(ports)[:max_examples],
                })

        return ToolResult(
            output={
                "edge_count":     len(edges),
                "node_count":     len(nodes),
                "fan_out":        fan_out,
                "fan_in":         fan_in,
                "new_edges":      new_edges,
                "distinct_ports": distinct_ports,
                "thresholds": {
                    "fan_out_min":         fan_out_min,
                    "fan_in_min":          fan_in_min,
                    "distinct_ports_min":  distinct_ports_min,
                },
            },
            metadata={
                "baseline_edges_count": len(baseline_set),
                "max_examples":         max_examples,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{len(edges)} edges → "
                f"{len(fan_out)} fan-out, {len(fan_in)} fan-in, "
                f"{len(new_edges)} new-edge, {len(distinct_ports)} port-scan"
            ),
        )
