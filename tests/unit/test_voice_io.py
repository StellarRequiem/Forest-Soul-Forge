"""ADR-0070 T1 (B286) — voice I/O substrate tests.

Covers:
  - VoiceTranscript dataclass shape (frozen, expected fields)
  - VoiceIOError taxonomy
  - LocalWhisperBackend declares the right backend_id /
    supported_methods / supported_input_formats
  - LocalWhisperBackend raises VoiceFormatUnsupported on bad format
  - LocalWhisperBackend raises VoiceBackendUnavailable on missing
    model file
  - LocalWhisperBackend.synthesize raises (it's ASR-only)
  - audit_chain.py KNOWN_EVENT_TYPES includes the three voice events
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.voice_io import (
    SUPPORTED_AUDIO_FORMATS,
    LocalWhisperBackend,
    VoiceBackendUnavailable,
    VoiceDecodeError,
    VoiceFormatUnsupported,
    VoiceIOError,
    VoiceTimeoutError,
    VoiceTranscript,
)


# ---------------------------------------------------------------------------
# VoiceTranscript
# ---------------------------------------------------------------------------
def test_voice_transcript_is_frozen():
    """VoiceTranscript is a frozen dataclass — mutation should fail."""
    t = VoiceTranscript(
        text="hello", language="en", duration_s=1.2,
        confidence=0.85, backend_id="x", model_id="y",
        elapsed_ms=120,
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        t.text = "mutated"  # type: ignore[misc]


def test_voice_transcript_optional_segments():
    """Segments field defaults to None when backend doesn't emit them."""
    t = VoiceTranscript(
        text="x", language=None, duration_s=0.5,
        confidence=None, backend_id="b", model_id="m",
        elapsed_ms=50,
    )
    assert t.segments is None


# ---------------------------------------------------------------------------
# VoiceIOError taxonomy
# ---------------------------------------------------------------------------
def test_error_taxonomy_inheritance():
    """All voice errors subclass VoiceIOError so callers can catch
    the family without enumerating every type."""
    assert issubclass(VoiceBackendUnavailable, VoiceIOError)
    assert issubclass(VoiceFormatUnsupported, VoiceIOError)
    assert issubclass(VoiceTimeoutError, VoiceIOError)
    assert issubclass(VoiceDecodeError, VoiceIOError)


# ---------------------------------------------------------------------------
# LocalWhisperBackend
# ---------------------------------------------------------------------------
def test_local_whisper_backend_identifiers():
    backend = LocalWhisperBackend()
    assert backend.backend_id == "forest-voice-whisper-cpp"
    assert backend.supported_methods == ("transcribe",)
    # All declared formats are in the global supported set.
    for f in backend.supported_input_formats:
        assert f in SUPPORTED_AUDIO_FORMATS


def test_local_whisper_transcribe_rejects_bad_format(tmp_path):
    # Model path doesn't exist, but the format check runs first.
    backend = LocalWhisperBackend(model_path=tmp_path / "ggml.bin")
    with pytest.raises(VoiceFormatUnsupported, match="format"):
        backend.transcribe(b"", audio_format="bogus")


def test_local_whisper_transcribe_missing_model_raises(tmp_path):
    backend = LocalWhisperBackend(model_path=tmp_path / "does-not-exist.bin")
    with pytest.raises(VoiceBackendUnavailable, match="model not found"):
        backend.transcribe(b"\x00", audio_format="wav")


def test_local_whisper_synthesize_raises_not_supported():
    """LocalWhisperBackend is ASR-only. Calling synthesize raises."""
    backend = LocalWhisperBackend()
    with pytest.raises(VoiceBackendUnavailable, match="ASR-only"):
        backend.synthesize("hello world")


# ---------------------------------------------------------------------------
# Audit event types
# ---------------------------------------------------------------------------
def test_voice_event_types_in_known_set():
    """KNOWN_EVENT_TYPES must include voice_transcribed /
    voice_synthesized / voice_failed so the verifier accepts them."""
    for et in ("voice_transcribed", "voice_synthesized", "voice_failed"):
        assert et in KNOWN_EVENT_TYPES, (
            f"audit_chain.py KNOWN_EVENT_TYPES missing {et}"
        )


# ---------------------------------------------------------------------------
# Sanity — module imports without optional whisper-cpp dep
# ---------------------------------------------------------------------------
def test_module_imports_without_whisper_dep():
    """Importing the module must NOT pull whisper-cpp. The
    binding is lazy-loaded at first transcribe() call. Operators
    who don't enable voice don't pay the import cost."""
    # The module is already imported by this test file — if it
    # required whisper-cpp at import time, test collection would
    # have failed. This test exists as a regression guard against
    # accidentally moving the import to top-level.
    import forest_soul_forge.core.voice_io  # noqa
