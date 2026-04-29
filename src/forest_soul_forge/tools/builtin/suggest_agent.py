"""``suggest_agent.v1`` — operator-facing agent matcher.

ADR-003X Phase C6. Given a natural-language task description, return
the top-K agents whose role + name + (eventually) skill history fit.
v1 is BM25 over per-agent metadata pulled from the Registry. v2 will
add skill-history weighting once that telemetry is queryable.

Why this exists: once Phase I lands ~30 new role types and the swarm
is running multiple genres concurrently, an operator can't keep them
all in their head. ``suggest_agent.v1`` is the tab-completion
equivalent — describe the task in English, get a ranked shortlist
plus a one-sentence reason per candidate.

Per-agent corpus (v1):
    - role
    - agent_name
    - genre (if the role is claimed by a genre)

The corpus is intentionally narrow in v1. Adding soul.md voice text,
recent tool-call history, or skill-completion stats are all
incremental wins for v2 — they move the matching from "the role this
agent is" toward "the work this agent has actually done." For the
catalog-scaling problem v1 solves, role+name is enough.

Side effects: read_only. The tool only enumerates the registry; it
never births, writes, or signals.

This is the FIRST tool that reads from ``ctx.agent_registry``. If the
dispatcher wasn't given a Registry handle (test contexts), the tool
refuses cleanly with ``ToolValidationError``.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

DEFAULT_TOP_K = 5
MAX_TOP_K = 50

# BM25 hyperparameters — standard tuning, known good across most
# short-document corpora. k1 controls term-frequency saturation
# (higher = each repeat counts more); b controls length normalization
# (1.0 = full normalization, 0.0 = none).
_BM25_K1 = 1.5
_BM25_B = 0.75

# Token regex — letters + digits, lowercased. Splits on EVERYTHING
# else (punctuation, whitespace, hyphens, AND underscores). Splitting
# on underscore is intentional: snake_case role names like 'log_lurker'
# tokenize to ['log', 'lurker'] so a natural-language query like
# 'watches logs' can match. Exact-match queries still work because
# both query and corpus tokenize the same way.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SuggestAgentTool:
    """Args:
      task (str, required): natural-language task description.
      top_k (int, optional): max candidates to return. Default 5, max 50.
      filter (object, optional):
        genre (str): restrict to agents in this genre.
        status (str): restrict to agents with this status. Default 'active'.

    Output:
      {
        "candidates": [
          {"instance_id": str, "agent_name": str, "role": str,
           "genre": str|None, "score": float, "reason": str},
          ...
        ],
        "matched": int,         # candidates with score > 0
        "scanned": int,         # total agents that passed the filter
      }
    """

    name = "suggest_agent"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        task = args.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ToolValidationError(
                "task is required and must be a non-empty string"
            )
        top_k = args.get("top_k", DEFAULT_TOP_K)
        if not isinstance(top_k, int) or top_k < 1 or top_k > MAX_TOP_K:
            raise ToolValidationError(
                f"top_k must be an integer in [1, {MAX_TOP_K}]; got {top_k!r}"
            )
        flt = args.get("filter")
        if flt is not None:
            if not isinstance(flt, dict):
                raise ToolValidationError("filter must be an object when provided")
            for k in ("genre", "status"):
                v = flt.get(k)
                if v is not None and not isinstance(v, str):
                    raise ToolValidationError(
                        f"filter.{k} must be a string when provided; got {type(v).__name__}"
                    )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        registry = ctx.agent_registry
        if registry is None:
            raise ToolValidationError(
                "suggest_agent.v1: no agent registry wired into this dispatcher "
                "(test context, or daemon constructed without registry). "
                "Cannot enumerate candidates."
            )

        task: str = args["task"]
        top_k: int = int(args.get("top_k", DEFAULT_TOP_K))
        flt = args.get("filter") or {}
        filter_genre = flt.get("genre")
        filter_status = flt.get("status", "active")

        # Pull agents matching the structural filter. status defaults
        # to 'active' so a bare query doesn't surface archived agents.
        try:
            agents = registry.list_agents(status=filter_status)
        except Exception as e:
            raise ToolValidationError(
                f"agent registry list_agents failed: {e}"
            ) from e

        # Genre filter is post-hoc because list_agents doesn't natively
        # filter by genre — genre comes from the role-to-genre map on
        # the genre engine. We resolve genre per-agent during scoring.
        genre_engine = ctx.constraints.get("genre_engine")

        scanned = len(agents)
        if scanned == 0:
            return ToolResult(
                output={"candidates": [], "matched": 0, "scanned": 0},
                metadata={"task_tokens": _tokenize(task), "filter": dict(flt)},
                side_effect_summary=(
                    "suggest_agent: 0 candidates (registry empty after filter)"
                ),
            )

        # Build a per-agent corpus document. Each doc is the bag of
        # tokens we'll BM25-rank against the task query.
        docs: list[list[str]] = []
        meta: list[dict[str, Any]] = []
        for a in agents:
            agent_genre = _resolve_genre(genre_engine, a.role)
            if filter_genre and agent_genre != filter_genre:
                continue  # post-hoc filter
            corpus_text = " ".join(filter(None, [
                a.role,
                a.agent_name or "",
                agent_genre or "",
            ]))
            doc_tokens = _tokenize(corpus_text)
            docs.append(doc_tokens)
            meta.append({
                "instance_id": a.instance_id,
                "agent_name": a.agent_name,
                "role": a.role,
                "genre": agent_genre,
                "corpus_text": corpus_text,
            })

        if not docs:
            return ToolResult(
                output={"candidates": [], "matched": 0, "scanned": scanned},
                metadata={"task_tokens": _tokenize(task), "filter": dict(flt)},
                side_effect_summary=(
                    f"suggest_agent: 0 candidates (genre filter excluded all {scanned} agents)"
                ),
            )

        # Score every passed-filter agent via BM25.
        query_tokens = _tokenize(task)
        scores = _bm25_scores(query_tokens, docs)

        # Sort by score desc, then by agent_name for deterministic ties.
        ranked = sorted(
            zip(scores, meta, docs),
            key=lambda t: (-t[0], t[1]["agent_name"] or ""),
        )
        candidates: list[dict[str, Any]] = []
        matched = 0
        for score, m, _ in ranked[:top_k]:
            if score > 0:
                matched += 1
            candidates.append({
                "instance_id": m["instance_id"],
                "agent_name": m["agent_name"],
                "role": m["role"],
                "genre": m["genre"],
                "score": round(float(score), 4),
                "reason": _reason_for(m, query_tokens),
            })

        return ToolResult(
            output={
                "candidates": candidates,
                "matched": matched,
                "scanned": scanned,
            },
            metadata={
                "task_tokens": query_tokens,
                "filter": dict(flt),
                "top_k": top_k,
            },
            side_effect_summary=(
                f"suggest_agent: ranked {len(candidates)}/{scanned} "
                f"({matched} with non-zero score) for task[:40]={task[:40]!r}"
            ),
        )


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on non-word boundaries + minimal plural strip.

    The plural strip is the smallest stemmer that catches the cases an
    operator actually hits: 'logs' → 'log', 'anomalies' → 'anomaly',
    'watchers' → 'watcher'. We deliberately don't pull in Porter or
    Snowball — they add a real dependency for marginal recall, and
    over-aggressive stemming hurts precision (e.g. 'data' → 'dat').
    """
    return [_strip_plural(t) for t in _TOKEN_RE.findall((text or "").lower())]


def _strip_plural(token: str) -> str:
    """Tiny pluralization stripper. Two rules, in order:

    - 'ies' (>4) → 'y'   (anomalies → anomaly, agencies → agency)
    - 's'   (>3) → ''    (logs → log, watchers → watcher)

    Length floors avoid mangling short words (we, is, his, css, dns).
    The 'ss' tail-guard preserves things like 'css', 'class', 'access'.

    Edge cases we deliberately don't handle (would need a real stemmer):
      - 'analyses' / 'theses' (Greek -is plurals) — they degrade to
        'analyse' / 'these' under this rule, which is wrong but rare.
      - irregular plurals (children, mice, feet) — no rule helps.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _resolve_genre(genre_engine: Any, role: str) -> str | None:
    """Best-effort genre lookup. Returns None when the engine is None,
    the role is unclaimed, or any error occurs (we never want a genre
    lookup failure to crash a suggestion call)."""
    if genre_engine is None:
        return None
    try:
        gd = genre_engine.for_role(role)
        return gd.name
    except Exception:
        return None


def _bm25_scores(query: list[str], docs: list[list[str]]) -> list[float]:
    """Score each document against the query via Okapi BM25.

    Standard formula:
        IDF(t) = log((N - df + 0.5) / (df + 0.5) + 1)
        score(d, q) = Σ_{t in q} IDF(t) · (tf · (k1 + 1)) /
                                   (tf + k1 · (1 − b + b · |d| / avgdl))

    Returns a list of scores in the same order as `docs`. Empty query
    or empty corpus returns all zeros.
    """
    if not query or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    doc_lens = [len(d) for d in docs]
    avgdl = sum(doc_lens) / n if n else 0.0

    # Doc-frequency per query term.
    df: dict[str, int] = {}
    for term in set(query):
        df[term] = sum(1 for d in docs if term in d)

    # IDF per query term (BM25's IDF can be negative; clamp at 0 to
    # avoid penalizing matches on common terms — common practice).
    idf: dict[str, float] = {}
    for term, dft in df.items():
        idf[term] = max(0.0, math.log((n - dft + 0.5) / (dft + 0.5) + 1.0))

    scores: list[float] = []
    for i, d in enumerate(docs):
        dl = doc_lens[i]
        tf = Counter(d)
        s = 0.0
        for term in query:
            if term not in tf:
                continue
            freq = tf[term]
            denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / (avgdl or 1.0)))
            numer = freq * (_BM25_K1 + 1)
            s += idf[term] * (numer / denom)
        scores.append(s)
    return scores


def _reason_for(m: dict[str, Any], query: list[str]) -> str:
    """Human-readable one-liner for why this candidate ranked. Surfaces
    the matched terms — operators can glance at "matched: log, scan"
    and see why log_lurker came up first."""
    doc_tokens = set(_tokenize(m["corpus_text"]))
    matched = [t for t in query if t in doc_tokens]
    if not matched:
        return f"role={m['role']!r} (no exact-token match; ranked by BM25 floor)"
    matched_unique = []
    for t in matched:
        if t not in matched_unique:
            matched_unique.append(t)
    role_part = f"role={m['role']!r}"
    if m.get("genre"):
        role_part += f" (genre={m['genre']})"
    return f"{role_part} — matched on: {', '.join(matched_unique)}"
