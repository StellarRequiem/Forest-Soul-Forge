"""Hosted ASR/TTS backend adapters — ADR-0070 T6 (B328).

Reference implementations that opt the operator into hosted
cloud backends. Per ADR-0070 D2, local-first is the default
posture; these adapters exist for operators who explicitly
want quality / latency / language coverage that the canonical
local backends don't deliver yet.

## What ships in T6

  - :class:`OpenAIWhisperBackend` — ASR via OpenAI's
    ``audio.transcriptions`` endpoint (api.openai.com). Lazy-
    imports ``openai`` so the daemon boots without the dep.
  - :class:`ElevenLabsTTSBackend` — TTS via ElevenLabs.
    Stub that documents the expected shape; real network calls
    deferred (per Forest's no-network-without-explicit-opt-in
    posture this would require additional explicit operator
    consent at a future tranche).

## Credential resolution

API keys NEVER come from process env directly. They flow through
the Forest secrets store (ADR-003X C1) so:

  - The operator's at-rest encryption covers them.
  - Per-agent allowlists gate which agents can wake them up.
  - The chain captures secret_revealed events at access time.

The adapter constructor takes a ``secret_name`` (string). At
``transcribe`` time it pulls the secret through the supplied
``secrets_accessor`` (a SecretsAccessor instance from
``ctx.secrets``). Daemons that don't have the secrets subsystem
wired (test contexts) can pass a callable that returns the
plaintext directly.

## Failure posture

A hosted adapter that can't load its dep, can't reach the
network, or has a missing API key surfaces a
:class:`VoiceBackendUnavailable` (defined in voice_io.py). The
caller (the /voice/transcribe handler) returns 503 + emits
``voice_failed`` — same path the local backend uses for missing
model files.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from forest_soul_forge.core.voice_io import (
    VoiceBackendUnavailable,
    VoiceDecodeError,
    VoiceFormatUnsupported,
    VoiceIOError,
    VoiceTimeoutError,
    VoiceTranscript,
)


# Callable shape for credential resolution. Returns the plaintext
# API key for a given secret name. Daemons wire this to
# ``ctx.secrets.get``; tests pass a lambda returning a stub.
SecretFn = Callable[[str], str]


@dataclass
class OpenAIWhisperBackend:
    """Hosted ASR via OpenAI's Whisper API.

    Lazy-imports the ``openai`` package so daemons without the
    dep boot cleanly. The ``OPENAI_API_KEY`` env var is NOT
    consulted directly — credentials always flow through the
    Forest secrets accessor.

    Operators enable via:
      1. ``pip install openai`` on the daemon's Python.
      2. Store the API key in the secrets backend:
         ``fsf secret set openai_api_key sk-...``.
      3. Set ``FSF_VOICE_ASR_BACKEND=openai_whisper`` + provide
         the agent constitution that grants access to the
         ``openai_api_key`` secret name (ADR-003X C2).
      4. Restart the daemon.

    Note: this backend SENDS YOUR AUDIO TO OPENAI. The audit
    chain records the dispatch with backend_id='openai-whisper'
    so the operator can audit when hosted ASR was used vs the
    local default. Every dispatch is operator-traceable.
    """

    backend_id: str = "openai-whisper"
    supported_methods: tuple[str, ...] = ("transcribe",)
    # OpenAI accepts a broad set; we expose the most common.
    supported_input_formats: tuple[str, ...] = (
        "wav", "mp3", "mp4", "m4a", "ogg", "flac", "webm",
    )
    # The transcription model. v1 ships whisper-1; future
    # tranches expose gpt-4o-transcribe / gpt-4o-mini-transcribe.
    model_id: str = "whisper-1"
    # Where the operator's API key is filed in the secrets store.
    secret_name: str = "openai_api_key"
    # API endpoint. Overridable for Azure OpenAI / proxy setups.
    base_url: str = "https://api.openai.com/v1"

    _client: Any = field(default=None, init=False, repr=False)

    @property
    def model_path(self) -> Optional[Any]:
        """Compat with LocalWhisperBackend's interface — hosted
        backends have no on-disk model so this is always None.
        The /voice/status endpoint reads .model_path to decide
        the 'model_present' boolean; for hosted backends the
        equivalent check is `credentials available`, which the
        status endpoint reports separately."""
        return None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:
            raise VoiceBackendUnavailable(
                f"{self.backend_id}: openai package not installed. "
                f"`pip install openai`."
            ) from e
        # Construction defers to .transcribe() so we can resolve
        # the secret per-call (different agents may have different
        # credentials over time).
        return OpenAI  # the class itself; per-call we instantiate

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        audio_format: str,
        language_hint: Optional[str] = None,
        timeout_s: float = 60.0,
        secret_fn: Optional[SecretFn] = None,
    ) -> VoiceTranscript:
        """Run hosted Whisper inference on the supplied audio bytes.

        ``secret_fn`` is required for production use; tests pass a
        lambda that returns a stub key. When not supplied + the
        env var fallback is unavailable, the call raises
        VoiceBackendUnavailable rather than blocking.
        """
        if audio_format not in self.supported_input_formats:
            raise VoiceFormatUnsupported(
                f"{self.backend_id} doesn't accept format "
                f"{audio_format!r}; supported: "
                f"{sorted(self.supported_input_formats)}"
            )
        if not audio_bytes:
            raise VoiceDecodeError(
                f"{self.backend_id}: empty audio payload"
            )

        OpenAI = self._ensure_client()  # raises VoiceBackendUnavailable

        if secret_fn is None:
            raise VoiceBackendUnavailable(
                f"{self.backend_id}: secret_fn required to resolve "
                f"{self.secret_name!r}. Wire ctx.secrets.get."
            )
        try:
            api_key = secret_fn(self.secret_name)
        except Exception as e:  # noqa: BLE001 — wrap into our hierarchy
            raise VoiceBackendUnavailable(
                f"{self.backend_id}: secret resolution failed: {e}"
            ) from e
        if not api_key:
            raise VoiceBackendUnavailable(
                f"{self.backend_id}: secret {self.secret_name!r} "
                f"resolved to empty value"
            )

        client = OpenAI(api_key=api_key, base_url=self.base_url)
        # Wrap bytes as a named file-like; OpenAI's SDK needs the
        # filename for content-type inference.
        file_obj = io.BytesIO(audio_bytes)
        file_obj.name = f"audio.{audio_format}"

        start_ns = time.monotonic_ns()
        try:
            resp = client.audio.transcriptions.create(
                model=self.model_id,
                file=file_obj,
                language=language_hint,
                timeout=timeout_s,
            )
        except Exception as e:  # noqa: BLE001 — translate to our hierarchy
            # Best-effort categorization. Timeout errors get their
            # own exception type so the caller can retry vs refuse.
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                raise VoiceTimeoutError(
                    f"{self.backend_id}: API timeout after {timeout_s}s: {e}"
                ) from e
            raise VoiceIOError(
                f"{self.backend_id}: API call failed: {e}"
            ) from e
        elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)

        # The OpenAI SDK returns a Transcription object with .text
        # + optionally .language + .duration (when verbose response
        # format is requested). v1 keeps the call shape simple.
        text = getattr(resp, "text", None) or ""
        language = getattr(resp, "language", None)
        duration = getattr(resp, "duration", None)

        return VoiceTranscript(
            text=text,
            language=language,
            duration_s=float(duration) if duration is not None else 0.0,
            confidence=None,  # OpenAI doesn't expose log-probs in v1
            backend_id=self.backend_id,
            model_id=self.model_id,
            elapsed_ms=elapsed_ms,
        )


# ---------------------------------------------------------------------------
# ElevenLabs TTS — stub
# ---------------------------------------------------------------------------


@dataclass
class ElevenLabsTTSBackend:
    """Hosted TTS via ElevenLabs. Stub: documents the shape +
    raises VoiceBackendUnavailable on synthesize() so daemons
    that wire it accidentally don't silently fall through to a
    no-op.

    Real network synthesis lands in a follow-up tranche; T6's
    closure goal is the substrate shape + the ASR adapter.
    """

    backend_id: str = "elevenlabs-tts"
    supported_methods: tuple[str, ...] = ("synthesize",)
    supported_output_formats: tuple[str, ...] = ("mp3", "wav", "ogg")
    default_voice_id: str = "rachel"
    secret_name: str = "elevenlabs_api_key"

    def synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        output_format: str = "mp3",
        secret_fn: Optional[SecretFn] = None,
    ) -> bytes:
        raise VoiceBackendUnavailable(
            f"{self.backend_id}: stub implementation. Wire the "
            f"elevenlabs SDK in a follow-up tranche."
        )

    def available_voices(self) -> list[dict]:
        """Compat with LocalPiperBackend.available_voices."""
        return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def resolve_hosted_asr_backend(
    backend_id: str,
    *,
    model_id: Optional[str] = None,
    secret_name: Optional[str] = None,
) -> Any:
    """Lookup helper for the lifespan's backend swap path.
    Returns the matching backend instance. Unknown backend ids
    raise VoiceIOError — the caller decides whether to fall
    back to the local backend or surface the misconfiguration."""
    bid = backend_id.strip().lower()
    if bid in ("openai_whisper", "openai-whisper", "openai"):
        kwargs: dict[str, Any] = {}
        if model_id:
            kwargs["model_id"] = model_id
        if secret_name:
            kwargs["secret_name"] = secret_name
        return OpenAIWhisperBackend(**kwargs)
    raise VoiceIOError(
        f"unknown hosted ASR backend {backend_id!r}; "
        f"supported: openai-whisper"
    )
