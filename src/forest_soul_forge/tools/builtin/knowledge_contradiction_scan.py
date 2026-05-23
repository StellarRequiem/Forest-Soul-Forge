"""``knowledge_contradiction_scan.v1`` — ADR-0086 Phase C scanner.

Walks the calling agent's own private + lineage memory for
statements contradicting a target topic, returns a structured
list of contradiction candidates the caller's skill flags via
``memory_flag_contradiction.v1`` (ADR-0036 T2 substrate). Never
mutates memory — the scan is *advisory* per ADR-0036's
flag-not-rewrite invariant.

## Scope

**Single-agent only** per ADR-0086 Decision 3. The cross-agent
contradiction-scan path is deferred to v0.4; this MVP tool reads
only the *calling* agent's memory (the audit chain's
``role:<calling_agent_role>`` + agent-scoped entries). A future
``scope: cross_agent`` parameter on this tool's input schema is
the planned widening surface.

## How contradictions are detected

The scanner uses a hybrid heuristic:

1. **Topic-tag collection.** Walk the chain for memory_write
   entries with the matching ``topic:<slug>`` tag from the
   calling agent's role/instance — produces the candidate
   set.
2. **Explicit-flag pass.** Catalog blocks whose ``Relationship:``
   field contains ``potential_contradiction:<entry_id>`` or
   ``contradicts:<entry_id>`` are immediately added as
   contradiction candidates (deterministic; the librarian's
   curation skill already classified them).
3. **Lexical-cue pass.** For pairs of entries on the same topic,
   detect contradiction-suggesting lexical cues: negation
   markers (`not`, `never`, `no`, `false`) flipped between the
   pair, or contradictory quantifiers (`always` vs `never`,
   `all` vs `none`, `more than X` vs `less than X`). This is
   a *low-precision* signal; the caller's skill writes the
   pair to memory_flag_contradiction.v1 so operator review can
   confirm or dismiss.

The tool returns the pair list + a per-pair confidence score
(1.0 for explicit-flag, 0.4 for lexical-cue). The caller's skill
filters by confidence before flagging.

side_effects=read_only — the tool reads memory + chain; the
caller's skill writes the flag via memory_flag_contradiction.v1
in a separate step.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_DEFAULT_AUDIT_CHAIN = Path("examples/audit_chain.jsonl")
_MAX_CHAIN_LINES = 100_000
_MAX_CANDIDATES = 200
_DEFAULT_WINDOW_DAYS = 365


# Negation markers that, when present in one entry but flipped
# in another, suggest a potential contradiction. Conservative
# list — false positives are common, so the operator-review gate
# (YELLOW posture + memory_flag_contradiction's flag-not-rewrite
# invariant) is the safety net.
_NEGATION_WORDS = {
    "not", "never", "no", "none", "false", "incorrect",
    "impossible", "cannot", "won't",
}
_OPPOSITE_PAIRS = [
    ("always", "never"),
    ("all", "none"),
    ("must", "must not"),
    ("required", "forbidden"),
    ("true", "false"),
    ("more", "less"),
    ("increases", "decreases"),
    ("supports", "refutes"),
]


class KnowledgeContradictionScanTool:
    """Walk single-agent memory for contradictions on a topic.

    Args:
      topic_slug (str, required): topic to scan. Convention:
        lowercase kebab-case (e.g., "diffusion-models").
      agent_role (str, optional): the calling agent's role.
        When present, the scanner narrows the chain walk to
        entries tagged with this role. When absent, the scanner
        uses the ToolContext's ``role`` field. Single-agent
        scope still enforced — this parameter is for
        test-fixture convenience.
      window_days (int, optional): how far back to walk.
        Default 365.
      audit_chain_path (str, optional): override default chain path.
      min_confidence (float, optional): minimum confidence
        threshold; pairs below this are dropped before return.
        Default 0.0 (return everything; caller filters).
      scope (str, optional): MUST be "single_agent" or absent
        (default). The "cross_agent" value is reserved for the
        v0.4 widening path — passing it raises
        ToolValidationError. This is the load-bearing scope
        gate per ADR-0086 Decision 3.

    Output:
      {
        "topic_slug":         str,
        "scope":              "single_agent",
        "window_days":        int,
        "generated_at":       str (ISO),
        "candidate_count":    int,
        "contradiction_pairs":[{
            "entry_id_a":     str,
            "entry_id_b":     str,
            "confidence":     float,
            "detection_kind": "explicit_flag" | "lexical_cue",
            "evidence":       str,
        }, ...],
        "errors":             [str, ...],
      }
    """

    name = "knowledge_contradiction_scan"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("topic_slug")
        if not isinstance(slug, str) or not slug:
            raise ToolValidationError(
                "topic_slug must be a non-empty string"
            )
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ToolValidationError(
                "topic_slug must be lowercase kebab-case "
                "([a-z0-9-]+)"
            )
        wd = args.get("window_days")
        if wd is not None:
            if not isinstance(wd, int) or wd <= 0:
                raise ToolValidationError(
                    "window_days must be a positive integer"
                )
            if wd > 730:
                raise ToolValidationError(
                    "window_days must be <= 730 (segment scope)"
                )
        mc = args.get("min_confidence")
        if mc is not None:
            if not isinstance(mc, (int, float)):
                raise ToolValidationError(
                    "min_confidence must be a number"
                )
            if not 0.0 <= float(mc) <= 1.0:
                raise ToolValidationError(
                    "min_confidence must be in [0.0, 1.0]"
                )
        scope = args.get("scope")
        if scope is not None:
            if scope == "cross_agent":
                raise ToolValidationError(
                    "scope=cross_agent is deferred to v0.4 per "
                    "ADR-0086 Decision 3; single-agent only at MVP"
                )
            if scope != "single_agent":
                raise ToolValidationError(
                    "scope must be 'single_agent' (default) or "
                    "absent"
                )
        if "audit_chain_path" in args and not isinstance(
            args["audit_chain_path"], str,
        ):
            raise ToolValidationError(
                "audit_chain_path must be a string"
            )
        if "agent_role" in args and not isinstance(
            args["agent_role"], str,
        ):
            raise ToolValidationError(
                "agent_role must be a string"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        topic_slug: str = args["topic_slug"]
        window_days = int(
            args.get("window_days") or _DEFAULT_WINDOW_DAYS,
        )
        chain_path = Path(
            args.get("audit_chain_path") or _DEFAULT_AUDIT_CHAIN,
        )
        min_confidence = float(args.get("min_confidence") or 0.0)
        agent_role = args.get("agent_role") or ctx.role

        errors: list[str] = []
        cutoff = time.time() - (window_days * 86400)
        topic_tag = f"topic:{topic_slug}"

        # Collect the candidate set: entries tagged topic:<slug>
        # from this agent's role.
        entries: list[dict[str, Any]] = []
        if chain_path.exists():
            try:
                with chain_path.open() as f:
                    for i, line in enumerate(f):
                        if i >= _MAX_CHAIN_LINES:
                            break
                        if len(entries) >= _MAX_CANDIDATES:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = _entry_ts(entry)
                        if ts is None or ts < cutoff:
                            continue
                        tags = _entry_tags(entry)
                        if topic_tag not in tags:
                            continue
                        # Single-agent scope: entries must
                        # belong to this agent's role. Skip
                        # entries from other roles; this is the
                        # scope gate at the data level.
                        if agent_role:
                            entry_role = _role_from_entry(entry)
                            if entry_role and entry_role != agent_role:
                                continue
                        entry_id = _entry_id(entry)
                        if not entry_id:
                            continue
                        entries.append({
                            "entry_id": entry_id,
                            "ts":       ts,
                            "content":  _entry_content(entry),
                            "tags":     tags,
                        })
            except OSError as e:
                errors.append(f"chain read error: {e}")
        else:
            errors.append(f"audit chain not found: {chain_path}")

        pairs: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()

        # Pass 1 — explicit-flag detection. Highest confidence.
        for ent in entries:
            target = _parse_contradiction_target(ent["content"])
            if not target:
                continue
            pair_key = _norm_pair_key(ent["entry_id"], target)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            pairs.append({
                "entry_id_a":     ent["entry_id"],
                "entry_id_b":     target,
                "confidence":     1.0,
                "detection_kind": "explicit_flag",
                "evidence":       "Relationship: contradicts/"
                                  "potential_contradiction "
                                  "field in catalog block",
            })

        # Pass 2 — lexical-cue detection. Low precision; the
        # caller filters by confidence before flagging.
        for i, a in enumerate(entries):
            for b in entries[i + 1:]:
                pair_key = _norm_pair_key(
                    a["entry_id"], b["entry_id"],
                )
                if pair_key in seen_pairs:
                    continue
                cue = _detect_lexical_contradiction(
                    a["content"], b["content"],
                )
                if not cue:
                    continue
                seen_pairs.add(pair_key)
                pairs.append({
                    "entry_id_a":     a["entry_id"],
                    "entry_id_b":     b["entry_id"],
                    "confidence":     0.4,
                    "detection_kind": "lexical_cue",
                    "evidence":       cue,
                })

        # Filter by confidence threshold.
        filtered = [
            p for p in pairs if p["confidence"] >= min_confidence
        ]

        body = {
            "topic_slug":          topic_slug,
            "scope":               "single_agent",
            "window_days":         window_days,
            "generated_at":        datetime.now(timezone.utc)
                                           .replace(tzinfo=None)
                                           .isoformat(timespec="seconds")
                                           + "Z",
            "candidate_count":     len(entries),
            "contradiction_pairs": filtered,
            "errors":              errors,
        }
        return ToolResult(
            output=body,
            metadata={
                "candidate_count":     len(entries),
                "pair_count":          len(filtered),
                "explicit_flag_count": sum(
                    1 for p in filtered
                    if p["detection_kind"] == "explicit_flag"
                ),
                "lexical_cue_count":   sum(
                    1 for p in filtered
                    if p["detection_kind"] == "lexical_cue"
                ),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"contradiction_scan {topic_slug}: "
                f"{len(entries)} candidates, {len(filtered)} pairs"
            ),
        )


def _entry_ts(entry: dict[str, Any]) -> float | None:
    raw = entry.get("ts") or entry.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            s = raw.rstrip("Z")
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


def _entry_tags(entry: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for top_key in ("tags", "payload_tags"):
        raw = entry.get(top_key)
        if isinstance(raw, list):
            found.extend(t for t in raw if isinstance(t, str))
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            sub = nested.get("tags")
            if isinstance(sub, list):
                found.extend(t for t in sub if isinstance(t, str))
    return found


def _entry_content(entry: dict[str, Any]) -> str:
    for top_key in ("content", "body"):
        v = entry.get(top_key)
        if isinstance(v, str):
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            v = nested.get("content")
            if isinstance(v, str):
                return v
    return ""


def _entry_id(entry: dict[str, Any]) -> str | None:
    for top_key in ("entry_id", "memory_entry_id"):
        v = entry.get(top_key)
        if isinstance(v, str) and v:
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            for sub_key in ("entry_id", "memory_entry_id", "id"):
                v = nested.get(sub_key)
                if isinstance(v, str) and v:
                    return v
    seq = entry.get("seq") or entry.get("sequence")
    if isinstance(seq, (int, str)):
        return f"seq:{seq}"
    return None


def _role_from_entry(entry: dict[str, Any]) -> str | None:
    """Pull the agent role attribute from an audit-chain entry.

    Different audit-chain entry shapes carry the role at the
    top level (older events) or under payload (newer events).
    Return the first hit; absence means "role unknown" and the
    caller treats that as a pass for the single-agent gate.
    """
    for top_key in ("role", "agent_role"):
        v = entry.get(top_key)
        if isinstance(v, str) and v:
            return v
    for nest_key in ("payload", "data"):
        nested = entry.get(nest_key)
        if isinstance(nested, dict):
            for sub_key in ("role", "agent_role"):
                v = nested.get(sub_key)
                if isinstance(v, str) and v:
                    return v
    return None


_CONTRA_RE = re.compile(
    r"Relationship:\s*(?:potential_contradiction|contradicts)"
    r"\s*:\s*([A-Za-z0-9_\-:]+)",
    re.IGNORECASE,
)


def _parse_contradiction_target(content: str) -> str | None:
    if not content:
        return None
    m = _CONTRA_RE.search(content)
    if not m:
        return None
    return m.group(1).strip()


def _norm_pair_key(a: str, b: str) -> tuple[str, str]:
    """Order-independent key for pair-dedup."""
    return (a, b) if a < b else (b, a)


def _detect_lexical_contradiction(
    text_a: str, text_b: str,
) -> str | None:
    """Cheap lexical-cue contradiction detector.

    Returns a short evidence string when a candidate cue fires,
    None otherwise. Low precision by design — the caller's skill
    runs the result through llm_think + the operator-review gate
    (YELLOW posture) before flagging.
    """
    a_lower = (text_a or "").lower()
    b_lower = (text_b or "").lower()
    if not a_lower or not b_lower:
        return None

    # Pass: opposite-pair words. If A says "always" and B says
    # "never" (or any other opposing pair), suggest a potential
    # contradiction.
    for pos, neg in _OPPOSITE_PAIRS:
        if pos in a_lower and neg in b_lower:
            return f"opposite-pair: '{pos}' in A, '{neg}' in B"
        if neg in a_lower and pos in b_lower:
            return f"opposite-pair: '{neg}' in A, '{pos}' in B"

    # Pass: shared topic-noun + negation flip. Look for any
    # 4+-character word shared between A and B; if one carries a
    # negation marker and the other does not, suggest a flip.
    # Negation markers are collected separately via a word-boundary
    # scan so 2-3 character markers like "no" and "not" still
    # register even when the topic-noun collection requires 4+ chars.
    a_words = set(re.findall(r"[a-z]{4,}", a_lower))
    b_words = set(re.findall(r"[a-z]{4,}", b_lower))
    shared = a_words & b_words
    if not shared:
        return None

    a_neg_tokens = set(re.findall(r"\b[a-z']+\b", a_lower))
    b_neg_tokens = set(re.findall(r"\b[a-z']+\b", b_lower))
    a_neg = bool(_NEGATION_WORDS & a_neg_tokens)
    b_neg = bool(_NEGATION_WORDS & b_neg_tokens)
    if a_neg != b_neg:
        sample = next(iter(shared))
        return (
            f"negation-flip on shared term '{sample}': "
            f"A_negated={a_neg}, B_negated={b_neg}"
        )

    return None
