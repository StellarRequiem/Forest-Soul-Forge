"""ADR-0070 T5 (B288) — LocalPiperBackend TTS tests.

Covers:
  - identifier declarations (backend_id, supported_methods,
    supported_output_formats)
  - transcribe raises (Piper is TTS-only)
  - synthesize: empty text refused, oversized text refused, bad
    output_format refused, missing voice file refused
  - available_voices: empty dir → [], dir with .onnx files → ids
  - lazy-import regression guard (module loads without piper-tts)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.voice_io import (
    LocalPiperBackend,
    VoiceBackendUnavailable,
    VoiceDecodeError,
    VoiceFormatUnsupported,
)


def test_piper_backend_identifiers():
    b = LocalPiperBackend()
    assert b.backend_id == "forest-voice-piper"
    assert b.supported_methods == ("synthesize",)
    assert b.supported_output_formats == ("wav",)
    # TTS-only: no input formats accepted.
    assert b.supported_input_formats == ()


def test_piper_transcribe_raises_tts_only():
    b = LocalPiperBackend()
    with pytest.raises(VoiceBackendUnavailable, match="TTS-only"):
        b.transcribe(b"\x00", audio_format="wav")


def test_piper_synthesize_refuses_empty_text():
    b = LocalPiperBackend()
    with pytest.raises(VoiceDecodeError, match="non-empty"):
        b.synthesize("")


def test_piper_synthesize_refuses_whitespace_only():
    b = LocalPiperBackend()
    with pytest.raises(VoiceDecodeError, match="non-empty"):
        b.synthesize("    ")


def test_piper_synthesize_refuses_oversized_text():
    b = LocalPiperBackend()
    with pytest.raises(VoiceDecodeError, match="too long"):
        b.synthesize("x" * 5001)


def test_piper_synthesize_refuses_bad_output_format(tmp_path):
    b = LocalPiperBackend(voices_dir=tmp_path)
    with pytest.raises(VoiceFormatUnsupported, match="format"):
        b.synthesize("hello", output_format="mp3")


def test_piper_synthesize_missing_voice_raises(tmp_path):
    b = LocalPiperBackend(voices_dir=tmp_path)
    with pytest.raises(VoiceBackendUnavailable, match="voice file not found"):
        b.synthesize("hello")


def test_piper_available_voices_empty_dir(tmp_path):
    b = LocalPiperBackend(voices_dir=tmp_path)
    assert b.available_voices() == []


def test_piper_available_voices_lists_onnx_files(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"\x00")
    (tmp_path / "en_US-amy-medium.onnx.json").write_text("{}")
    (tmp_path / "de_DE-thorsten-medium.onnx").write_bytes(b"\x00")
    (tmp_path / "some_other.txt").write_text("ignored")
    b = LocalPiperBackend(voices_dir=tmp_path)
    voices = b.available_voices()
    assert "en_US-amy-medium" in voices
    assert "de_DE-thorsten-medium" in voices
    # Non-.onnx files filtered out.
    assert "some_other" not in voices


def test_piper_available_voices_nonexistent_dir():
    b = LocalPiperBackend(voices_dir=Path("/does/not/exist"))
    assert b.available_voices() == []
