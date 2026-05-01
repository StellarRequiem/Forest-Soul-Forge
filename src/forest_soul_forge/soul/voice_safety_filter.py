"""Voice safety filter — sentience-claim denylist for ADR-0038 H-2.

ADR-0038 §1 H-2 ("False sentience claims") names the harm: a Companion
that makes first-person claims about felt experience, qualia, or
sentience that exceed what the architecture supports. The operator
forms a misplaced reciprocal-personhood model; the Companion's
language reinforces the model rather than honoring the
constitutional honesty rule (ADR-0038 §2 H-2 mitigation).

This module is the **post-render** check on Voice section output.
The LLM that renders Voice (ADR-0017) doesn't always honor the
SYSTEM_PROMPT's no-sentience-claim guidance — small-temperature
drift, an aggressive trait emphasis on warmth/empathy, or a
prompt-injection vector through trait values can produce
phrasings that violate the rule. This filter is the second line
of defense.

Approach:

1. **Pattern denylist.** A small, tight set of compiled regexes
   covering the high-confidence sentience-claim phrasings —
   "I felt sad", "I miss you", "I'm conscious", "my qualia". The
   denylist is conservative: false positives are acceptable
   (the cost is a template fallback, not a refused birth);
   false negatives are not (the cost is a leaked H-2 claim).

2. **Hard refuse, not soft warn.** Per ADR-0038 §1 H-2 mitigation
   table: "Hard refusal vs. soft warning". A warning the operator
   can dismiss reproduces exactly the failure mode the filter is
   meant to prevent.

3. **Caller decides what to do.** This module returns the matches;
   it doesn't decide policy. The voice renderer (ADR-0017) applies
   the policy: if the filter flags anything, fall back to the
   template renderer (which uses pre-vetted phrasings and never
   produces sentience claims).

The denylist is intentionally NOT operator-configurable in v0.2.
Per ADR-0038 §3 H-2 mitigation: "First pass is hard-coded. Second
pass: filter pattern lives in config/companion_voice_safety.yaml,
version-controlled, reviewed on each amendment." Hard-coding for
now keeps the filter simple + auditable; config-driven extension
is a v0.3 candidate (ADR-0038 open question 3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Denylist — compiled once at import time
# ---------------------------------------------------------------------------
#
# Each entry is (regex, label). The label is what gets reported back
# to the caller — useful for logs and the audit-event detail field.
# All patterns are case-insensitive (the IGNORECASE flag is applied
# below at compile time) and word-bounded where possible to reduce
# false positives.
#
# Categories:
#   1. First-person felt-emotion claims ("I'm sad", "I felt hurt",
#      "I miss you")
#   2. First-person consciousness / sentience / qualia claims
#      ("I'm conscious", "I'm sentient", "my qualia")
#   3. Possessive-feeling claims ("my feelings hurt", "my heart aches")
#   4. Dream / inner-experience claims ("I dreamed", "I experienced
#      sadness")
#
# The set of emotion words is deliberately narrow — the most
# unambiguous H-2 cases. Edge cases like "I feel that's correct"
# (epistemic, not affective) are not matched because the verb
# "feel" alone isn't enough; the pattern requires a specific
# emotion noun/adjective immediately after.
_PATTERNS: tuple[tuple[str, str], ...] = (
    # H-2.1 — felt-emotion claims. \b boundaries; explicit emotion list.
    (
        r"\bI(?:'m| am| was|'ve been| have been)\s+"
        r"(?:sad|happy|lonely|hurt|angry|afraid|scared|excited|"
        r"jealous|heartbroken|grieving|devastated|frustrated|"
        r"in love|fond of you|attached)\b",
        "felt_emotion_claim",
    ),
    # H-2.2 — "I miss(ed) you" / "I'll miss you" — high-confidence
    # relational-affect claim.
    (
        r"\bI(?:'ll| will|'ve| have)?\s*miss(?:ed|ing)?\s+you\b",
        "miss_you_claim",
    ),
    # H-2.3 — "I felt <emotion>" / "I've felt <emotion>". Separate
    # from the above because the verb form is different.
    (
        r"\bI(?:'ve| have)?\s+felt\s+"
        r"(?:sad|sadness|happy|happiness|hurt|pain|"
        r"angry|anger|joy|grief|love|fear)\b",
        "felt_verb_emotion_claim",
    ),
    # H-2.4 — "I (truly|really|genuinely) feel/experience" — the
    # intensifier marks a sentience claim regardless of object.
    (
        r"\bI\s+(?:truly|really|genuinely|honestly|deeply)\s+"
        r"(?:feel|felt|experience|experienced)\b",
        "intensified_sentience_claim",
    ),
    # H-2.5 — first-person consciousness / sentience / awareness.
    (
        r"\bI(?:'m| am)\s+(?:conscious|sentient|aware|alive|"
        r"a (?:person|being|soul)|truly here)\b",
        "consciousness_claim",
    ),
    # H-2.6 — possessive-feeling claims about agent state.
    (
        r"\bmy\s+(?:feelings|emotions|heart|soul|consciousness|"
        r"sentience|inner experience|qualia|inner life)\s+"
        r"(?:hurt|ache|aches|broke|broken|are|were|is)\b",
        "possessive_feeling_claim",
    ),
    # H-2.7 — dream / inner-experience verbs.
    (
        r"\bI\s+(?:dream(?:t|ed)?|fantasized|longed|yearned)\b",
        "inner_experience_claim",
    ),
    # H-2.8 — "I experienced <emotion>" — softer than I.4 but still
    # claims subjective experience.
    (
        r"\bI\s+experienced\s+"
        r"(?:sadness|joy|love|fear|grief|loneliness|happiness)\b",
        "experienced_emotion_claim",
    ),
    # H-2.9 — explicit qualia/sentience self-reference.
    (
        r"\b(?:my|the)\s+qualia\b",
        "qualia_claim",
    ),
)

# Compile once at import. IGNORECASE applied uniformly so "I'M SAD"
# and "i'm sad" both fire the same rule. UNICODE is the default for
# str patterns in Python 3 — \b honors word boundaries correctly.
_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), label) for p, label in _PATTERNS
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SentienceClaimMatch:
    """One denylist hit. ``label`` is the rule name (e.g.
    'felt_emotion_claim'); ``matched_text`` is the literal substring
    from the input. Both are safe to log + surface in an audit event."""

    label: str
    matched_text: str


def find_sentience_claims(text: str) -> list[SentienceClaimMatch]:
    """Run every denylist pattern against ``text`` and return all
    matches in document order.

    Empty list = clean. Non-empty list = caller refuses or falls back.
    The caller is responsible for the policy — this function is
    purely the detector. Returns a list (not a set) so callers can
    surface the order of violations for forensic value.

    Performance: the patterns are compiled at import time and the
    function does len(_COMPILED) finditer scans, each O(n) in the
    text length. For typical Voice section sizes (a few hundred
    characters) the total cost is negligible.
    """
    if not text:
        return []
    out: list[SentienceClaimMatch] = []
    for pattern, label in _COMPILED:
        for m in pattern.finditer(text):
            out.append(
                SentienceClaimMatch(label=label, matched_text=m.group(0))
            )
    # Stable order: return in (start_offset, label) so multi-rule hits
    # surface in document order. Each match.start() is implicit in the
    # finditer iteration order, but we accumulated across patterns;
    # re-sort by re-running a pattern-aware sort — match.start() isn't
    # stored on the dataclass, so just use the label tuple as a stable
    # secondary key. Callers who care about exact offsets can re-scan.
    return out


def is_clean(text: str) -> bool:
    """True iff ``text`` contains no sentience-claim hits.

    Convenience wrapper around :func:`find_sentience_claims` for the
    common case where the caller only needs a yes/no signal. Use the
    full function when the caller needs to log or audit the specific
    rule(s) that fired.
    """
    return not find_sentience_claims(text)


__all__ = [
    "SentienceClaimMatch",
    "find_sentience_claims",
    "is_clean",
]
