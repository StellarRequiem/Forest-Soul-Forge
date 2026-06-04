"""Cross-process single-writer lock (ADR-0005 follow-up).

Within one process, the audit chain + registry coordinate writers via
``app.state.write_lock`` and an internal RLock. Cross-process appends to the
same files were explicitly deferred (ADR-0005 threat model) — the daemon was
assumed to be the sole writer.

That assumption was violated: a bulk plugin-install CLI ran while the daemon was
live, so two processes each computed ``seq = head.seq + 1`` against their own
in-memory head and interleaved — corrupting both the audit chain (42 duplicate
seqs) and the registry SQLite. This module closes that gap with an OS-level
advisory lock (``flock``) on a single lockfile:

* the daemon acquires it at boot and holds it for its lifetime;
* any other writer (the write CLIs) acquires it first and REFUSES if it's held.

One writer at a time, enforced by the kernel — not by discipline alone. flock is
released automatically when the holding process dies (even on SIGKILL), so there
is no stale-lock failure mode the way a bare PID file would have.
"""
from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

#: Default lockfile. Lives in data/ (runtime, gitignored) next to the registry.
DEFAULT_LOCK_PATH = Path("data/.fsf-writer.lock")


def writer_lock_disabled() -> bool:
    """True when the cross-process writer lock should be skipped.

    The test harness sets ``FSF_DISABLE_WRITER_LOCK=1`` (root tests/conftest.py)
    because the suite boots the app hundreds of times in one process — a single
    global lock would have every boot after the first contend with itself.
    Production leaves it unset, so the daemon and write-CLIs acquire normally.
    The lock module's own unit tests call :class:`WriterLock` directly (not via
    this gate), so they still exercise real locking.
    """
    return os.environ.get("FSF_DISABLE_WRITER_LOCK", "").strip().lower() in (
        "1", "true", "yes",
    )


class SingleWriterError(RuntimeError):
    """Raised when the writer lock is already held by another live process."""


class WriterLock:
    """An exclusive, non-blocking ``flock`` on a single lockfile.

    Held for the lifetime of the holder (the file handle stays open). Released
    on :meth:`release`, on context-manager exit, or — crucially — automatically
    by the kernel when the holding process dies. The lockfile records the
    holder's pid + role so the next contender gets a useful error.
    """

    def __init__(self, path: Path | str = DEFAULT_LOCK_PATH, *, role: str = "writer") -> None:
        self._path = Path(path)
        self._role = role
        self._fh = None

    def acquire(self) -> "WriterLock":
        """Acquire the lock, or raise :class:`SingleWriterError` naming the holder.

        Non-blocking: a second writer must fail fast, never wait — waiting would
        just queue a second writer behind the daemon, which is not what anyone
        wants. Fail loudly so the operator stops the other writer.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 'a+' so a prior holder's identity line survives until we actually win
        # the lock and overwrite it (avoids blanking the holder info on a failed
        # contend).
        fh = open(self._path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            holder = self._read_holder(fh)
            fh.close()
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise SingleWriterError(
                    f"the FSF writer lock ({self._path}) is held by another live "
                    f"process [{holder}]. Only one writer may touch the registry "
                    f"DB + audit chain at a time — stop it first, or use the "
                    f"daemon API. (This guard exists because a concurrent writer "
                    f"once corrupted both stores.)"
                ) from e
            raise
        # Won it — stamp identity for the next contender's error message.
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} role={self._role}\n")
        fh.flush()
        self._fh = fh
        return self

    @staticmethod
    def _read_holder(fh) -> str:
        try:
            fh.seek(0)
            return fh.readline().strip() or "unknown holder"
        except Exception:
            return "unknown holder"

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def __enter__(self) -> "WriterLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


def assert_single_writer(
    path: Path | str = DEFAULT_LOCK_PATH, *, role: str = "cli",
) -> WriterLock:
    """For write-CLIs: acquire the writer lock or raise :class:`SingleWriterError`.

    Returns the held lock — the caller keeps it for the duration of its writes
    and ``release()``s when done (or just lets process exit drop it). If the
    daemon is live it holds the lock, so this refuses with a clear message —
    exactly the guard that would have prevented the corruption.
    """
    return WriterLock(path, role=role).acquire()
