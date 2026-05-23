"""``curriculum_design.v1`` — ADR-0089 Phase A curriculum composer.

Composes a topic-prerequisite directed acyclic graph (DAG) from a
goal topic + an operator-supplied topic catalog (e.g. recent D1
catalog reads) + operator-profile context (current expertise
level, learning style hints). Returns an ordered learning path —
the ordering is a deterministic topological walk of the DAG with
ties broken by depth then catalog index, so two calls with the same
inputs always produce the same path.

Read-only. The ``curriculum_design.v1`` skill wraps this tool with
operator-profile context + memory_recall of prior catalogs + a
memory_write of the final attestation; the LLM-grade narrative on
top is layered separately. Deterministic so the operator can audit
and replay the curriculum.

## Scoring model — why deterministic

Curriculum design is a long-lived artifact the operator will consult
repeatedly through a multi-week or multi-month learning arc. An
LLM-generated path is opaque and unrepeatable; small prompt
variations would shuffle the order or drop topics, which destroys
the trust contract with the operator who's pacing their study
schedule against it. A deterministic topo-walk over an
operator-curated catalog keeps the path replayable + auditable +
diff-able when the catalog changes.

## Output shape

The DAG nodes are catalog entries (``slug`` + ``title`` + optional
``prereq_slugs``). Edges run from prereq → dependent. The ordered
path is the topological order with the following stable
tie-breaking:

1. Topics with FEWER unmet prereqs come first.
2. Then by ``depth`` (BFS distance from any root) — operator
   gets shallower foundations before deep dives.
3. Then by catalog index — preserves operator's source ordering as
   the final tiebreaker.

Cycles in the catalog (A requires B requires A) are reported but
NOT auto-broken. The tool returns ``has_cycles: true`` and lists
the cycle members; the operator decides whether to redo the
prereq declarations or drop a topic.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_CATALOG_ENTRIES = 200
_MAX_SLUG_LEN = 200
_MAX_TITLE_LEN = 500
_MAX_PREREQS_PER_ENTRY = 50


class CurriculumDesignTool:
    """Compose a learning path DAG from a goal + topic catalog.

    Args:
      goal_topic (str, required): the operator's stated end goal —
        the topic they want to be fluent in by the end of the
        curriculum. Used as the root of the BFS depth calculation.
      catalog (list[dict], required): topic entries the curriculum
        is composed from. Each entry:

          - ``slug`` (str, required): kebab-case identifier
          - ``title`` (str, required): human-readable name
          - ``prereq_slugs`` (list[str], optional): topics that
            must precede this one
          - ``current_familiarity`` (int 0..10, optional): how
            well the operator already knows this topic. Default 0
            (cold). When >= 7 the topic is "already known" and is
            excluded from the path but kept in the DAG as a
            satisfied prereq.
      expertise_level (str, optional): operator's general level
        (``novice``, ``intermediate``, ``advanced``). Used to
        adjust depth weighting. Default ``novice``.
      target_weeks (int, optional): operator's intended duration
        (1..52). Default 12. Surfaced in metadata for the
        downstream skill's scheduling narrative; doesn't affect
        the path ordering.

    Output:
      {
        "generated_at":      str (ISO),
        "goal_topic":        str,
        "expertise_level":   str,
        "target_weeks":      int,
        "ordered_path":      [{
          "rank":             int,    # 1-based
          "slug":             str,
          "title":            str,
          "depth":            int,    # BFS depth from goal_topic
          "unmet_prereqs":    [str, ...],
          "current_familiarity": int,
        }, ...],
        "dag": {
          "nodes":            [str, ...],   # all slugs
          "edges":            [[str, str], ...],   # prereq → dependent
        },
        "has_cycles":        bool,
        "cycle_members":     [str, ...],
        "already_known":     [str, ...],
        "orphan_prereqs":    [str, ...],   # referenced but not defined
        "summary": {
          "catalog_size":    int,
          "path_size":       int,
          "max_depth":       int,
        },
      }
    """

    name = "curriculum_design"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        goal = args.get("goal_topic")
        if not isinstance(goal, str) or not goal.strip():
            raise ToolValidationError(
                "goal_topic must be a non-empty string"
            )
        if len(goal) > _MAX_TITLE_LEN:
            raise ToolValidationError(
                f"goal_topic must be <= {_MAX_TITLE_LEN} chars"
            )

        catalog = args.get("catalog")
        if not isinstance(catalog, list):
            raise ToolValidationError("catalog must be a list")
        if not catalog:
            raise ToolValidationError(
                "catalog must contain at least one entry"
            )
        if len(catalog) > _MAX_CATALOG_ENTRIES:
            raise ToolValidationError(
                f"catalog must have <= {_MAX_CATALOG_ENTRIES} entries; "
                f"got {len(catalog)}"
            )

        seen_slugs: set[str] = set()
        for i, entry in enumerate(catalog):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"catalog[{i}] must be a dict; got {type(entry).__name__}"
                )
            slug = entry.get("slug")
            if not isinstance(slug, str) or not slug.strip():
                raise ToolValidationError(
                    f"catalog[{i}].slug must be a non-empty string"
                )
            if len(slug) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"catalog[{i}].slug must be <= {_MAX_SLUG_LEN} chars"
                )
            if slug in seen_slugs:
                raise ToolValidationError(
                    f"catalog[{i}].slug duplicates earlier entry: {slug!r}"
                )
            seen_slugs.add(slug)
            title = entry.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ToolValidationError(
                    f"catalog[{i}].title must be a non-empty string"
                )
            if len(title) > _MAX_TITLE_LEN:
                raise ToolValidationError(
                    f"catalog[{i}].title must be <= {_MAX_TITLE_LEN} chars"
                )
            pre = entry.get("prereq_slugs", [])
            if not isinstance(pre, list):
                raise ToolValidationError(
                    f"catalog[{i}].prereq_slugs must be a list"
                )
            if len(pre) > _MAX_PREREQS_PER_ENTRY:
                raise ToolValidationError(
                    f"catalog[{i}].prereq_slugs count must be "
                    f"<= {_MAX_PREREQS_PER_ENTRY}"
                )
            for j, p in enumerate(pre):
                if not isinstance(p, str) or not p.strip():
                    raise ToolValidationError(
                        f"catalog[{i}].prereq_slugs[{j}] "
                        f"must be a non-empty string"
                    )
            cf = entry.get("current_familiarity")
            if cf is not None:
                if (
                    not isinstance(cf, (int, float))
                    or cf < 0 or cf > 10
                ):
                    raise ToolValidationError(
                        f"catalog[{i}].current_familiarity must be in "
                        f"[0, 10]; got {cf}"
                    )

        level = args.get("expertise_level")
        if level is not None:
            if not isinstance(level, str) or level not in {
                "novice", "intermediate", "advanced",
            }:
                raise ToolValidationError(
                    "expertise_level must be one of "
                    "novice / intermediate / advanced"
                )

        weeks = args.get("target_weeks")
        if weeks is not None:
            if (
                not isinstance(weeks, int)
                or weeks < 1 or weeks > 52
            ):
                raise ToolValidationError(
                    "target_weeks must be an integer in [1, 52]"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        goal_topic = args["goal_topic"]
        catalog = args["catalog"]
        expertise_level = args.get("expertise_level") or "novice"
        target_weeks = int(args.get("target_weeks") or 12)

        nodes: list[str] = []
        node_titles: dict[str, str] = {}
        node_prereqs: dict[str, list[str]] = {}
        familiarity: dict[str, int] = {}
        catalog_index: dict[str, int] = {}
        for i, entry in enumerate(catalog):
            slug = entry["slug"]
            nodes.append(slug)
            node_titles[slug] = entry["title"]
            node_prereqs[slug] = list(entry.get("prereq_slugs") or [])
            cf = entry.get("current_familiarity")
            familiarity[slug] = int(cf) if cf is not None else 0
            catalog_index[slug] = i

        edges: list[list[str]] = []
        orphan_prereqs: set[str] = set()
        for slug in nodes:
            for p in node_prereqs[slug]:
                if p not in node_titles:
                    orphan_prereqs.add(p)
                    continue
                edges.append([p, slug])

        has_cycles, cycle_members = _detect_cycles(nodes, node_prereqs)

        depths = _bfs_depths(goal_topic, nodes, node_prereqs)

        already_known = sorted(
            s for s in nodes if familiarity.get(s, 0) >= 7
        )
        known_set = set(already_known)

        eligible = [s for s in nodes if s not in known_set]
        unmet_count = {
            s: sum(
                1 for p in node_prereqs[s]
                if p in node_titles and p not in known_set
            )
            for s in eligible
        }

        topo_order = _stable_topo(
            eligible, node_prereqs, known_set, depths,
            unmet_count, catalog_index,
        )

        ordered_path: list[dict[str, Any]] = []
        for rank, slug in enumerate(topo_order, start=1):
            unmet_list = [
                p for p in node_prereqs[slug]
                if p in node_titles and p not in known_set
            ]
            ordered_path.append({
                "rank":                 rank,
                "slug":                 slug,
                "title":                node_titles[slug],
                "depth":                depths.get(slug, -1),
                "unmet_prereqs":        unmet_list,
                "current_familiarity":  familiarity.get(slug, 0),
            })

        max_depth = (
            max(depths.values()) if depths else 0
        )
        summary = {
            "catalog_size":  len(nodes),
            "path_size":     len(ordered_path),
            "max_depth":     max_depth,
        }

        body = {
            "generated_at":     datetime.now(timezone.utc)
                                          .replace(tzinfo=None)
                                          .isoformat(timespec="seconds")
                                          + "Z",
            "goal_topic":       goal_topic,
            "expertise_level":  expertise_level,
            "target_weeks":     target_weeks,
            "ordered_path":     ordered_path,
            "dag": {
                "nodes":   nodes,
                "edges":   edges,
            },
            "has_cycles":      has_cycles,
            "cycle_members":   cycle_members,
            "already_known":   already_known,
            "orphan_prereqs":  sorted(orphan_prereqs),
            "summary":         summary,
        }
        return ToolResult(
            output=body,
            metadata={
                "catalog_size":  summary["catalog_size"],
                "path_size":     summary["path_size"],
                "has_cycles":    has_cycles,
                "goal_topic":    goal_topic,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"curriculum {summary['path_size']} topic"
                f"{'s' if summary['path_size'] != 1 else ''} "
                f"toward {goal_topic!r} "
                f"(depth={summary['max_depth']}, "
                f"cycles={'yes' if has_cycles else 'no'})"
            ),
        )


def _detect_cycles(
    nodes: list[str], prereqs: dict[str, list[str]],
) -> tuple[bool, list[str]]:
    """DFS three-color cycle scan; returns every node on any cycle.

    Self-loops (A requires A) count. Multi-node cycles count.
    Orphan prereqs (referenced but not in nodes) are skipped, not
    flagged.
    """
    node_set = set(nodes)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes}
    cycle_members: set[str] = set()

    def _dfs(start: str) -> None:
        stack: list[tuple[str, list[str]]] = [
            (start, list(prereqs.get(start, []))),
        ]
        color[start] = GRAY
        path: list[str] = [start]
        path_set: set[str] = {start}
        while stack:
            cur, pending = stack[-1]
            advanced = False
            while pending:
                nxt = pending.pop(0)
                if nxt not in node_set:
                    continue
                if nxt in path_set:
                    cycle_start = path.index(nxt)
                    for m in path[cycle_start:]:
                        cycle_members.add(m)
                    continue
                if color[nxt] == BLACK:
                    continue
                color[nxt] = GRAY
                path.append(nxt)
                path_set.add(nxt)
                stack.append((nxt, list(prereqs.get(nxt, []))))
                advanced = True
                break
            if not advanced:
                color[cur] = BLACK
                stack.pop()
                if path and path[-1] == cur:
                    path.pop()
                    path_set.discard(cur)

    for n in nodes:
        if n in prereqs.get(n, []):
            cycle_members.add(n)
        if color[n] == WHITE:
            _dfs(n)
    return bool(cycle_members), sorted(cycle_members)


def _bfs_depths(
    goal: str, nodes: list[str], prereqs: dict[str, list[str]],
) -> dict[str, int]:
    """BFS depth from the goal walking backwards through prereqs."""
    depths: dict[str, int] = {}
    if goal not in nodes:
        # Goal isn't in the catalog (the operator named a target
        # that hasn't been catalogued). Depth defaults to catalog
        # rank so the ordering still works.
        for i, n in enumerate(nodes):
            depths[n] = i
        return depths
    depths[goal] = 0
    queue: list[tuple[str, int]] = [(goal, 0)]
    while queue:
        cur, d = queue.pop(0)
        for p in prereqs.get(cur, []):
            if p not in depths and p in set(nodes):
                depths[p] = d + 1
                queue.append((p, d + 1))
    # Any catalog entries not reachable from the goal get a default
    # depth that sorts them after the reachable set.
    max_reachable = max(depths.values(), default=0)
    for n in nodes:
        if n not in depths:
            depths[n] = max_reachable + 1
    return depths


def _stable_topo(
    eligible: list[str],
    prereqs: dict[str, list[str]],
    known: set[str],
    depths: dict[str, int],
    unmet_count: dict[str, int],
    catalog_index: dict[str, int],
) -> list[str]:
    """Topological sort with deterministic tie-breaking.

    Returns nodes from ``eligible`` in an order satisfying
    prereqs (filtering out ``known`` and orphan prereqs). Ties
    broken by:
      1. fewer unmet prereqs first,
      2. shallower depth (closer to the foundation),
      3. catalog index (operator's stated order).

    Cycles are handled by returning the partial topo and appending
    the remaining nodes in catalog-index order. The caller knows
    cycles exist via has_cycles, so the surfaced order remains
    deterministic + complete.
    """
    eligible_set = set(eligible)
    remaining_prereqs: dict[str, set[str]] = {
        s: {
            p for p in prereqs.get(s, [])
            if p in eligible_set
        }
        for s in eligible
    }

    def _sort_key(slug: str) -> tuple[int, int, int]:
        return (
            unmet_count.get(slug, 0),
            depths.get(slug, 1_000_000),
            catalog_index.get(slug, 1_000_000),
        )

    ordered: list[str] = []
    pending = set(eligible)
    while pending:
        ready = [
            s for s in pending if not remaining_prereqs[s]
        ]
        if not ready:
            # Cycle remainder — surface in catalog order.
            ready = sorted(
                pending, key=lambda s: catalog_index.get(s, 0),
            )
            for s in ready:
                ordered.append(s)
                pending.discard(s)
            break
        ready.sort(key=_sort_key)
        chosen = ready[0]
        ordered.append(chosen)
        pending.discard(chosen)
        for s in pending:
            remaining_prereqs[s].discard(chosen)
    return ordered
