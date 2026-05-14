"""``/voice/*`` — ADR-0070 T2 (B287) voice I/O HTTP surface.

Two endpoints in T2:

  - **POST /voice/transcribe** — multipart upload of audio bytes,
    routes to the configured ASR backend (default
    LocalWhisperBackend), audit-chains a voice_transcribed event,
    returns the VoiceTranscript as JSON.

  - **GET /voice/status** — backend health: which backend is
    configured, model file present?, recent transcription counts.

T5 adds /voice/synthesize. T4 adds wake-word streaming endpoints.

## Why audit-chain transcripts

Per ADR-0070 D3, every voice→intent transcription is an audit
chain entry. The raw transcript text is the operator's words; it
goes through the same tamper-evident substrate as every other
operator action. Encryption-at-rest (ADR-0050 T3) covers the
event_data when enabled.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, UploadFile, status,
)

from fastapi.responses import Response

from forest_soul_forge.core.voice_io import (
    LocalPiperBackend,
    LocalWhisperBackend,
    SUPPORTED_AUDIO_FORMATS,
    VoiceBackendUnavailable,
    VoiceDecodeError,
    VoiceFormatUnsupported,
    VoiceIOError,
    VoiceTimeoutError,
    VoiceTranscript,
)
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    require_api_token,
    require_writes_enabled,
)


router = APIRouter(prefix="/voice", tags=["voice"])


# Process-cached backend. Constructed on first call; subsequent
# calls reuse so the whisper-cpp model stays warm in memory.
_BACKEND_CACHE: dict[str, Any] = {}


def _get_backend() -> LocalWhisperBackend:
    """Lazy backend construction. Reuse across requests so the
    whisper-cpp model stays warm. Operator-pluggable in future
    tranches via voice_io plugins; T2 ships the canonical local
    backend wired in directly."""
    backend = _BACKEND_CACHE.get("default")
    if backend is None:
        backend = LocalWhisperBackend()
        _BACKEND_CACHE["default"] = backend
    return backend


def _get_tts_backend() -> LocalPiperBackend:
    """Lazy TTS backend construction. Reuse across requests so the
    Piper voice cache stays warm. T5 ships the canonical local
    backend; future tranches wire operator-pluggable backends."""
    backend = _BACKEND_CACHE.get("tts_default")
    if backend is None:
        backend = LocalPiperBackend()
        _BACKEND_CACHE["tts_default"] = backend
    return backend


def _transcript_to_dict(t: VoiceTranscript) -> dict[str, Any]:
    return {
        "text":          t.text,
        "language":      t.language,
        "duration_s":    t.duration_s,
        "confidence":    t.confidence,
        "backend_id":    t.backend_id,
        "model_id":      t.model_id,
        "elapsed_ms":    t.elapsed_ms,
        "segments":      list(t.segments) if t.segments else None,
    }


@router.post(
    "/transcribe",
    dependencies=[
        Depends(require_api_token),
        Depends(require_writes_enabled),
    ],
)
async def transcribe(
    audio: UploadFile = File(...),
    audio_format: str = Form(...),
    language_hint: Optional[str] = Form(None),
    timeout_s: float = Form(60.0),
    audit=Depends(get_audit_chain),
):
    """ASR endpoint. Returns the transcript + audit-chains a
    voice_transcribed event.

    Form fields (multipart):
      audio: the audio file (any of the supported formats)
      audio_format: one of wav / mp3 / flac / ogg / m4a / webm
      language_hint: optional ISO 639-1 (e.g. "en") — bypasses
        automatic language detection for faster inference.
      timeout_s: per-call wall-clock budget. Default 60s.

    Returns: VoiceTranscript JSON (text + language + duration +
    confidence + backend_id + model_id + elapsed_ms + segments).
    """
    if audio_format not in SUPPORTED_AUDIO_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported audio_format {audio_format!r}; "
                f"supported: {sorted(SUPPORTED_AUDIO_FORMATS)}"
            ),
        )

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="audio file is empty",
        )

    backend = _get_backend()
    try:
        transcript = backend.transcribe(
            audio_bytes,
            audio_format=audio_format,
            language_hint=language_hint,
            timeout_s=timeout_s,
        )
    except VoiceBackendUnavailable as e:
        _emit_voice_failed(audit, "backend_unavailable", str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except VoiceFormatUnsupported as e:
        _emit_voice_failed(audit, "format_unsupported", str(e))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(e),
        )
    except VoiceTimeoutError as e:
        _emit_voice_failed(audit, "timeout", str(e))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        )
    except VoiceDecodeError as e:
        _emit_voice_failed(audit, "decode_error", str(e))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except VoiceIOError as e:  # catch-all family
        _emit_voice_failed(audit, "voice_io_error", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    # Emit voice_transcribed audit event. Raw transcript text in
    # event_data — encryption-at-rest (ADR-0050 T3) covers it.
    try:
        audit.append(
            "voice_transcribed",
            {
                "backend_id":  transcript.backend_id,
                "model_id":    transcript.model_id,
                "language":    transcript.language,
                "duration_s":  transcript.duration_s,
                "confidence":  transcript.confidence,
                "elapsed_ms":  transcript.elapsed_ms,
                "text":        transcript.text,
                "audio_format": audio_format,
                "audio_bytes_len": len(audio_bytes),
            },
            agent_dna=None,  # operator-initiated, not agent-attributed
        )
    except Exception:
        # Audit failure is non-fatal — the transcript is already
        # in memory and returning it is more useful than crashing.
        pass

    return _transcript_to_dict(transcript)


@router.post(
    "/synthesize",
    dependencies=[
        Depends(require_api_token),
        Depends(require_writes_enabled),
    ],
)
async def synthesize(
    text: str = Form(...),
    voice_id: Optional[str] = Form(None),
    output_format: str = Form("wav"),
    timeout_s: float = Form(60.0),
    audit=Depends(get_audit_chain),
):
    """TTS endpoint. Returns WAV audio bytes + audit-chains a
    voice_synthesized event.

    Form fields:
      text: utterance to speak. 1-5000 chars.
      voice_id: which Piper voice (e.g. 'en_US-amy-medium'). None
        uses the backend's configured default.
      output_format: only 'wav' supported in T5.
      timeout_s: per-call wall-clock budget. Default 60s.

    Returns: raw audio bytes (Content-Type: audio/wav).
    """
    if not text or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text is required and must be non-empty",
        )

    backend = _get_tts_backend()
    import time as _time
    t0 = _time.perf_counter()
    try:
        audio_bytes = backend.synthesize(
            text,
            voice_id=voice_id,
            output_format=output_format,
            timeout_s=timeout_s,
        )
    except VoiceBackendUnavailable as e:
        _emit_voice_failed(audit, "tts_backend_unavailable", str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    except VoiceFormatUnsupported as e:
        _emit_voice_failed(audit, "tts_format_unsupported", str(e))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(e),
        )
    except VoiceDecodeError as e:
        _emit_voice_failed(audit, "tts_decode_error", str(e))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except VoiceIOError as e:
        _emit_voice_failed(audit, "tts_voice_io_error", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    elapsed_ms = int((_time.perf_counter() - t0) * 1000)

    # Emit voice_synthesized audit event. We log the text since
    # operator-asked-agent-to-speak is the same privacy surface as
    # operator-spoke-into-mic. Encryption-at-rest covers it.
    try:
        audit.append(
            "voice_synthesized",
            {
                "backend_id":     backend.backend_id,
                "voice_id":       voice_id or backend.default_voice_id,
                "text":           text,
                "audio_bytes_len": len(audio_bytes),
                "output_format":  output_format,
                "elapsed_ms":     elapsed_ms,
            },
            agent_dna=None,
        )
    except Exception:
        pass

    return Response(
        content=audio_bytes,
        media_type=f"audio/{output_format}",
        headers={
            "X-Voice-Backend-Id": backend.backend_id,
            "X-Voice-Voice-Id":   voice_id or backend.default_voice_id,
            "X-Voice-Elapsed-Ms": str(elapsed_ms),
        },
    )


@router.get(
    "/status",
    dependencies=[Depends(require_api_token)],
)
async def voice_status(audit=Depends(get_audit_chain)):
    """Backend health check.

    Returns: configured backend id + model_path + model_present? +
    24h transcription counts.
    """
    backend = _get_backend()
    model_path = getattr(backend, "model_path", None)
    model_present = bool(model_path and model_path.exists())

    # TTS backend status (T5 / B288).
    tts_backend = _get_tts_backend()
    tts_voices = tts_backend.available_voices()

    # Last 24h voice activity from the audit chain.
    transcribed_24h, synthesized_24h, failed_24h = _count_voice_events_24h(audit)

    return {
        "schema_version":  1,
        "asr": {
            "backend_id":      backend.backend_id,
            "model_id":        getattr(backend, "model_id", "unknown"),
            "model_path":      str(model_path) if model_path else None,
            "model_present":   model_present,
            "supported_input_formats": list(backend.supported_input_formats),
            "supported_methods":       list(backend.supported_methods),
        },
        "tts": {
            "backend_id":      tts_backend.backend_id,
            "voices_dir":      str(tts_backend.voices_dir),
            "default_voice_id": tts_backend.default_voice_id,
            "available_voices": tts_voices,
            "supported_methods": list(tts_backend.supported_methods),
            "supported_output_formats": list(tts_backend.supported_output_formats),
        },
        "activity_24h": {
            "transcribed": transcribed_24h,
            "synthesized": synthesized_24h,
            "failed":      failed_24h,
        },
    }


def _emit_voice_failed(audit, reason_code: str, detail: str) -> None:
    """Audit the failure path. Non-fatal: if audit append errors,
    the HTTP error still surfaces."""
    try:
        audit.append(
            "voice_failed",
            {"reason_code": reason_code, "detail": detail},
            agent_dna=None,
        )
    except Exception:
        pass


def _count_voice_events_24h(audit) -> tuple[int, int, int]:
    """Returns (transcribed, synthesized, failed) counts for last 24h."""
    try:
        entries = audit.tail(5000)
    except Exception:
        return 0, 0, 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    transcribed = 0
    synthesized = 0
    failed = 0
    for e in entries:
        et = getattr(e, "event_type", "")
        if et not in (
            "voice_transcribed", "voice_synthesized", "voice_failed",
        ):
            continue
        ts_str = getattr(e, "timestamp", None)
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        if et == "voice_transcribed":
            transcribed += 1
        elif et == "voice_synthesized":
            synthesized += 1
        else:
            failed += 1
    return transcribed, synthesized, failed
