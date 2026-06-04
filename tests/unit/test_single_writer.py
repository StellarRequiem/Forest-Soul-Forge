"""Tests for the cross-process single-writer lock (ADR-0005 follow-up).

The lock is what stops a write-CLI from corrupting shared state while the daemon
is live — the failure mode that interleaved two seq counters into the audit
chain + registry DB. flock conflicts across separate open-file-descriptions even
within one process, so these single-process tests exercise the real cross-
process semantics.
"""
import pytest

from forest_soul_forge.core.single_writer import (
    SingleWriterError,
    WriterLock,
    assert_single_writer,
)


def test_acquire_writes_holder_and_release(tmp_path):
    p = tmp_path / "w.lock"
    lock = WriterLock(p, role="t1").acquire()
    assert lock.held
    assert p.exists()
    assert "role=t1" in p.read_text()
    lock.release()
    assert not lock.held


def test_second_acquire_refused_while_held(tmp_path):
    p = tmp_path / "w.lock"
    first = WriterLock(p, role="first").acquire()
    try:
        with pytest.raises(SingleWriterError) as ei:
            WriterLock(p, role="second").acquire()
        msg = str(ei.value)
        assert "held by another live process" in msg
        assert "first" in msg  # the holder's role is reported
    finally:
        first.release()


def test_release_allows_reacquire(tmp_path):
    p = tmp_path / "w.lock"
    a = WriterLock(p).acquire()
    a.release()
    b = WriterLock(p).acquire()  # must succeed now
    assert b.held
    b.release()


def test_context_manager_acquires_and_releases(tmp_path):
    p = tmp_path / "w.lock"
    with WriterLock(p, role="ctx"):
        with pytest.raises(SingleWriterError):
            WriterLock(p).acquire()
    # released on context exit → reacquire works
    WriterLock(p).acquire().release()


def test_assert_single_writer_raises_when_held(tmp_path):
    p = tmp_path / "w.lock"
    held = WriterLock(p, role="daemon").acquire()
    try:
        with pytest.raises(SingleWriterError):
            assert_single_writer(p, role="cli")
    finally:
        held.release()


def test_assert_single_writer_returns_held_lock_when_free(tmp_path):
    p = tmp_path / "w.lock"
    lock = assert_single_writer(p, role="cli")
    assert lock.held
    lock.release()
