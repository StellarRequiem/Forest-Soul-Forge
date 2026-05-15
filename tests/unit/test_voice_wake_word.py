"""ADR-0070 T4 (B327) — wake-word substrate tests.

Coverage:
  Factory:
    - default = NullWakeWordBackend
    - explicit 'openwakeword' returns the real one (without
      starting it; native deps absent in sandbox is fine)
    - 'null' / 'off' / 'disabled' all resolve to null
    - unknown backend id falls back to null
    - phrase resolution: kwarg > env > default

  wake_word_enabled():
    - false / unset = False; 'TRUE' / 'true' = True

  NullWakeWordBackend:
    - start/stop toggles is_running()
    - current_phrase echoes
    - callback never fires (no detections by design)

  Audit-event registration:
    - voice_wake_word_armed / detected / disarmed all in
      KNOWN_EVENT_TYPES
"""
from __future__ import annotations

import os

import pytest

from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.voice_wake_word import (
    DEFAULT_WAKE_PHRASE,
    ENV_WAKE_WORD_BACKEND,
    ENV_WAKE_WORD_ENABLED,
    ENV_WAKE_WORD_PHRASE,
    NullWakeWordBackend,
    OpenWakeWordBackend,
    WakeWordDetection,
    resolve_wake_word_backend,
    wake_word_enabled,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_default_backend_is_null(monkeypatch):
    monkeypatch.delenv(ENV_WAKE_WORD_BACKEND, raising=False)
    b = resolve_wake_word_backend()
    assert isinstance(b, NullWakeWordBackend)


def test_explicit_null_is_null():
    b = resolve_wake_word_backend(backend_id="null")
    assert isinstance(b, NullWakeWordBackend)


def test_off_and_disabled_aliases_resolve_to_null():
    for alias in ("off", "disabled", "Off", "DISABLED"):
        b = resolve_wake_word_backend(backend_id=alias)
        assert isinstance(b, NullWakeWordBackend), alias


def test_openwakeword_backend_returned_without_starting():
    """We must be able to construct the backend even without
    openwakeword installed. start() is where the import fires."""
    b = resolve_wake_word_backend(backend_id="openwakeword")
    assert isinstance(b, OpenWakeWordBackend)
    assert b.backend_id == "openwakeword"


def test_unknown_backend_falls_back_to_null():
    b = resolve_wake_word_backend(backend_id="porcupine")
    assert isinstance(b, NullWakeWordBackend)


def test_phrase_resolution_kwarg_wins(monkeypatch):
    monkeypatch.setenv(ENV_WAKE_WORD_PHRASE, "hey env")
    b = resolve_wake_word_backend(phrase="hey kwarg")
    assert b.current_phrase() == "hey kwarg"


def test_phrase_resolution_env_used_when_no_kwarg(monkeypatch):
    monkeypatch.setenv(ENV_WAKE_WORD_PHRASE, "hey env")
    b = resolve_wake_word_backend()
    assert b.current_phrase() == "hey env"


def test_phrase_resolution_default_when_neither(monkeypatch):
    monkeypatch.delenv(ENV_WAKE_WORD_PHRASE, raising=False)
    b = resolve_wake_word_backend()
    assert b.current_phrase() == DEFAULT_WAKE_PHRASE


def test_env_backend_picked_up(monkeypatch):
    monkeypatch.setenv(ENV_WAKE_WORD_BACKEND, "openwakeword")
    b = resolve_wake_word_backend()
    assert isinstance(b, OpenWakeWordBackend)


# ---------------------------------------------------------------------------
# wake_word_enabled
# ---------------------------------------------------------------------------


def test_wake_word_enabled_default_off(monkeypatch):
    monkeypatch.delenv(ENV_WAKE_WORD_ENABLED, raising=False)
    assert wake_word_enabled() is False


def test_wake_word_enabled_true_variants(monkeypatch):
    for val in ("true", "TRUE", "True"):
        monkeypatch.setenv(ENV_WAKE_WORD_ENABLED, val)
        assert wake_word_enabled() is True


def test_wake_word_enabled_false_variants(monkeypatch):
    for val in ("false", "0", "no", "off", ""):
        monkeypatch.setenv(ENV_WAKE_WORD_ENABLED, val)
        assert wake_word_enabled() is False


# ---------------------------------------------------------------------------
# NullWakeWordBackend behavior
# ---------------------------------------------------------------------------


def test_null_backend_is_not_running_by_default():
    b = NullWakeWordBackend()
    assert b.is_running() is False


def test_null_backend_start_stop_toggle():
    calls: list[WakeWordDetection] = []
    b = NullWakeWordBackend()
    b.start(lambda d: calls.append(d))
    assert b.is_running() is True
    b.stop()
    assert b.is_running() is False
    # By contract null detector NEVER fires its callback.
    assert calls == []


def test_null_backend_current_phrase_echoes():
    b = NullWakeWordBackend(phrase="hey claude")
    assert b.current_phrase() == "hey claude"


# ---------------------------------------------------------------------------
# Audit event registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_type", [
    "voice_wake_word_armed",
    "voice_wake_word_detected",
    "voice_wake_word_disarmed",
])
def test_wake_word_audit_events_registered(event_type):
    assert event_type in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


def test_detection_is_frozen():
    d = WakeWordDetection(
        phrase="hey x", confidence=0.9,
        backend_id="null", detected_at="2026-05-15T00:00:00Z",
    )
    with pytest.raises(Exception):
        d.phrase = "other"  # type: ignore[misc]
