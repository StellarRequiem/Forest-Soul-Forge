"""Voice I/O substrate — ADR-0070 T1 (B286).

Defines the plugin interface every voice backend implements.
Different operators want different backends (local Whisper-cpp,
faster-whisper, OpenAI Whisper API, ElevenLabs, Apple Speech,
Piper, macOS AVSpeechSynthesizer). The right pattern is plugin-
shape, not builtin-tool — same model ADR-0043 used for MCP
plugins.

## Surface

  - :class:`VoiceTranscript` — frozen ASR result
  - :class:`VoiceIOProtocol` — the interface every voice_io
    plugin implements
  - :class:`VoiceIOError` — taxonomy
  - :class:`LocalWhisperBackend` — canonical sovereign default
    (lazy-imports whisper-cpp so the module loads without the
    optional dep)

## Why local-first

Forest's ethos says every operator-facing AI service has a local
backend that ships in-box. Voice is sensitive — the transcript
IS the operator's words verbatim. Sending them to a hosted ASR
puts them on someone else's machine. Default = local.

Operators who want hosted backends (better accents, faster on
slow CPU, multilingual) opt in via separate plugins.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


# Supported audio container formats. Plugins declare which they
# accept; daemon converts via ffmpeg if a mismatch.
SUPPORTED_AUDIO_FORMATS = frozenset({"wav", "ogg", "flac", "mp3", "m4a", "webm"})


class VoiceIOError(RuntimeError):
    """Base class for voice I/O failures.

    Subclasses surface different failure modes so callers can
    decide retry policy:
      - :class:`VoiceBackendUnavailable` — model file missing,
        plugin process not running, etc. Operator-fixable.
      - :class:`VoiceFormatUnsupported` — audio container not
        accepted by this backend.
      - :class:`VoiceTimeoutError` — transcription exceeded the
        per-call wall-clock budget.
      - :class:`VoiceDecodeError` — audio bytes corrupted /
        malformed.
    """


class VoiceBackendUnavailable(VoiceIOError):
    """Backend isn't ready — model not loaded, plugin process
    not running, etc."""


class VoiceFormatUnsupported(VoiceIOError):
    """Audio container format isn't one this backend accepts."""


class VoiceTimeoutError(VoiceIOError):
    """Transcription exceeded the per-call wall-clock budget."""


class VoiceDecodeError(VoiceIOError):
    """Audio bytes can't be decoded by the backend."""


@dataclass(frozen=True)
class VoiceTranscript:
    """One ASR result.

    Frozen so callers can't mutate transcription data after the
    fact. Confidence semantics are backend-specific (Whisper's
    log-probability vs. e.g. cloud APIs' percentage); callers
    treating it as a comparable score should normalize first.

    The ``segments`` field lists per-segment timing for backends
    that produce sub-utterance timestamps. None when the backend
    doesn't emit segments.
    """
    text: str
    language: Optional[str]  # ISO 639-1 e.g. "en", or None if undetected
    duration_s: float  # audio duration in seconds (not wall-clock)
    confidence: Optional[float]  # backend-specific; None when unavailable
    backend_id: str  # plugin id that produced this transcript
    model_id: str  # backend-specific model identifier
    elapsed_ms: int  # wall-clock time the backend took
    segments: Optional[tuple[dict, ...]] = None  # per-segment timing


class VoiceIOProtocol(Protocol):
    """The interface every voice_io plugin implements.

    Plugins declare which methods they support via the
    ``supported_methods`` property — ASR-only plugins return
    ``("transcribe",)``, TTS-only return ``("synthesize",)``,
    full-duplex backends return both.
    """

    backend_id: str
    """Unique identifier matching the plugin manifest name."""

    supported_methods: tuple[str, ...]
    """Which RPC methods this plugin implements."""

    supported_input_formats: tuple[str, ...]
    """Audio container formats this backend accepts for ASR."""

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        audio_format: str,
        language_hint: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> VoiceTranscript:
        """ASR: audio bytes → VoiceTranscript.

        Raises VoiceBackendUnavailable if the model isn't loaded,
        VoiceFormatUnsupported if audio_format isn't in
        supported_input_formats, VoiceTimeoutError on timeout,
        VoiceDecodeError on malformed audio.
        """
        ...

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        output_format: str = "wav",
        timeout_s: float = 60.0,
    ) -> bytes:
        """TTS: text → audio bytes.

        voice_id is backend-specific (e.g. Piper voice name,
        ElevenLabs voice UUID). None requests the backend's
        default voice.
        """
        ...


# ---------------------------------------------------------------------------
# Canonical local Whisper backend (lazy-imports whisper-cpp)
# ---------------------------------------------------------------------------


class LocalWhisperBackend:
    """Reference ASR backend wrapping whisper-cpp.

    Lazy-imports the `whispercpp` (or `pywhispercpp`) Python
    binding at first transcribe() call. Module load doesn't pull
    the dependency — so operators who don't enable voice don't
    pay the import cost.

    Model file is operator-supplied. Forest ships a setup script
    that downloads the ggml-base.en.bin model (~150MB) into the
    plugin's data dir. T7 (operator setup docs) documents this.

    Per ADR-0070 D2, this is the canonical sovereign default.
    No network at inference time. CPU-only inference (the M4
    mini handles ~real-time for 5-10s utterances).
    """

    backend_id: str = "forest-voice-whisper-cpp"
    supported_methods: tuple[str, ...] = ("transcribe",)
    supported_input_formats: tuple[str, ...] = ("wav", "mp3", "flac", "ogg")

    def __init__(
        self,
        model_path: Optional[Path] = None,
        *,
        model_id: str = "ggml-base.en",
    ):
        """Args:
          model_path: filesystem path to the ggml model. Operator-
            supplied. Default falls back to
            ~/.forest/voice-models/ggml-base.en.bin which the
            setup script writes to.
          model_id: tag for the audit chain. Doesn't affect inference.
        """
        if model_path is None:
            model_path = (
                Path.home() / ".forest" / "voice-models" / "ggml-base.en.bin"
            )
        self.model_path = model_path
        self.model_id = model_id
        self._whisper = None  # lazy

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        audio_format: str,
        language_hint: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> VoiceTranscript:
        """Run whisper-cpp inference on the supplied audio bytes."""
        if audio_format not in self.supported_input_formats:
            raise VoiceFormatUnsupported(
                f"{self.backend_id} doesn't accept format "
                f"{audio_format!r}; supported: "
                f"{sorted(self.supported_input_formats)}"
            )

        if not self.model_path.exists():
            raise VoiceBackendUnavailable(
                f"whisper-cpp model not found at {self.model_path}. "
                f"Run ./setup-voice.command to download the canonical "
                f"ggml-base.en.bin (~150MB), or pass an explicit "
                f"model_path to LocalWhisperBackend(...)"
            )

        # Lazy-import the whisper-cpp binding. Two common bindings
        # exist (pywhispercpp + whispercpp); try the more common one
        # first.
        if self._whisper is None:
            try:
                import pywhispercpp.model as _pw  # type: ignore[import-not-found]
                self._whisper = _pw.Model(str(self.model_path))
            except ImportError:
                try:
                    import whispercpp as _wc  # type: ignore[import-not-found]
                    self._whisper = _wc.Whisper.from_pretrained(
                        str(self.model_path),
                    )
                except ImportError as e:
                    raise VoiceBackendUnavailable(
                        f"whisper-cpp not installed. Install via "
                        f"`pip install pywhispercpp` (preferred) or "
                        f"`pip install whispercpp`. Original error: {e}"
                    ) from e

        # Inference. wall-clock timed.
        t0 = time.perf_counter()
        try:
            segments = self._invoke_transcribe(
                audio_bytes, audio_format, language_hint,
            )
        except Exception as e:
            # Catch broadly — the binding's error taxonomy varies
            # across versions. Map to the unified VoiceIOError shape.
            raise VoiceDecodeError(
                f"whisper-cpp inference failed: {type(e).__name__}: {e}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if elapsed_ms > timeout_s * 1000:
            # Inference completed but slowly — still return the
            # transcript, but operator sees it took longer than
            # expected. Raising here would discard the work.
            pass

        # Stitch segments into the unified transcript shape.
        text = " ".join(seg.get("text", "").strip() for seg in segments).strip()
        # Pull duration from the last segment's end timestamp, or 0
        # if segments is empty.
        duration_s = float(segments[-1].get("end", 0.0)) if segments else 0.0
        # Confidence: whisper-cpp's avg_logprob is the conventional
        # signal. Fold across segments.
        confidences = [
            seg.get("avg_logprob")
            for seg in segments
            if seg.get("avg_logprob") is not None
        ]
        confidence = (
            sum(confidences) / len(confidences)
            if confidences else None
        )

        return VoiceTranscript(
            text=text,
            language=segments[0].get("lang") if segments else language_hint,
            duration_s=duration_s,
            confidence=confidence,
            backend_id=self.backend_id,
            model_id=self.model_id,
            elapsed_ms=elapsed_ms,
            segments=tuple(segments) if segments else None,
        )

    def _invoke_transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str,
        language_hint: Optional[str],
    ) -> list[dict]:
        """Adapter around the binding's transcribe call. Returns a
        normalized list of segment dicts with keys:
        {text, start, end, avg_logprob, lang}.

        Different bindings emit slightly different shapes; this
        adapter normalizes. Stubbed-out body: real binding wiring
        ships in T2 once the HTTP surface needs it. For now, raise
        VoiceBackendUnavailable so the test surface is clean and
        the wiring is deferred.
        """
        raise VoiceBackendUnavailable(
            "LocalWhisperBackend._invoke_transcribe is stubbed in T1. "
            "Real binding wiring lands in T2 with the /voice/transcribe "
            "HTTP endpoint."
        )

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        output_format: str = "wav",
        timeout_s: float = 60.0,
    ) -> bytes:
        """LocalWhisperBackend is ASR-only. TTS lives in
        forest-voice-piper (T5)."""
        raise VoiceBackendUnavailable(
            f"{self.backend_id} is ASR-only; "
            f"synthesize() not supported. Use forest-voice-piper (T5) "
            f"or another TTS backend."
        )
