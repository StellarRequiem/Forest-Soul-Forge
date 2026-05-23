"""``voice_profile_build.v1`` — ADR-0088 Phase B voice profiler.

Derives a deterministic stylometric voice profile from a list of
operator writing samples. Returns a JSON-able profile with
sentence-rhythm + vocabulary + structural features the
``voice_match_check.v1`` sibling tool can score drafts against.

Read-only. No LLM in the tool itself — the
``voice_profile_build.v1`` skill wraps this with operator-profile
context + memory_write of the final attestation, but the profile
derivation ITSELF is deterministic so the operator can audit +
replay it. LLM-driven voice modeling would be opaque +
unrepeatable; deterministic stylometry with explicit features is
what the value-prop calls for.

## Features captured (per profile)

- **mean_sentence_length** (float, words): average words/sentence
- **stdev_sentence_length** (float, words): spread; high = varied,
  low = consistent rhythm
- **mean_word_length** (float, chars): vocabulary heft
- **type_token_ratio** (float 0..1): vocabulary diversity
- **avg_paragraph_length** (float, sentences): structural cadence
- **comma_per_sentence** (float): punctuation rhythm
- **semicolon_per_1k** (float): a strong style fingerprint
- **emdash_per_1k** (float): another strong fingerprint
- **hedging_per_1k** (float): "maybe", "perhaps", "could", "might"
  density — operator-confidence cadence
- **first_person_per_1k** (float): "I", "me", "my", "we", "us"
  density — voice-distance fingerprint
- **top_function_words** (list[(str, count)]): top 20 function
  words by frequency — distinctive vocabulary signature
- **sample_count** (int): number of samples ingested
- **total_words** (int): total word count across samples
"""
from __future__ import annotations

import re
import math
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_SAMPLES = 50
_MAX_SAMPLE_CHARS = 100_000
_MIN_TOTAL_WORDS = 50  # floor — below this the profile is unreliable
_TOP_FUNCTION_WORDS_K = 20

_HEDGING_TERMS = {
    "maybe", "perhaps", "possibly", "could", "might", "seems",
    "somewhat", "kind", "sort", "roughly", "approximately",
    "presumably", "arguably", "potentially", "tentatively",
}
_FIRST_PERSON = {"i", "me", "my", "mine", "we", "us", "our", "ours"}
# Function words = high-frequency, low-semantic-load tokens. These
# are the strongest stylometric fingerprint per the Mosteller +
# Wallace literature (Federalist Papers authorship). We don't
# attempt to model them all here — just count occurrences of a
# stable set and report the top-K by raw count.
_FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on",
    "at", "by", "for", "with", "as", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "this", "that", "these", "those", "it", "its", "if",
    "then", "than", "so", "because", "while", "when", "where",
    "which", "who", "whom", "what", "how", "not", "no", "yes",
    "all", "some", "any", "each", "every", "both", "neither",
}

_WORD_RE = re.compile(r"\b[\w']+\b")
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


class VoiceProfileBuildTool:
    """Build a deterministic stylometric voice profile.

    Args:
      samples (list[str], required): operator writing samples.
        At least one sample; max 50 samples. Each sample must
        be a string; max 100,000 chars per sample.
      profile_label (str, optional): operator-readable label for
        this profile (e.g. "blog_2024_2025"). Recorded as
        metadata only — does not affect derivation.

    Output:
      {
        "generated_at":           str (ISO Pacific),
        "profile_label":          str,
        "sample_count":           int,
        "total_words":            int,
        "mean_sentence_length":   float,
        "stdev_sentence_length":  float,
        "mean_word_length":       float,
        "type_token_ratio":       float,
        "avg_paragraph_length":   float,
        "comma_per_sentence":     float,
        "semicolon_per_1k":       float,
        "emdash_per_1k":          float,
        "hedging_per_1k":         float,
        "first_person_per_1k":    float,
        "top_function_words":     [[str, int], ...],  # length <= 20
      }
    """

    name = "voice_profile_build"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        samples = args.get("samples")
        if not isinstance(samples, list):
            raise ToolValidationError("samples must be a list")
        if not samples:
            raise ToolValidationError(
                "samples must contain at least one entry"
            )
        if len(samples) > _MAX_SAMPLES:
            raise ToolValidationError(
                f"samples count must be <= {_MAX_SAMPLES}; "
                f"got {len(samples)}"
            )
        for i, s in enumerate(samples):
            if not isinstance(s, str):
                raise ToolValidationError(
                    f"samples[{i}] must be a string; "
                    f"got {type(s).__name__}"
                )
            if len(s) > _MAX_SAMPLE_CHARS:
                raise ToolValidationError(
                    f"samples[{i}] must be <= {_MAX_SAMPLE_CHARS} "
                    f"chars; got {len(s)}"
                )
        label = args.get("profile_label")
        if label is not None and not isinstance(label, str):
            raise ToolValidationError(
                "profile_label must be a string when provided"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        samples: list[str] = args["samples"]
        label = args.get("profile_label") or ""

        # Sentence + word stats across all samples
        sentence_lengths: list[int] = []
        word_lengths: list[int] = []
        all_words_lower: list[str] = []
        total_paragraphs = 0
        total_sentences_from_paragraphs = 0
        comma_count = 0
        semicolon_count = 0
        emdash_count = 0
        hedging_count = 0
        first_person_count = 0
        function_word_counts: dict[str, int] = {}

        for sample in samples:
            # paragraphs
            paragraphs = [
                p for p in _PARAGRAPH_RE.split(sample) if p.strip()
            ]
            total_paragraphs += len(paragraphs)

            # punctuation
            comma_count += sample.count(",")
            semicolon_count += sample.count(";")
            # both em-dash and double-hyphen variants
            emdash_count += sample.count("—") + sample.count("--")

            # sentences
            sentences = [
                s for s in _SENTENCE_RE.split(sample) if s.strip()
            ]
            total_sentences_from_paragraphs += len(sentences)
            for sent in sentences:
                tokens = _WORD_RE.findall(sent)
                if tokens:
                    sentence_lengths.append(len(tokens))

            # word-level features
            tokens = _WORD_RE.findall(sample)
            for tok in tokens:
                tok_l = tok.lower()
                all_words_lower.append(tok_l)
                word_lengths.append(len(tok))
                if tok_l in _HEDGING_TERMS:
                    hedging_count += 1
                if tok_l in _FIRST_PERSON:
                    first_person_count += 1
                if tok_l in _FUNCTION_WORDS:
                    function_word_counts[tok_l] = (
                        function_word_counts.get(tok_l, 0) + 1
                    )

        total_words = len(all_words_lower)
        if total_words < _MIN_TOTAL_WORDS:
            raise ToolValidationError(
                f"total words across samples must be >= "
                f"{_MIN_TOTAL_WORDS}; got {total_words}. "
                f"Provide longer writing samples."
            )

        mean_sentence_length = (
            sum(sentence_lengths) / len(sentence_lengths)
            if sentence_lengths else 0.0
        )
        stdev_sentence_length = _stdev(sentence_lengths)
        mean_word_length = (
            sum(word_lengths) / len(word_lengths)
            if word_lengths else 0.0
        )
        # type-token ratio — distinct lowercased tokens / total
        type_token_ratio = (
            len(set(all_words_lower)) / total_words
            if total_words else 0.0
        )
        avg_paragraph_length = (
            total_sentences_from_paragraphs / total_paragraphs
            if total_paragraphs else 0.0
        )
        comma_per_sentence = (
            comma_count / len(sentence_lengths)
            if sentence_lengths else 0.0
        )
        per_1k = lambda n: (n * 1000.0 / total_words) if total_words else 0.0  # noqa: E731
        semicolon_per_1k = per_1k(semicolon_count)
        emdash_per_1k = per_1k(emdash_count)
        hedging_per_1k = per_1k(hedging_count)
        first_person_per_1k = per_1k(first_person_count)

        # top-K function words by frequency, stable order on tie
        top_fw_pairs = sorted(
            function_word_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )[:_TOP_FUNCTION_WORDS_K]

        body = {
            "generated_at": _now_iso(),
            "profile_label": label,
            "sample_count": len(samples),
            "total_words": total_words,
            "mean_sentence_length": round(mean_sentence_length, 3),
            "stdev_sentence_length": round(stdev_sentence_length, 3),
            "mean_word_length": round(mean_word_length, 3),
            "type_token_ratio": round(type_token_ratio, 4),
            "avg_paragraph_length": round(avg_paragraph_length, 3),
            "comma_per_sentence": round(comma_per_sentence, 3),
            "semicolon_per_1k": round(semicolon_per_1k, 3),
            "emdash_per_1k": round(emdash_per_1k, 3),
            "hedging_per_1k": round(hedging_per_1k, 3),
            "first_person_per_1k": round(first_person_per_1k, 3),
            "top_function_words": [
                [w, c] for w, c in top_fw_pairs
            ],
        }

        return ToolResult(
            output=body,
            metadata={
                "sample_count": len(samples),
                "total_words": total_words,
                "profile_label": label,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"built voice profile from {len(samples)} sample(s); "
                f"{total_words} total words"
            ),
        )


def _stdev(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )
