"""Schedule parser for ADR-0041 set-and-forget orchestrator.

Initial implementation: interval-based schedules only.

Supported syntax:
    "every Ns"   N seconds
    "every Nm"   N minutes
    "every Nh"   N hours
    "every Nd"   N days

Cron syntax ("0 */6 * * *") is queued for a follow-up tranche.
The unit suffix is required; bare integers are rejected so the
operator never silently gets seconds-vs-hours confused.

Example::

    >>> s = parse_schedule("every 6h")
    >>> s.interval_seconds
    21600
    >>> from datetime import datetime, timezone
    >>> last = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    >>> s.next_after(last).isoformat()
    '2026-05-03T18:00:00+00:00'
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


_INTERVAL_RE = re.compile(r"^\s*every\s+(\d+)\s*([smhd])\s*$", re.IGNORECASE)

_UNIT_TO_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
}


class ScheduleParseError(ValueError):
    """Raised when a schedule string can't be parsed."""


@dataclass(frozen=True)
class Schedule:
    """A parsed schedule. Currently interval-based only."""

    interval_seconds: int
    raw: str

    def next_after(self, last_run: datetime | None, now: datetime) -> datetime:
        """Compute the next-run timestamp.

        If ``last_run`` is None (task has never run), schedule
        the first run for ``now`` so it fires on the first tick
        after registration. Otherwise add the interval to
        ``last_run``.

        Caller passes ``now`` so tests are deterministic and the
        scheduler doesn't drift due to clock-read jitter inside
        this function.
        """
        if last_run is None:
            return now
        return last_run + timedelta(seconds=self.interval_seconds)

    def __str__(self) -> str:
        return f"Schedule({self.raw}, every {self.interval_seconds}s)"


def parse_schedule(spec: str) -> Schedule:
    """Parse an interval schedule string. See module docstring.

    Raises :class:`ScheduleParseError` on malformed input.
    """
    if not isinstance(spec, str):
        raise ScheduleParseError(
            f"schedule must be a string; got {type(spec).__name__}"
        )
    m = _INTERVAL_RE.match(spec)
    if m is None:
        raise ScheduleParseError(
            f"schedule {spec!r} does not match 'every N[smhd]'. "
            "Cron syntax is not yet supported."
        )
    n = int(m.group(1))
    unit = m.group(2).lower()
    if n <= 0:
        raise ScheduleParseError(
            f"schedule interval must be positive; got {n}"
        )
    seconds = n * _UNIT_TO_SECONDS[unit]
    return Schedule(interval_seconds=seconds, raw=spec.strip())
