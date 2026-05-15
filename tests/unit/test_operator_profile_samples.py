"""ADR-0068 T5 (B315) — voice + writing samples tests.

Same shape as the trust-circle tests:
  - Dataclass surface
  - Round-trip preservation
  - YAML omits when empty / per-entry optional fields when None
  - Loader refuses malformed entries
  - NO Reality Anchor seeds (these are operational pointers,
    not assertion-grade facts)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    OperatorProfileError,
    VoiceSample,
    WorkHours,
    WritingSample,
    load_operator_profile,
    profile_to_ground_truth_seeds,
    save_operator_profile,
)


def _base_profile(**overrides) -> OperatorProfile:
    defaults = dict(
        schema_version=1,
        operator_id="op_1",
        name="Alex Price",
        preferred_name="Alex",
        email="alex@example.com",
        timezone="America/Los_Angeles",
        locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return OperatorProfile(**defaults)


# ---------------------------------------------------------------------------
# Dataclass surface
# ---------------------------------------------------------------------------

def test_voice_sample_required_fields_only():
    s = VoiceSample(phrase="Mira", audio_path="mira.wav")
    assert s.phrase == "Mira"
    assert s.audio_path == "mira.wav"
    assert s.notes is None


def test_voice_sample_optional_notes():
    s = VoiceSample(
        phrase="Soul Forge", audio_path="forge.wav",
        notes="emphasize Forge",
    )
    assert s.notes == "emphasize Forge"


def test_writing_sample_required_fields_only():
    s = WritingSample(title="post", file_path="post.md")
    assert s.title == "post"
    assert s.file_path == "post.md"
    assert s.channel is None
    assert s.notes is None


def test_writing_sample_optional_channel_and_notes():
    s = WritingSample(
        title="t", file_path="f.md",
        channel="blog", notes="long-form",
    )
    assert s.channel == "blog"
    assert s.notes == "long-form"


def test_profile_defaults_both_sample_lists_empty():
    profile = _base_profile()
    assert profile.voice_samples == ()
    assert profile.writing_samples == ()


# ---------------------------------------------------------------------------
# Round-trip + YAML shape
# ---------------------------------------------------------------------------

def test_voice_samples_roundtrip(tmp_path):
    profile = _base_profile(voice_samples=(
        VoiceSample(phrase="Mira", audio_path="mira.wav"),
        VoiceSample(
            phrase="Soul Forge", audio_path="forge.wav",
            notes="emphasize Forge",
        ),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    reloaded = load_operator_profile(path)
    assert len(reloaded.voice_samples) == 2
    assert reloaded.voice_samples[0].notes is None
    assert reloaded.voice_samples[1].notes == "emphasize Forge"


def test_writing_samples_roundtrip(tmp_path):
    profile = _base_profile(writing_samples=(
        WritingSample(title="Q3 plan", file_path="plan.md", channel="email"),
        WritingSample(
            title="thread reply", file_path="reply.txt",
            channel="slack", notes="terse, dry humor",
        ),
    ))
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    reloaded = load_operator_profile(path)
    assert len(reloaded.writing_samples) == 2
    assert reloaded.writing_samples[0].channel == "email"
    assert reloaded.writing_samples[0].notes is None
    assert reloaded.writing_samples[1].notes == "terse, dry humor"


def test_yaml_omits_empty_sample_lists(tmp_path):
    """Empty lists shouldn't surface in the YAML — keeps the
    pre-T5 profile shape identical for operators who haven't
    recorded any samples."""
    profile = _base_profile()
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    text = path.read_text(encoding="utf-8")
    assert "voice_samples" not in text
    assert "writing_samples" not in text


def test_yaml_omits_per_entry_optional_fields(tmp_path):
    profile = _base_profile(
        voice_samples=(VoiceSample(phrase="A", audio_path="a.wav"),),
        writing_samples=(WritingSample(title="B", file_path="b.md"),),
    )
    path = tmp_path / "profile.yaml"
    save_operator_profile(profile, path)
    raw = yaml.safe_load(path.read_text())
    v = raw["operator"]["voice_samples"][0]
    w = raw["operator"]["writing_samples"][0]
    assert "notes" not in v
    assert "channel" not in w
    assert "notes" not in w


# ---------------------------------------------------------------------------
# Loader refusals
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, key: str, value):
    """Write a minimal profile YAML overriding one sample-list key."""
    raw = {
        "schema_version": 1,
        "operator": {
            "operator_id": "op_1",
            "name": "X",
            "preferred_name": "X",
            "email": "x@y.com",
            "timezone": "UTC",
            "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
            key: value,
        },
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


@pytest.mark.parametrize("list_key", ["voice_samples", "writing_samples"])
def test_loader_refuses_non_list(tmp_path, list_key):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, list_key, "not_a_list")
    with pytest.raises(OperatorProfileError, match="must be a list"):
        load_operator_profile(p)


@pytest.mark.parametrize("list_key", ["voice_samples", "writing_samples"])
def test_loader_refuses_non_dict_entry(tmp_path, list_key):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, list_key, ["just-a-string"])
    with pytest.raises(OperatorProfileError, match="must be a mapping"):
        load_operator_profile(p)


def test_loader_refuses_voice_sample_missing_phrase(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, "voice_samples", [{"audio_path": "x.wav"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'phrase'",
    ):
        load_operator_profile(p)


def test_loader_refuses_voice_sample_missing_audio_path(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, "voice_samples", [{"phrase": "X"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'audio_path'",
    ):
        load_operator_profile(p)


def test_loader_refuses_writing_sample_missing_title(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, "writing_samples", [{"file_path": "x.md"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'title'",
    ):
        load_operator_profile(p)


def test_loader_refuses_writing_sample_missing_file_path(tmp_path):
    p = tmp_path / "bad.yaml"
    _write_yaml(p, "writing_samples", [{"title": "X"}])
    with pytest.raises(
        OperatorProfileError, match="missing required field 'file_path'",
    ):
        load_operator_profile(p)


# ---------------------------------------------------------------------------
# No Reality Anchor seeds
# ---------------------------------------------------------------------------

def test_voice_writing_samples_do_not_generate_ra_seeds():
    """The samples are operational pointers (paths to files), not
    assertion-grade facts. The Reality Anchor would have nothing
    useful to pattern-match against."""
    profile = _base_profile(
        voice_samples=(VoiceSample(phrase="A", audio_path="a.wav"),),
        writing_samples=(WritingSample(title="B", file_path="b.md"),),
    )
    seeds = profile_to_ground_truth_seeds(profile)
    for seed in seeds:
        sid = seed["id"]
        assert "voice" not in sid.lower()
        assert "writing" not in sid.lower()
        assert "sample" not in sid.lower()
