"""``voice_match_check.v1`` — ADR-0088 Phase B voice matcher.

Scores a candidate draft against a previously-built voice profile
(produced by ``voice_profile_build.v1``). Returns a structured
report with per-feature deltas, a composite match score, and
span-pointer flags for the operator + style_steward to review.

Read-only. Deterministic — same draft + same profile always
produce the same report. LLM-driven matching would be opaque +
unrepeatable; this tool computes stylometric deltas directly so
the operator can audit + replay the comparison.

## What the report contains

For each profile feature, a delta + a flag:

- **delta** — abs(draft_value − profile_value) / max(profile_value, 1)
  (relative). Lower = closer match.
- **flag** — categorical: "match" / "drift_minor" / "drift_major"
  driven by feature-specific thresholds.

Plus a **composite_score** (0..1, 1.0 = perfect match) derived
from a weighted aggregate of the per-feature deltas, and a
**flagged_features** list naming which features tripped
drift_major.

## When to use

The ``editing.v1`` skill (D7 Phase C) dispatches this before
emitting an "approved" verdict. The style_steward agent
(D7 Phase B) also dispatches it directly as part of its
voice-drift audit pass.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

# Reuse the regexes + helper sets from voice_profile_build for
# consistency. Importing live (not re-declaring) so any future
# tightening of the regex propagates atomically.
from forest_soul_forge.tools.builtin.voice_profile_build import (
    _WORD_RE, _SENTENCE_RE, _PARAGRAPH_RE,
    _HEDGING_TERMS, _FIRST_PERSON, _FUNCTION_WORDS,
    _stdev,
)


_MAX_DRAFT_CHARS = 200_000
_MIN_DRAFT_WORDS = 50

# Per-feature thresholds for drift_major. Drift_minor is anything
# in (0, drift_major). delta == 0 is "match".
_DRIFT_MAJOR_THRESHOLDS = {
    "mean_sentence_length": 0.40,
    "stdev_sentence_length": 0.60,
    "mean_word_length": 0.20,
    "type_token_ratio": 0.30,
    "avg_paragraph_length": 0.60,
    "comma_per_sentence": 0.50,
    "semicolon_per_1k": 1.50,    # rare punctuation; allow more swing
    "emdash_per_1k": 1.50,       # rare punctuation; allow more swing
    "hedging_per_1k": 0.80,
    "first_person_per_1k": 0.80,
}
# Composite score weights — sum must be 1.0. Hedging + first-person
# carry less weight because they're style choices that the writer
# legitimately tunes per format; sentence-length stats + word-length
# + TTR carry more weight because they're the deeper voice fingerprint.
_FEATURE_WEIGHTS = {
    "mean_sentence_length": 0.20,
    "stdev_sentence_length": 0.10,
    "mean_word_length": 0.15,
    "type_token_ratio": 0.15,
    "avg_paragraph_length": 0.10,
    "comma_per_sentence": 0.10,
    "semicolon_per_1k": 0.05,
    "emdash_per_1k": 0.05,
    "hedging_per_1k": 0.05,
    "first_person_per_1k": 0.05,
}


class VoiceMatchCheckTool:
    """Score a draft against a voice profile + flag drift.

    Args:
      draft (str, required): the candidate text to score.
        50..200,000 chars.
      profile (dict, required): a voice profile of the shape
        produced by ``voice_profile_build.v1``. Must carry the
        numeric feature keys; ``top_function_words`` and metadata
        keys are tolerated but not required for scoring.

    Output:
      {
        "generated_at":      str (ISO),
        "composite_score":   float (0..1, 1.0 = perfect),
        "verdict":           str ("match"/"drift_minor"/"drift_major"),
        "per_feature":       {
          "<feature>": {
            "draft_value":     float,
            "profile_value":   float,
            "delta":           float,
            "flag":            str,
            "weight":          float,
          }, ...
        },
        "flagged_features":  [str, ...],   # features flagged drift_major
        "spans": [                          # operator-visible drift pointers
          {"feature": str, "excerpt": str, "note": str}, ...
        ],
        "draft_word_count":  int,
      }
    """

    name = "voice_match_check"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        draft = args.get("draft")
        if not isinstance(draft, str) or not draft.strip():
            raise ToolValidationError(
                "draft must be a non-empty string"
            )
        if len(draft) > _MAX_DRAFT_CHARS:
            raise ToolValidationError(
                f"draft must be <= {_MAX_DRAFT_CHARS} chars; "
                f"got {len(draft)}"
            )

        profile = args.get("profile")
        if not isinstance(profile, dict):
            raise ToolValidationError("profile must be a dict")
        for key in _FEATURE_WEIGHTS.keys():
            if key not in profile:
                raise ToolValidationError(
                    f"profile is missing required feature '{key}'. "
                    f"Pass the dict returned by voice_profile_build.v1."
                )
            if not isinstance(profile[key], (int, float)):
                raise ToolValidationError(
                    f"profile['{key}'] must be a number"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        draft: str = args["draft"]
        profile: dict[str, Any] = args["profile"]

        draft_features = _compute_features(draft)
        draft_word_count = draft_features.pop("__total_words")
        if draft_word_count < _MIN_DRAFT_WORDS:
            raise ToolValidationError(
                f"draft word count must be >= {_MIN_DRAFT_WORDS}; "
                f"got {draft_word_count}. Comparison would be noisy."
            )

        per_feature: dict[str, dict[str, Any]] = {}
        weighted_match_total = 0.0
        flagged: list[str] = []
        for feature, weight in _FEATURE_WEIGHTS.items():
            d = float(draft_features[feature])
            p = float(profile[feature])
            delta = abs(d - p) / max(p, 1.0)
            threshold = _DRIFT_MAJOR_THRESHOLDS[feature]
            if delta < 1e-9:
                flag = "match"
            elif delta >= threshold:
                flag = "drift_major"
                flagged.append(feature)
            else:
                flag = "drift_minor"
            # Per-feature match component: 1.0 at delta=0; decays
            # toward 0 as delta approaches threshold. Clamped.
            match_component = max(0.0, 1.0 - (delta / threshold))
            weighted_match_total += weight * match_component
            per_feature[feature] = {
                "draft_value": round(d, 4),
                "profile_value": round(p, 4),
                "delta": round(delta, 4),
                "flag": flag,
                "weight": weight,
            }

        composite_score = round(weighted_match_total, 4)
        if not flagged and composite_score >= 0.85:
            verdict = "match"
        elif flagged:
            verdict = "drift_major"
        else:
            verdict = "drift_minor"

        spans = _spans_for_flags(draft, flagged, draft_features, profile)

        body = {
            "generated_at": _now_iso(),
            "composite_score": composite_score,
            "verdict": verdict,
            "per_feature": per_feature,
            "flagged_features": flagged,
            "spans": spans,
            "draft_word_count": draft_word_count,
        }

        return ToolResult(
            output=body,
            metadata={
                "verdict": verdict,
                "composite_score": composite_score,
                "flagged_count": len(flagged),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"voice match verdict={verdict}; "
                f"score={composite_score}; "
                f"flagged_features={len(flagged)}"
            ),
        )


def _compute_features(text: str) -> dict[str, Any]:
    """Compute the same feature set voice_profile_build.v1 uses,
    over a single text. Returns a dict including a sentinel
    ``__total_words`` so the caller can validate length.
    """
    paragraphs = [p for p in _PARAGRAPH_RE.split(text) if p.strip()]
    total_paragraphs = len(paragraphs)

    comma_count = text.count(",")
    semicolon_count = text.count(";")
    emdash_count = text.count("—") + text.count("--")

    sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()]
    sentence_lengths: list[int] = []
    for sent in sentences:
        toks = _WORD_RE.findall(sent)
        if toks:
            sentence_lengths.append(len(toks))

    tokens = _WORD_RE.findall(text)
    word_lengths = [len(t) for t in tokens]
    all_lower = [t.lower() for t in tokens]
    total_words = len(all_lower)

    hedging_count = sum(1 for t in all_lower if t in _HEDGING_TERMS)
    first_person_count = sum(1 for t in all_lower if t in _FIRST_PERSON)

    mean_sentence_length = (
        sum(sentence_lengths) / len(sentence_lengths)
        if sentence_lengths else 0.0
    )
    stdev_sentence_length = _stdev(sentence_lengths)
    mean_word_length = (
        sum(word_lengths) / len(word_lengths)
        if word_lengths else 0.0
    )
    type_token_ratio = (
        len(set(all_lower)) / total_words if total_words else 0.0
    )
    avg_paragraph_length = (
        len(sentences) / total_paragraphs if total_paragraphs else 0.0
    )
    comma_per_sentence = (
        comma_count / len(sentence_lengths)
        if sentence_lengths else 0.0
    )
    per_1k = lambda n: (n * 1000.0 / total_words) if total_words else 0.0  # noqa: E731

    return {
        "mean_sentence_length": mean_sentence_length,
        "stdev_sentence_length": stdev_sentence_length,
        "mean_word_length": mean_word_length,
        "type_token_ratio": type_token_ratio,
        "avg_paragraph_length": avg_paragraph_length,
        "comma_per_sentence": comma_per_sentence,
        "semicolon_per_1k": per_1k(semicolon_count),
        "emdash_per_1k": per_1k(emdash_count),
        "hedging_per_1k": per_1k(hedging_count),
        "first_person_per_1k": per_1k(first_person_count),
        "__total_words": total_words,
    }


def _spans_for_flags(
    draft: str,
    flagged: list[str],
    draft_features: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, str]]:
    """Surface up to three small excerpts the operator can read to
    see WHERE the drift shows up. Each excerpt is the first
    matching sentence for the feature class — kept short and
    non-overlapping. This is the operator-visible "show me what
    you mean" pointer the value-prop calls for.
    """
    spans: list[dict[str, str]] = []
    if not flagged:
        return spans
    sentences = [s.strip() for s in _SENTENCE_RE.split(draft) if s.strip()]
    if not sentences:
        return spans

    for feature in flagged[:3]:  # cap at 3 to keep the report tight
        excerpt = ""
        note = ""
        if feature == "mean_sentence_length":
            # longest sentence as the most-visible outlier
            longest = max(sentences, key=lambda s: len(s.split()))
            excerpt = _truncate(longest, 200)
            note = "longest sentence — voice profile favors a different rhythm"
        elif feature == "semicolon_per_1k":
            hit = next((s for s in sentences if ";" in s), "")
            excerpt = _truncate(hit, 200) if hit else ""
            if not hit:
                # under-use signal
                note = "no semicolons in draft; profile uses them"
            else:
                note = "semicolon usage diverges from profile"
        elif feature == "emdash_per_1k":
            hit = next(
                (s for s in sentences if "—" in s or "--" in s), "",
            )
            excerpt = _truncate(hit, 200) if hit else ""
            note = (
                "em-dash usage diverges from profile"
                if hit else "no em-dashes in draft; profile uses them"
            )
        elif feature == "hedging_per_1k":
            hit = next(
                (s for s in sentences
                 if any(h in s.lower() for h in _HEDGING_TERMS)),
                "",
            )
            excerpt = _truncate(hit, 200) if hit else ""
            note = (
                "hedging cadence diverges from profile"
                if hit else "no hedging in draft; profile uses it"
            )
        elif feature == "first_person_per_1k":
            hit = next(
                (s for s in sentences
                 if any(f" {p} " in f" {s.lower()} "
                        for p in _FIRST_PERSON)),
                "",
            )
            excerpt = _truncate(hit, 200) if hit else ""
            note = (
                "first-person voice-distance diverges from profile"
                if hit else "no first-person in draft; profile uses it"
            )
        elif feature == "comma_per_sentence":
            heavy = max(sentences, key=lambda s: s.count(","))
            excerpt = _truncate(heavy, 200)
            note = "comma rhythm diverges from profile"
        else:
            # generic fallback — first sentence as a sample
            excerpt = _truncate(sentences[0], 200)
            note = f"{feature} diverges from profile"
        if excerpt:
            spans.append({
                "feature": feature,
                "excerpt": excerpt,
                "note": note,
            })
    return spans


def _truncate(text: str, n: int) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )
