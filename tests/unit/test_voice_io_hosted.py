"""ADR-0070 T6 (B328) — hosted ASR/TTS backend tests.

Coverage:
  OpenAIWhisperBackend:
    - backend_id / supported_methods / supported_input_formats
    - model_path is always None (no on-disk model)
    - transcribe refuses unsupported audio_format
    - transcribe refuses empty audio bytes
    - transcribe refuses when secret_fn is None
    - transcribe refuses when secret resolves to empty string
    - transcribe refuses when secret_fn raises
    - missing 'openai' package surfaces VoiceBackendUnavailable
    - happy path (mocked OpenAI client): returns VoiceTranscript
    - API timeout maps to VoiceTimeoutError
    - other API error maps to VoiceIOError

  ElevenLabsTTSBackend:
    - synthesize raises VoiceBackendUnavailable (stub)
    - available_voices returns []

  resolve_hosted_asr_backend:
    - 'openai_whisper' / 'openai-whisper' / 'openai' all resolve
    - unknown backend id raises VoiceIOError
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from forest_soul_forge.core.voice_io import (
    VoiceBackendUnavailable,
    VoiceDecodeError,
    VoiceFormatUnsupported,
    VoiceIOError,
    VoiceTimeoutError,
)
from forest_soul_forge.core.voice_io_hosted import (
    ElevenLabsTTSBackend,
    OpenAIWhisperBackend,
    resolve_hosted_asr_backend,
)


# ---------------------------------------------------------------------------
# OpenAIWhisperBackend — metadata
# ---------------------------------------------------------------------------


def test_openai_backend_metadata():
    b = OpenAIWhisperBackend()
    assert b.backend_id == "openai-whisper"
    assert b.supported_methods == ("transcribe",)
    assert "wav" in b.supported_input_formats
    assert "webm" in b.supported_input_formats
    assert b.model_path is None  # hosted; no local model file
    assert b.model_id == "whisper-1"


# ---------------------------------------------------------------------------
# OpenAIWhisperBackend — error gates
# ---------------------------------------------------------------------------


def test_unsupported_format_refused():
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceFormatUnsupported, match="doesn't accept format"):
        b.transcribe(b"audio", audio_format="aiff",
                     secret_fn=lambda name: "stub")


def test_empty_audio_refused():
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceDecodeError, match="empty"):
        b.transcribe(b"", audio_format="wav",
                     secret_fn=lambda name: "stub")


def test_missing_secret_fn_refused(monkeypatch):
    # Stub out the OpenAI import so we get past _ensure_client.
    fake_openai = SimpleNamespace(OpenAI=lambda **kw: None)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceBackendUnavailable, match="secret_fn required"):
        b.transcribe(b"audio", audio_format="wav")


def test_empty_secret_refused(monkeypatch):
    fake_openai = SimpleNamespace(OpenAI=lambda **kw: None)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceBackendUnavailable, match="empty value"):
        b.transcribe(b"audio", audio_format="wav",
                     secret_fn=lambda name: "")


def test_secret_fn_raising_wrapped(monkeypatch):
    fake_openai = SimpleNamespace(OpenAI=lambda **kw: None)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    b = OpenAIWhisperBackend()
    def _bad(name):
        raise RuntimeError("keystore unreachable")
    with pytest.raises(VoiceBackendUnavailable, match="keystore unreachable"):
        b.transcribe(b"audio", audio_format="wav", secret_fn=_bad)


def test_missing_openai_package_surfaces_unavailable(monkeypatch):
    """When openai isn't installed, the lazy import fails → we
    raise VoiceBackendUnavailable (not ImportError leaking)."""
    # Force the import to fail.
    monkeypatch.setitem(sys.modules, "openai", None)
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceBackendUnavailable, match="openai package not installed"):
        b.transcribe(b"audio", audio_format="wav",
                     secret_fn=lambda name: "stub")


# ---------------------------------------------------------------------------
# OpenAIWhisperBackend — happy path + API error translation
# ---------------------------------------------------------------------------


def _install_fake_openai(monkeypatch, *, response, raise_exc=None):
    """Install a fake `openai` module whose OpenAI() returns a
    client whose .audio.transcriptions.create returns / raises
    what the test specifies."""
    client = MagicMock()
    if raise_exc is not None:
        client.audio.transcriptions.create.side_effect = raise_exc
    else:
        client.audio.transcriptions.create.return_value = response
    fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=client))
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    return fake_openai, client


def test_happy_path_returns_voice_transcript(monkeypatch):
    response = SimpleNamespace(
        text="hello world", language="en", duration=2.5,
    )
    _, _ = _install_fake_openai(monkeypatch, response=response)
    b = OpenAIWhisperBackend()
    transcript = b.transcribe(
        b"audio-bytes", audio_format="wav",
        secret_fn=lambda name: "sk-test",
    )
    assert transcript.text == "hello world"
    assert transcript.language == "en"
    assert transcript.duration_s == 2.5
    assert transcript.backend_id == "openai-whisper"
    assert transcript.model_id == "whisper-1"
    # elapsed_ms is non-negative real number.
    assert transcript.elapsed_ms >= 0


def test_happy_path_handles_missing_duration(monkeypatch):
    """Response without duration → transcript.duration_s defaults
    to 0.0 (no crash)."""
    response = SimpleNamespace(text="x")
    _install_fake_openai(monkeypatch, response=response)
    b = OpenAIWhisperBackend()
    t = b.transcribe(b"x", audio_format="wav",
                     secret_fn=lambda name: "sk")
    assert t.text == "x"
    assert t.duration_s == 0.0
    assert t.language is None


def test_timeout_error_maps_to_voice_timeout(monkeypatch):
    _install_fake_openai(
        monkeypatch, response=None,
        raise_exc=Exception("Request timed out after 60s"),
    )
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceTimeoutError, match="timeout"):
        b.transcribe(b"x", audio_format="wav",
                     secret_fn=lambda name: "sk")


def test_generic_api_error_maps_to_voice_io_error(monkeypatch):
    _install_fake_openai(
        monkeypatch, response=None,
        raise_exc=Exception("400 bad request"),
    )
    b = OpenAIWhisperBackend()
    with pytest.raises(VoiceIOError, match="API call failed"):
        b.transcribe(b"x", audio_format="wav",
                     secret_fn=lambda name: "sk")


# ---------------------------------------------------------------------------
# ElevenLabsTTSBackend — stub
# ---------------------------------------------------------------------------


def test_elevenlabs_synthesize_is_stub():
    b = ElevenLabsTTSBackend()
    with pytest.raises(VoiceBackendUnavailable, match="stub implementation"):
        b.synthesize("hello", secret_fn=lambda name: "stub")


def test_elevenlabs_available_voices_empty():
    assert ElevenLabsTTSBackend().available_voices() == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", [
    "openai_whisper", "openai-whisper", "openai",
    "OpenAI", "OPENAI-WHISPER",
])
def test_factory_openai_aliases(alias):
    b = resolve_hosted_asr_backend(alias)
    assert isinstance(b, OpenAIWhisperBackend)


def test_factory_propagates_model_id_and_secret_name():
    b = resolve_hosted_asr_backend(
        "openai", model_id="gpt-4o-transcribe",
        secret_name="my_custom_key",
    )
    assert b.model_id == "gpt-4o-transcribe"
    assert b.secret_name == "my_custom_key"


def test_factory_unknown_backend_raises():
    with pytest.raises(VoiceIOError, match="unknown hosted ASR backend"):
        resolve_hosted_asr_backend("anthropic-claude-voice")
