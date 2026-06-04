"""Root test configuration.

Disable the cross-process single-writer lock for the whole suite. The lock is a
production guard — the daemon holds it for its lifetime and write-CLIs must
acquire it (ADR-0005 follow-up; see core/single_writer.py). But the suite boots
the FastAPI app hundreds of times in one process; with the lock on, every boot
after the first would contend on the single global lockfile and refuse to start.

The lock module's own unit tests (tests/unit/test_single_writer.py) call
``WriterLock`` directly rather than through the env gate, so the locking logic
itself stays fully covered.
"""
import os

os.environ.setdefault("FSF_DISABLE_WRITER_LOCK", "1")
