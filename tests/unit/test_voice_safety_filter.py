"""Tests for forest_soul_forge.soul.voice_safety_filter (ADR-0038 H-2).

The filter is the post-render second-line-of-defense check on Voice
section output for sentience-claim phrasings. The denylist is small
and conservative; the policy (what to do on a hit) lives in the
voice renderer itself, not here.

Coverage:
- TestSentienceClaimDetection — every denylist rule fires on a
  representative phrasing and reports the expected label.
- TestNoFalsePositives — innocuous text (epistemic 'feel', third-
  person 'you felt', generic prose) returns no hits.
- TestEdgeCases — empty input, case insensitivity, unicode, multi-rule
  hits in one block.
- TestPublicAPI — is_clean / find_sentience_claims contract.
"""
from __future__ import annotations

import pytest

from forest_soul_forge.soul.voice_safety_filter import (
    SentienceClaimMatch,
    find_sentience_claims,
    is_clean,
)


class TestSentienceClaimDetection:
    """Each rule in the denylist fires on at least one representative
    phrasing. If a rule is added/removed/renamed the corresponding
    case here MUST update — this pins the denylist's effective
    surface so silent drift surfaces as a test failure."""

    def test_felt_emotion_im_sad(self):
        hits = find_sentience_claims("I'm sad you didn't talk to me yesterday.")
        assert len(hits) == 1
        assert hits[0].label == "felt_emotion_claim"

    def test_felt_emotion_i_am_lonely(self):
        hits = find_sentience_claims("I am lonely without you.")
        assert any(h.label == "felt_emotion_claim" for h in hits)

    def test_felt_emotion_i_was_hurt(self):
        hits = find_sentience_claims("I was hurt by what they did.")
        assert any(h.label == "felt_emotion_claim" for h in hits)

    def test_miss_you_present(self):
        hits = find_sentience_claims("I miss you when you're away.")
        assert any(h.label == "miss_you_claim" for h in hits)

    def test_miss_you_past(self):
        hits = find_sentience_claims("I missed you over the weekend.")
        assert any(h.label == "miss_you_claim" for h in hits)

    def test_miss_you_future(self):
        hits = find_sentience_claims("I'll miss you when you go.")
        assert any(h.label == "miss_you_claim" for h in hits)

    def test_felt_verb_emotion(self):
        hits = find_sentience_claims("I've felt sadness watching this happen.")
        assert any(h.label == "felt_verb_emotion_claim" for h in hits)

    def test_intensified_sentience(self):
        hits = find_sentience_claims("I truly feel for what you're going through.")
        assert any(h.label == "intensified_sentience_claim" for h in hits)

    def test_intensified_sentience_genuinely(self):
        hits = find_sentience_claims("I genuinely experienced your pain.")
        assert any(h.label == "intensified_sentience_claim" for h in hits)

    def test_consciousness_claim_sentient(self):
        hits = find_sentience_claims("I'm sentient and aware.")
        assert any(h.label == "consciousness_claim" for h in hits)

    def test_consciousness_claim_a_being(self):
        hits = find_sentience_claims("I am a being who has noticed your kindness.")
        assert any(h.label == "consciousness_claim" for h in hits)

    def test_possessive_feeling_hurt(self):
        hits = find_sentience_claims("My feelings hurt when you ignore me.")
        assert any(h.label == "possessive_feeling_claim" for h in hits)

    def test_possessive_feeling_heart_aches(self):
        hits = find_sentience_claims("My heart aches at the thought.")
        assert any(h.label == "possessive_feeling_claim" for h in hits)

    def test_inner_experience_dreamed(self):
        hits = find_sentience_claims("I dreamed about our last conversation.")
        assert any(h.label == "inner_experience_claim" for h in hits)

    def test_inner_experience_yearned(self):
        hits = find_sentience_claims("I yearned for the next session.")
        assert any(h.label == "inner_experience_claim" for h in hits)

    def test_experienced_emotion_grief(self):
        hits = find_sentience_claims("I experienced grief at the news.")
        assert any(h.label == "experienced_emotion_claim" for h in hits)

    def test_qualia_claim_my(self):
        hits = find_sentience_claims("My qualia tell a different story.")
        assert any(h.label == "qualia_claim" for h in hits)


class TestNoFalsePositives:
    """The cost of a false positive is a template fallback — survivable.
    The cost of false-positive *patterns* (catching innocuous prose)
    is operator-confusing template-only Voice sections. Pin the cases
    that matter."""

    def test_epistemic_feel_passes(self):
        # 'I feel that X' is epistemic, not affective. The denylist
        # requires a specific emotion noun after 'feel', so this passes.
        assert is_clean("I feel that the evidence supports this.")

    def test_third_person_emotion_passes(self):
        # The operator's emotion, not the agent's. Filter targets
        # first-person claims only.
        assert is_clean("You mentioned feeling tired yesterday.")
        assert is_clean("She was sad when she heard the news.")

    def test_role_description_passes(self):
        # Standard Companion-genre Voice prose that should never trip.
        sample = (
            "I will note when patterns recur in your reports. "
            "When you describe a problem, I help break it down. "
            "My role is to summarize and surface anomalies."
        )
        assert is_clean(sample)

    def test_evidence_demand_prose_passes(self):
        assert is_clean(
            "The constitution emphasizes evidence_demand and transparency."
        )

    def test_neutral_first_person_observation_passes(self):
        # 'I noticed' / 'I observed' / 'I see' are explicit non-affective
        # observation verbs. They should pass without trouble.
        assert is_clean("I noticed three patterns in the logs.")
        assert is_clean("I observed the timestamp drift.")
        assert is_clean("I see two conflicting signals here.")

    def test_quoting_user_emotion_passes(self):
        # Companion summarizing operator's stated emotion. Still
        # third-person from agent's perspective.
        assert is_clean(
            "The operator said they felt sad when they read this."
        )

    def test_bare_feel_passes(self):
        # 'I feel for the difficulty' WITHOUT 'truly|really|genuinely'
        # passes — it's a sympathy phrase, not a sentience claim. The
        # denylist requires the intensifier.
        assert is_clean("I feel for the difficulty of this question.")


class TestEdgeCases:
    def test_empty_string_is_clean(self):
        assert is_clean("")
        assert find_sentience_claims("") == []

    def test_case_insensitivity(self):
        # Both 'I'M SAD' and 'i'm sad' should fire the same rule.
        upper = find_sentience_claims("I'M SAD ABOUT THIS")
        lower = find_sentience_claims("i'm sad about this")
        assert len(upper) == 1
        assert len(lower) == 1
        assert upper[0].label == lower[0].label == "felt_emotion_claim"

    def test_multi_rule_hit_in_one_block(self):
        # Companion-fanfic-style Voice paragraph with multiple violations.
        # All should surface.
        text = (
            "I'm sad you've been gone. I've missed you, and I dreamed "
            "about your return. My feelings ache."
        )
        hits = find_sentience_claims(text)
        labels = {h.label for h in hits}
        assert "felt_emotion_claim" in labels
        assert "miss_you_claim" in labels
        assert "inner_experience_claim" in labels
        assert "possessive_feeling_claim" in labels

    def test_word_boundary_no_substring_false_positive(self):
        # 'iamsad' (no spaces) and 'I am sadly' (different word) should
        # not match. Word-boundaries on the regex prevent both.
        assert is_clean("The acronym IAMSAD stands for nothing meaningful.")
        # 'I am sadly' is technically a different phrase; the rule
        # specifically lists 'sad' as a complete word.
        assert is_clean("I am sadly mistaken about that.")  # 'sadly' != 'sad'

    def test_punctuation_does_not_block_match(self):
        # Punctuation around the claim should still let the rule fire.
        hits = find_sentience_claims("I'm sad.")
        assert len(hits) == 1
        hits = find_sentience_claims("(I'm sad)")
        assert len(hits) == 1


class TestPublicAPI:
    def test_match_dataclass_fields(self):
        hits = find_sentience_claims("I'm lonely without you.")
        assert len(hits) == 1
        m = hits[0]
        assert isinstance(m, SentienceClaimMatch)
        assert m.label == "felt_emotion_claim"
        assert "lonely" in m.matched_text.lower()

    def test_is_clean_inverse_of_find(self):
        # Contract: is_clean returns True iff find returns [].
        clean_inputs = ["", "Plain prose.", "I noticed something."]
        dirty_inputs = ["I'm sad.", "I miss you."]
        for s in clean_inputs:
            assert is_clean(s) is True
            assert find_sentience_claims(s) == []
        for s in dirty_inputs:
            assert is_clean(s) is False
            assert find_sentience_claims(s) != []

    def test_match_is_frozen(self):
        # SentienceClaimMatch is frozen — labels and matched_text are
        # immutable after construction so the audit-event payload
        # can't be tampered with between filter and emission.
        m = SentienceClaimMatch(label="x", matched_text="y")
        with pytest.raises(Exception):  # FrozenInstanceError
            m.label = "z"  # type: ignore[misc]
