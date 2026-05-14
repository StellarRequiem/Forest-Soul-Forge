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
        adapter normalizes. Bindings supported:
          - pywhispercpp.model.Model — preferred binding. Has a
            .transcribe(audio) returning a list of Segment objects
            with .text + .start + .end attributes (timestamps in
            10ms units in older versions, seconds in newer).
          - whispercpp.Whisper — alternative binding. Has a
            .transcribe(audio) returning a string + .segments()
            generator.

        Audio handling: bindings expect WAV PCM 16kHz mono. For T2
        we pass through what the operator uploaded; ffmpeg-based
        format normalization is queued for T3 (push-to-talk) when
        browser-recorded webm/opus shows up.
        """
        # Persist audio_bytes to a temp file. Most bindings prefer
        # a path over an in-memory buffer because they shell out
        # to whisper.cpp's CLI which expects a filesystem path.
        import tempfile
        suffix = f".{audio_format}" if audio_format else ".wav"
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=suffix, delete=False,
        ) as tf:
            tf.write(audio_bytes)
            audio_path = tf.name

        try:
            # pywhispercpp binding shape
            if hasattr(self._whisper, "transcribe"):
                try:
                    raw_segments = self._whisper.transcribe(
                        audio_path,
                        language=language_hint or "en",
                    )
                    return _normalize_pywhispercpp_segments(raw_segments)
                except TypeError:
                    # Older pywhispercpp signature; retry without
                    # the language kwarg.
                    raw_segments = self._whisper.transcribe(audio_path)
                    return _normalize_pywhispercpp_segments(raw_segments)
            raise VoiceBackendUnavailable(
                "loaded whisper binding has no .transcribe() method"
            )
        finally:
            # Best-effort cleanup. tempfile is in /tmp; OS will
            # clean it eventually but explicit unlink keeps the
            # session disk usage low.
            try:
                import os as _os
                _os.unlink(audio_path)
            except OSError:
                pass

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


class LocalPiperBackend:
    """Reference TTS backend wrapping Piper TTS.

    Lazy-imports the `piper-tts` Python binding at first
    synthesize() call. Module load doesn't pull the dependency —
    operators not enabling TTS pay nothing.

    Per ADR-0070 D2, this is the canonical sovereign default for
    TTS. CPU-only inference (~150-300ms for short utterances on
    M-series). Piper voices are .onnx model files (~20-60MB each)
    operator-supplied; setup script (T7) downloads a default
    en_US voice. Multi-voice support: each .onnx file in the
    plugin's voices/ dir is a selectable voice_id.

    Audio output format: WAV 22kHz mono PCM (Piper's native).
    """

    backend_id: str = "forest-voice-piper"
    supported_methods: tuple[str, ...] = ("synthesize",)
    # Synthesize doesn't accept input formats — TTS is text-in,
    # audio-out — so this stays empty for TTS-only backends.
    supported_input_formats: tuple[str, ...] = ()
    # Output formats Piper natively produces.
    supported_output_formats: tuple[str, ...] = ("wav",)

    def __init__(
        self,
        voices_dir: Optional[Path] = None,
        *,
        default_voice_id: str = "en_US-amy-medium",
    ):
        """Args:
          voices_dir: filesystem dir containing .onnx voice model
            files + their .onnx.json configs. Default
            ~/.forest/voice-models/piper/. Each .onnx file's stem
            is a selectable voice_id.
          default_voice_id: voice_id to use when caller passes
            voice_id=None.
        """
        if voices_dir is None:
            voices_dir = (
                Path.home() / ".forest" / "voice-models" / "piper"
            )
        self.voices_dir = voices_dir
        self.default_voice_id = default_voice_id
        # Per-voice cached PiperVoice instances. Loading a voice
        # is ~150ms; cache keeps subsequent synthesize calls fast.
        self._voices_cache: dict[str, Any] = {}

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        audio_format: str,
        language_hint: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> "VoiceTranscript":
        """LocalPiperBackend is TTS-only. ASR lives in
        forest-voice-whisper-cpp."""
        raise VoiceBackendUnavailable(
            f"{self.backend_id} is TTS-only; transcribe() "
            f"not supported. Use forest-voice-whisper-cpp or "
            f"another ASR backend."
        )

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        output_format: str = "wav",
        timeout_s: float = 60.0,
    ) -> bytes:
        """Generate WAV audio from text using a Piper voice.

        Args:
          text: input text. 1-5000 chars.
          voice_id: selects which .onnx voice file. None →
            self.default_voice_id.
          output_format: only "wav" supported in T5; convert
            elsewhere if needed.
        """
        if not isinstance(text, str) or not text.strip():
            raise VoiceDecodeError("text must be a non-empty string")
        if len(text) > 5000:
            raise VoiceDecodeError(
                f"text too long ({len(text)} chars > 5000); split into "
                "multiple synthesize calls or summarize first"
            )
        if output_format not in self.supported_output_formats:
            raise VoiceFormatUnsupported(
                f"{self.backend_id} doesn't produce format "
                f"{output_format!r}; supported: "
                f"{sorted(self.supported_output_formats)}"
            )

        voice_id = voice_id or self.default_voice_id
        voice_path = self.voices_dir / f"{voice_id}.onnx"
        if not voice_path.exists():
            raise VoiceBackendUnavailable(
                f"piper voice file not found: {voice_path}. "
                f"Run ./setup-voice.command to download the canonical "
                f"en_US voice, or supply your own .onnx + .onnx.json "
                f"under {self.voices_dir}"
            )

        # Lazy-import piper-tts. The Python piper-tts package
        # bundles its own .onnx inference; no system dep on
        # libonnxruntime needed.
        voice = self._voices_cache.get(voice_id)
        if voice is None:
            try:
                from piper.voice import PiperVoice  # type: ignore[import-not-found]
                voice = PiperVoice.load(str(voice_path))
                self._voices_cache[voice_id] = voice
            except ImportError as e:
                raise VoiceBackendUnavailable(
                    f"piper-tts not installed. Install via "
                    f"`pip install piper-tts`. Original error: {e}"
                ) from e
            except Exception as e:
                raise VoiceBackendUnavailable(
                    f"piper voice load failed for {voice_id}: "
                    f"{type(e).__name__}: {e}"
                ) from e

        # Synthesize to in-memory bytes via a BytesIO file-like.
        # Piper writes a WAV header + PCM payload.
        import io
        import wave
        t0 = time.perf_counter()
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                # PiperVoice.synthesize streams audio chunks into a
                # writable WAV file object. Sample rate + width come
                # from the voice's config.
                voice.synthesize(text, wav_file)
            audio_bytes = buf.getvalue()
        except Exception as e:
            raise VoiceDecodeError(
                f"piper synthesize failed: {type(e).__name__}: {e}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if elapsed_ms > timeout_s * 1000:
            # Inference completed but slowly — still return.
            pass

        return audio_bytes

    def available_voices(self) -> list[str]:
        """List voice_ids available in voices_dir.

        Used by /voice/status to surface the operator-installed
        voice catalog. A voice is "available" if its .onnx file
        exists; the .onnx.json config is checked at load time.
        """
        if not self.voices_dir.exists():
            return []
        return sorted(
            p.stem for p in self.voices_dir.glob("*.onnx")
        )


def _normalize_pywhispercpp_segments(raw: Any) -> list[dict]:
    """Normalize pywhispercpp Segment objects (or list thereof)
    into the unified {text, start, end, avg_logprob, lang} dict
    shape.

    Older pywhispercpp returns a list of Segment dataclasses with
    .text + .start + .end (timestamps in 10ms units). Newer
    versions return seconds. We detect via magnitude — if a
    segment's end > 10_000 we assume 10ms units.
    """
    if raw is None:
        return []
    segments: list[dict] = []
    # Detect 10ms units by peeking at the first non-empty segment.
    use_ms_units = False
    for seg in raw:
        end_val = getattr(seg, "end", None) or (
            seg.get("end") if isinstance(seg, dict) else 0
        )
        if isinstance(end_val, (int, float)) and end_val > 10000:
            use_ms_units = True
            break

    for seg in raw:
        if isinstance(seg, dict):
            text = seg.get("text", "")
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            avg_logprob = seg.get("avg_logprob")
            lang = seg.get("lang") or seg.get("language")
        else:
            text = getattr(seg, "text", "")
            start = getattr(seg, "start", 0.0)
            end = getattr(seg, "end", 0.0)
            avg_logprob = getattr(seg, "avg_logprob", None)
            lang = getattr(seg, "lang", None) or getattr(seg, "language", None)

        # Convert 10ms units → seconds when needed.
        if use_ms_units:
            start = float(start) / 100.0
            end = float(end) / 100.0
        else:
            start = float(start)
            end = float(end)

        segments.append({
            "text": str(text).strip(),
            "start": start,
            "end": end,
            "avg_logprob": (
                float(avg_logprob)
                if isinstance(avg_logprob, (int, float)) else None
            ),
            "lang": lang,
        })

    return segments
