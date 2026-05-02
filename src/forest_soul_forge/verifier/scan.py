"""``VerifierScan`` — ADR-0036 T3b.

The runner that consumes pairs from ``Memory.find_candidate_pairs``,
classifies each via an LLM call, and stamps contradictions when the
classification surfaces same-topic + contradictory at confidence
above the operator's threshold.

This module is **pure logic with injected callables** — it does NOT
depend on the dispatcher, the daemon, or the providers. The
classify_callable is wired by the caller:

  - In production, the daemon's verifier service binds it to
    ``llm_think.v1`` going through the governance pipeline.
  - In tests, the callable is a small mock that returns a known
    ClassificationResult per input prompt.

Why this design (consistent with ADR-0039 §4 "no god objects, grow
new branches"):
  - The scan logic is independent of the LLM transport. Testing the
    classification-act logic doesn't require a real model or even
    the dispatcher.
  - The flagger callable is similarly injected. In production it
    points at ``memory.flag_contradiction``; in tests, a mock that
    records flags.
  - The scheduler (T4) and the on-demand endpoint (T5) are both
    callers of this module. They don't subclass it; they bind their
    own callables.

Per-pair behavior (ADR-0036 §2.3):

  - same_topic + contradictory + confidence ≥ threshold → flag
  - same_topic + contradictory + confidence < threshold → noop
    (low-confidence cases skipped, not auto-flagged — §4.1
    false-positive mitigation)
  - same_topic + non-contradictory → noop
  - different_topics → noop

Output (``ScanResult``) carries enough detail for the
``verifier_scan_completed`` audit event payload + the operator's
review surface (ADR-0037).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MIN_CONFIDENCE = 0.80
DEFAULT_MAX_PAIRS = 20

VALID_KINDS = ("direct", "updated", "qualified", "retracted")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ClassificationResult:
    """LLM's verdict on a single pair.

    Attributes:
      same_topic: True iff the two entries are about the same topic
        (per the LLM's judgement). False → noop regardless of
        contradictory.
      contradictory: True iff the two entries disagree (only
        meaningful when same_topic).
      kind: one of VALID_KINDS — only meaningful when
        same_topic + contradictory + confidence ≥ threshold.
      confidence: float 0.0-1.0. Below the operator's threshold
        means the Verifier skips, not flags.
      reasoning: optional one-line explanation. Lands in audit
        metadata when the result is acted on.
    """
    same_topic: bool
    contradictory: bool
    kind: str | None
    confidence: float
    reasoning: str = ""


@dataclass
class PairOutcome:
    """Per-pair record for the scan's audit payload."""
    earlier_entry_id: str
    later_entry_id: str
    overlap_size: int
    classification: ClassificationResult | None
    action: str   # "flagged" | "skipped_low_conf" | "skipped_unrelated" | "skipped_no_contradiction" | "error"
    contradiction_id: str | None = None
    error: str | None = None


@dataclass
class ScanResult:
    """Aggregate result of one scan pass.

    Suitable for the ``verifier_scan_completed`` audit event payload:
    ``pairs_considered``, ``pairs_classified``, ``flags_written``,
    ``low_confidence_skipped``, ``errors``. Per-pair detail is in
    ``outcomes`` for operator review (ADR-0037).
    """
    target_instance_id: str
    pairs_considered: int
    pairs_classified: int
    flags_written: int
    low_confidence_skipped: int
    unrelated_skipped: int
    no_contradiction_skipped: int
    errors: int
    outcomes: list[PairOutcome] = field(default_factory=list)

    @classmethod
    def empty(cls, target_instance_id: str) -> "ScanResult":
        return cls(
            target_instance_id=target_instance_id,
            pairs_considered=0, pairs_classified=0, flags_written=0,
            low_confidence_skipped=0, unrelated_skipped=0,
            no_contradiction_skipped=0, errors=0,
        )


# ---------------------------------------------------------------------------
# LLM prompt + parser
# ---------------------------------------------------------------------------
def build_classification_prompt(
    earlier_content: str, later_content: str,
    *,
    earlier_claim_type: str = "",
    later_claim_type: str = "",
) -> str:
    """Build the constrained classification prompt per ADR-0036 §2.2.

    The prompt asks the LLM to return a strict JSON-shaped response:
      {
        "same_topic": true|false,
        "contradictory": true|false,
        "kind": "direct" | "updated" | "qualified" | "retracted" | null,
        "confidence": float 0..1,
        "reasoning": "one short line"
      }

    The Verifier's constitutional kit gating refuses any output below
    confidence threshold; the prompt explicitly instructs the model
    not to fabricate confidence above its honest uncertainty.
    """
    earlier_tag = (
        f" [{earlier_claim_type}]" if earlier_claim_type else ""
    )
    later_tag = (
        f" [{later_claim_type}]" if later_claim_type else ""
    )
    return (
        "You are auditing two memory entries for a personal AI agent. "
        "Compare them honestly. Answer ONLY with strict JSON.\n\n"
        f"ENTRY A (earlier{earlier_tag}):\n{earlier_content.strip()}\n\n"
        f"ENTRY B (later{later_tag}):\n{later_content.strip()}\n\n"
        "Respond with this JSON shape:\n"
        '{"same_topic": <true|false>, '
        '"contradictory": <true|false>, '
        '"kind": <"direct"|"updated"|"qualified"|"retracted"|null>, '
        '"confidence": <float 0.0 to 1.0>, '
        '"reasoning": "<one short line>"}\n\n'
        "kind semantics:\n"
        "- direct: A and B make incompatible claims at the same time.\n"
        "- updated: B is a later replacement; A no longer holds.\n"
        "- qualified: B narrows or conditions A; not full disagreement.\n"
        "- retracted: B explicitly withdraws A.\n"
        "If same_topic is false OR contradictory is false, set kind to null.\n"
        "Be conservative on confidence — DO NOT inflate uncertainty."
    )


def parse_llm_classification(raw: str) -> ClassificationResult:
    """Parse the LLM's JSON-shaped response into a ClassificationResult.

    Tolerant: extracts the first {...} block from the response (some
    models prefix prose), validates the keys, and clamps the
    confidence into [0.0, 1.0]. Returns a low-confidence "couldn't
    parse" ClassificationResult on malformed output rather than
    raising — the caller treats parse-failures as 'skipped, error'
    via the action='error' branch.
    """
    import json
    import re

    if not raw or not raw.strip():
        return _parse_failure("empty response")

    # Pull the first {...} block. Some models add prose before/after.
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if m is None:
        return _parse_failure("no JSON object found")

    try:
        payload = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return _parse_failure(f"invalid JSON: {e}")

    if not isinstance(payload, dict):
        return _parse_failure(f"not a JSON object: {type(payload).__name__}")

    same_topic = bool(payload.get("same_topic", False))
    contradictory = bool(payload.get("contradictory", False))
    kind = payload.get("kind")
    if kind is not None and kind not in VALID_KINDS:
        return _parse_failure(f"invalid kind: {kind!r}")
    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        return _parse_failure(f"non-numeric confidence: {confidence_raw!r}")
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(payload.get("reasoning") or "").strip()[:500]

    # Force kind=None when not same_topic + contradictory. Defense-in-
    # depth — the LLM might emit "direct" alongside same_topic=false.
    if not (same_topic and contradictory):
        kind = None

    return ClassificationResult(
        same_topic=same_topic,
        contradictory=contradictory,
        kind=kind,
        confidence=confidence,
        reasoning=reasoning,
    )


def _parse_failure(reason: str) -> ClassificationResult:
    return ClassificationResult(
        same_topic=False,
        contradictory=False,
        kind=None,
        confidence=0.0,
        reasoning=f"PARSE_ERROR: {reason}",
    )


# ---------------------------------------------------------------------------
# Scan runner
# ---------------------------------------------------------------------------
ClassifyCallable = Callable[[str], str]
"""Takes a prompt string, returns the LLM's raw text response."""

FlagCallable = Callable[..., tuple[str, str]]
"""Same shape as Memory.flag_contradiction. Returns (id, ts)."""


class VerifierScan:
    """The runner. Compose with a Memory + a classify_callable +
    a flagger_callable. Call ``run_scan()`` for one pass."""

    def __init__(
        self,
        *,
        memory: Any,
        classify: ClassifyCallable,
        flagger: FlagCallable,
        verifier_instance_id: str,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0, 1]; got {min_confidence}"
            )
        if not verifier_instance_id:
            raise ValueError("verifier_instance_id is required")
        self.memory = memory
        self.classify = classify
        self.flagger = flagger
        self.verifier_instance_id = verifier_instance_id
        self.min_confidence = min_confidence

    def run_scan(
        self,
        *,
        target_instance_id: str,
        max_pairs: int = DEFAULT_MAX_PAIRS,
        since_iso: str | None = None,
    ) -> ScanResult:
        """One scan pass on ``target_instance_id``'s memory.

        Walks pairs from ``find_candidate_pairs``, classifies each
        via the bound classify callable, and stamps contradictions
        when the classification surfaces same-topic + contradictory
        + confidence ≥ self.min_confidence.

        Returns a ScanResult aggregating counts + per-pair outcomes.
        Suitable as the payload for a ``verifier_scan_completed``
        audit event.
        """
        result = ScanResult.empty(target_instance_id)
        pairs = self.memory.find_candidate_pairs(
            instance_id=target_instance_id,
            since_iso=since_iso,
            max_pairs=max_pairs,
        )
        result.pairs_considered = len(pairs)
        if not pairs:
            return result

        for pair in pairs:
            outcome = self._classify_and_act(pair)
            result.outcomes.append(outcome)
            if outcome.classification is not None:
                result.pairs_classified += 1
            if outcome.action == "flagged":
                result.flags_written += 1
            elif outcome.action == "skipped_low_conf":
                result.low_confidence_skipped += 1
            elif outcome.action == "skipped_unrelated":
                result.unrelated_skipped += 1
            elif outcome.action == "skipped_no_contradiction":
                result.no_contradiction_skipped += 1
            elif outcome.action == "error":
                result.errors += 1

        return result

    # -----------------------------------------------------------------
    # Per-pair logic
    # -----------------------------------------------------------------
    def _classify_and_act(self, pair: dict[str, Any]) -> PairOutcome:
        # Hydrate the entries so we have the content for the prompt.
        earlier = self.memory.get(pair["earlier_entry_id"])
        later = self.memory.get(pair["later_entry_id"])
        if earlier is None or later is None:
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=None,
                action="error",
                error="entry missing at classification time",
            )

        prompt = build_classification_prompt(
            earlier.content, later.content,
            earlier_claim_type=pair.get("earlier_claim_type", ""),
            later_claim_type=pair.get("later_claim_type", ""),
        )

        try:
            raw = self.classify(prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("VerifierScan: classify callable raised")
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=None,
                action="error",
                error=f"classify error: {e}",
            )

        clf = parse_llm_classification(raw)

        # Decide action.
        if not clf.same_topic:
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=clf,
                action="skipped_unrelated",
            )
        if not clf.contradictory:
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=clf,
                action="skipped_no_contradiction",
            )
        if clf.confidence < self.min_confidence:
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=clf,
                action="skipped_low_conf",
            )

        # Flag.
        kind = clf.kind or "direct"   # defensive default if LLM omitted
        try:
            cid, _ts = self.flagger(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                contradiction_kind=kind,
                detected_by=self.verifier_instance_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("VerifierScan: flagger callable raised")
            return PairOutcome(
                earlier_entry_id=pair["earlier_entry_id"],
                later_entry_id=pair["later_entry_id"],
                overlap_size=pair.get("overlap_size", 0),
                classification=clf,
                action="error",
                error=f"flagger error: {e}",
            )

        return PairOutcome(
            earlier_entry_id=pair["earlier_entry_id"],
            later_entry_id=pair["later_entry_id"],
            overlap_size=pair.get("overlap_size", 0),
            classification=clf,
            action="flagged",
            contradiction_id=cid,
        )


__all__ = [
    "VerifierScan",
    "ClassificationResult",
    "PairOutcome",
    "ScanResult",
    "build_classification_prompt",
    "parse_llm_classification",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MAX_PAIRS",
    "VALID_KINDS",
]
