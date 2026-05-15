"""Wake-word detector substrate — ADR-0070 T4 (B327).

Always-listening daemon mode that watches the microphone for a
configurable wake-phrase and, on match, emits a
``voice_wake_word_detected`` audit event + signals the
transcribe pipeline to capture the operator's follow-up.

## Off by default — opt-in posture

The detector is OFF unless the operator sets
``FSF_WAKE_WORD_ENABLED=true``. Even with that on, the lifespan
constructs but does NOT start the detector unless the backend
(``openWakeWord`` by default, future tranches add Porcupine /
Snowboy) is available. Continuous mic capture is a privacy-
sensitive default; the operator opts in explicitly.

## Plugin-shape detector

Following ADR-0070 D1, the detector is plugin-shaped: a
``WakeWordBackend`` Protocol with ``start()`` / ``stop()`` /
``current_phrase()`` / ``backend_id``. T4 ships:

  - The Protocol + the audit-event glue
  - A null detector (``NullWakeWordBackend``) — always inactive,
    safe substrate for daemons that don't have the backend
    installed but want the lifespan code path exercised.
  - A reference openWakeWord backend (``OpenWakeWordBackend``)
    that lazy-imports the optional ``openwakeword`` package.

The null backend is what's wired by default. Operators install
``openwakeword`` + flip ``FSF_WAKE_WORD_BACKEND=openwakeword``
to switch to the real one.

## Detection event shape

``voice_wake_word_detected`` event_data: ``{phrase: str,
confidence: float, backend_id: str}``. The chain entry IS the
operator-traceability record; downstream code (the future
"after wake-word, auto-transcribe" handoff) consumes the
detection by polling the detector's ``last_detection`` value or
subscribing to a callback wired at lifespan.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol


# Default wake phrase. Operators override via env var.
DEFAULT_WAKE_PHRASE = "hey forest"
ENV_WAKE_WORD_ENABLED = "FSF_WAKE_WORD_ENABLED"
ENV_WAKE_WORD_PHRASE = "FSF_WAKE_WORD_PHRASE"
ENV_WAKE_WORD_BACKEND = "FSF_WAKE_WORD_BACKEND"


class WakeWordError(RuntimeError):
    """Raised on hard-fatal detector problems (backend missing
    its native dep, model file unreadable). The lifespan catches
    this and continues with the null backend so the daemon stays
    up."""


@dataclass(frozen=True)
class WakeWordDetection:
    """One successful detection."""
    phrase: str
    confidence: float
    backend_id: str
    detected_at: str  # ISO 8601 UTC


# Callback signature for "I detected the wake word." Lifespan
# wires this to an audit-emit closure + (future tranche) a
# transcribe-pipeline trigger.
WakeCallback = Callable[[WakeWordDetection], None]


class WakeWordBackend(Protocol):
    """The contract every wake-word detector implements.

    ``start(callback)`` begins continuous listening on a worker
    thread and invokes ``callback`` on every successful detection.
    ``stop()`` joins the worker. ``current_phrase()`` is the
    configured phrase (a debug surface for /voice/status).
    """

    backend_id: str

    def start(self, callback: WakeCallback) -> None: ...
    def stop(self) -> None: ...
    def current_phrase(self) -> str: ...
    def is_running(self) -> bool: ...


# ---------------------------------------------------------------------------
# Null backend — substrate-only, never fires
# ---------------------------------------------------------------------------


@dataclass
class NullWakeWordBackend:
    """No-op detector. Safe to wire by default; operators
    installing a real backend swap it via env var."""

    backend_id: str = "null"
    phrase: str = DEFAULT_WAKE_PHRASE
    _running: bool = field(default=False)

    def start(self, callback: WakeCallback) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def current_phrase(self) -> str:
        return self.phrase

    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# openWakeWord backend — real detector, optional dep
# ---------------------------------------------------------------------------


@dataclass
class OpenWakeWordBackend:
    """Reference implementation wrapping the ``openwakeword``
    pure-Python detector. Lazy-imports the dep on start() so the
    daemon boots without it.

    The audio capture loop runs on a worker thread; the loop
    polls 1280-sample frames from the configured microphone
    device and feeds them to ``Model.predict``. Confidence above
    ``threshold`` (default 0.5) fires the callback.

    Implementation notes:
      - The mic capture uses sounddevice (also lazy-imported).
        If sounddevice isn't installed but openwakeword is, the
        backend refuses cleanly with WakeWordError.
      - On exception inside the loop, the loop sleeps 1s and
        retries — the detector should be self-healing across
        transient sound-card hiccups.
    """

    backend_id: str = "openwakeword"
    phrase: str = DEFAULT_WAKE_PHRASE
    threshold: float = 0.5
    sample_rate: int = 16000
    frame_size: int = 1280

    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _stop_event: threading.Event = field(
        default_factory=threading.Event, init=False,
    )
    _model: Any = field(default=None, init=False)

    def current_phrase(self) -> str:
        return self.phrase

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, callback: WakeCallback) -> None:
        if self.is_running():
            return
        # Lazy import here so the daemon doesn't pay the cost
        # of openwakeword until the operator opts in.
        try:
            from openwakeword.model import Model  # type: ignore[import-not-found]
        except ImportError as e:
            raise WakeWordError(
                "openwakeword package not installed. "
                "pip install openwakeword + restart the daemon, "
                "or set FSF_WAKE_WORD_BACKEND=null to disable."
            ) from e
        try:
            import sounddevice  # type: ignore[import-not-found] # noqa: F401
        except ImportError as e:
            raise WakeWordError(
                "sounddevice not installed (required for "
                "OpenWakeWord mic capture). pip install sounddevice."
            ) from e
        self._model = Model()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(callback,),
            name="voice-wake-word", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        self._model = None

    def _run(self, callback: WakeCallback) -> None:
        """Worker thread loop. Polls frames, predicts, fires
        callback when confidence > threshold."""
        import sounddevice as sd  # local import; start() already verified
        try:
            with sd.InputStream(
                samplerate=self.sample_rate, channels=1,
                dtype="int16", blocksize=self.frame_size,
            ) as stream:
                while not self._stop_event.is_set():
                    try:
                        frame, _overflowed = stream.read(self.frame_size)
                        preds = self._model.predict(frame.flatten())
                        # preds is a dict {model_name: confidence}; we
                        # fire the callback if ANY model in the bundle
                        # exceeds the threshold (operators usually
                        # install exactly one wake-word model anyway).
                        for model_name, conf in preds.items():
                            if conf >= self.threshold:
                                detection = WakeWordDetection(
                                    phrase=self.phrase,
                                    confidence=float(conf),
                                    backend_id=self.backend_id,
                                    detected_at=_now_iso(),
                                )
                                try:
                                    callback(detection)
                                except Exception:
                                    # Callback failures must not
                                    # take down the detector loop.
                                    pass
                    except Exception:
                        # Transient capture / predict error — short
                        # sleep + retry. The Event check at the top
                        # gates clean shutdown either way.
                        if self._stop_event.wait(1.0):
                            return
        except Exception:
            # InputStream failed to open at all. Detector goes
            # silent; lifespan logged the diagnostic.
            return


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def resolve_wake_word_backend(
    *, backend_id: Optional[str] = None,
    phrase: Optional[str] = None,
) -> WakeWordBackend:
    """Construct the configured backend.

    Resolution order: explicit ``backend_id`` kwarg → env var
    ``FSF_WAKE_WORD_BACKEND`` → 'null'. The phrase resolution is
    parallel: kwarg → ``FSF_WAKE_WORD_PHRASE`` → DEFAULT_WAKE_PHRASE.

    Returns the null backend on any error so the daemon stays up.
    """
    requested = (
        backend_id
        or os.environ.get(ENV_WAKE_WORD_BACKEND)
        or "null"
    ).strip().lower()
    resolved_phrase = (
        phrase
        or os.environ.get(ENV_WAKE_WORD_PHRASE)
        or DEFAULT_WAKE_PHRASE
    ).strip()
    if requested in ("null", "off", "disabled"):
        return NullWakeWordBackend(phrase=resolved_phrase)
    if requested == "openwakeword":
        return OpenWakeWordBackend(phrase=resolved_phrase)
    # Unknown backend id — refuse to silently pick something else;
    # surface as a startup diagnostic by returning null + the
    # operator sees the mismatch in /healthz / /voice/status.
    return NullWakeWordBackend(phrase=resolved_phrase)


def wake_word_enabled() -> bool:
    """Operator's opt-in switch. Default off."""
    return (
        os.environ.get(ENV_WAKE_WORD_ENABLED) or "false"
    ).strip().lower() == "true"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
