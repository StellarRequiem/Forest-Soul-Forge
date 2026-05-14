"""ADR-0070 T2 (B287) — /voice/* endpoint tests.

Module-level tests of the router-level helpers + segment
normalizer. Integration tests with TestClient queued for T3 when
the frontend push-to-talk surface starts exercising the endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from forest_soul_forge.core.voice_io import _normalize_pywhispercpp_segments
from forest_soul_forge.daemon.routers.voice import (
    _count_voice_events_24h,
    _get_backend,
    _transcript_to_dict,
)


# ---------------------------------------------------------------------------
# _normalize_pywhispercpp_segments
# ---------------------------------------------------------------------------
def test_normalize_segments_none():
    assert _normalize_pywhispercpp_segments(None) == []


def test_normalize_segments_dict_form_seconds():
    raw = [
        {"text": "hello world", "start": 0.0, "end": 2.5,
         "avg_logprob": -0.3, "lang": "en"},
    ]
    out = _normalize_pywhispercpp_segments(raw)
    assert out[0]["text"] == "hello world"
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 2.5
    assert out[0]["avg_logprob"] == -0.3
    assert out[0]["lang"] == "en"


def test_normalize_segments_object_form_seconds():
    class _Seg:
        text = "spoken text"
        start = 1.0
        end = 3.0
        avg_logprob = -0.5
        lang = "en"
    out = _normalize_pywhispercpp_segments([_Seg()])
    assert out[0]["text"] == "spoken text"
    assert out[0]["start"] == 1.0


def test_normalize_segments_10ms_units_converted():
    """Older pywhispercpp emits 10ms units (e.g., end=250 = 2.5s).
    Normalizer detects + converts to seconds."""
    raw = [
        {"text": "fast", "start": 0, "end": 12000,
         "avg_logprob": None, "lang": None},
        {"text": "second", "start": 12000, "end": 25000,
         "avg_logprob": None, "lang": None},
    ]
    out = _normalize_pywhispercpp_segments(raw)
    # 12000 / 100 = 120 seconds
    assert out[0]["end"] == 120.0
    assert out[1]["start"] == 120.0
    assert out[1]["end"] == 250.0


def test_normalize_segments_trims_text():
    raw = [{"text": "   trimmed   ", "start": 0.0, "end": 1.0,
            "avg_logprob": None, "lang": None}]
    out = _normalize_pywhispercpp_segments(raw)
    assert out[0]["text"] == "trimmed"


# ---------------------------------------------------------------------------
# _get_backend caching
# ---------------------------------------------------------------------------
def test_get_backend_caches_instance():
    """Second call returns the same backend instance — keeps the
    whisper model warm across requests."""
    b1 = _get_backend()
    b2 = _get_backend()
    assert b1 is b2


# ---------------------------------------------------------------------------
# _transcript_to_dict
# ---------------------------------------------------------------------------
def test_transcript_to_dict_marshaling():
    from forest_soul_forge.core.voice_io import VoiceTranscript
    t = VoiceTranscript(
        text="hi", language="en", duration_s=1.0,
        confidence=0.9, backend_id="b", model_id="m",
        elapsed_ms=100, segments=({"text": "hi", "start": 0.0, "end": 1.0},),
    )
    d = _transcript_to_dict(t)
    assert d["text"] == "hi"
    assert d["language"] == "en"
    assert d["segments"] == [{"text": "hi", "start": 0.0, "end": 1.0}]


def test_transcript_to_dict_none_segments():
    from forest_soul_forge.core.voice_io import VoiceTranscript
    t = VoiceTranscript(
        text="x", language=None, duration_s=0.5,
        confidence=None, backend_id="b", model_id="m",
        elapsed_ms=50,
    )
    d = _transcript_to_dict(t)
    assert d["segments"] is None


# ---------------------------------------------------------------------------
# _count_voice_events_24h
# ---------------------------------------------------------------------------
class _FakeChain:
    def __init__(self, entries: list[Any]):
        self._entries = entries

    def tail(self, n: int) -> list[Any]:
        return list(reversed(self._entries[-n:]))


def _entry(seq: int, event_type: str, ts: str = None):
    return SimpleNamespace(
        seq=seq,
        timestamp=ts or "2026-05-14T12:00:00Z",
        event_type=event_type,
        event_data={},
    )


def test_count_voice_events_window_filtering():
    now = datetime.now(timezone.utc)
    recent_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chain = _FakeChain([
        _entry(1, "voice_transcribed", recent_ts),
        _entry(2, "voice_transcribed", recent_ts),
        _entry(3, "voice_failed", recent_ts),
        _entry(4, "voice_transcribed", old_ts),  # outside window
        _entry(5, "tool_call_succeeded", recent_ts),  # ignored
    ])
    transcribed, failed = _count_voice_events_24h(chain)
    assert transcribed == 2
    assert failed == 1


def test_count_voice_events_empty_chain():
    chain = _FakeChain([])
    transcribed, failed = _count_voice_events_24h(chain)
    assert transcribed == 0
    assert failed == 0
