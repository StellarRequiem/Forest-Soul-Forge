"""Fixture discovery + indexing per ADR-0023 T1.

Walks ``benchmarks/{genre}/`` for ``*.yaml`` files matching the
``{name}.v{N}.yaml`` shape, loads + validates each, and returns a
dict keyed by fixture_id.

Per ADR-0023 §Storage layout, fixtures live in:
    benchmarks/{genre}/{fixture_name}.v{N}.yaml
    benchmarks/{genre}/rubrics/{fixture_name}.v{N}.yaml   (T4+; ignored here)
    benchmarks/{genre}/fixtures/...                       (input data; ignored)

So this loader scans the top level of each genre directory only —
``rubrics/`` and ``fixtures/`` subdirectories are not fixture YAMLs.
"""
from __future__ import annotations

from pathlib import Path

from forest_soul_forge.benchmarks.fixture import Fixture, FixtureValidationError, load_fixture


def load_fixtures_from_dir(
    benchmarks_root: Path | str,
    *,
    strict: bool = True,
) -> dict[str, Fixture]:
    """Discover and load all fixtures under ``benchmarks_root``.

    Returns a dict keyed by ``fixture_id`` ("signal_detection.v1").
    The genre directory each fixture lives in must match its declared
    ``genre`` field — mismatch raises FixtureValidationError.

    Args:
        benchmarks_root: path to the top-level benchmarks/ directory.
        strict: when True (default), the first invalid fixture raises
            and stops discovery. When False, invalid fixtures are
            skipped + recorded in the caller-visible warning path.
            (Strict-only in T1; the lenient flag exists for T6+
            character-sheet integration where partial discovery may
            be desirable.)

    Raises:
        FileNotFoundError: benchmarks_root doesn't exist.
        FixtureValidationError: a fixture failed validation (strict mode).
    """
    root = Path(benchmarks_root)
    if not root.exists():
        raise FileNotFoundError(f"benchmarks root not found: {root}")

    out: dict[str, Fixture] = {}
    for genre_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for fixture_path in sorted(genre_dir.glob("*.yaml")):
            try:
                fixture = load_fixture(fixture_path)
            except FixtureValidationError:
                if strict:
                    raise
                continue

            if fixture.genre != genre_dir.name:
                msg = (
                    f"{fixture_path}: declared genre {fixture.genre!r} doesn't "
                    f"match directory name {genre_dir.name!r}"
                )
                if strict:
                    raise FixtureValidationError(msg)
                continue

            if fixture.fixture_id in out:
                msg = (
                    f"{fixture_path}: duplicate fixture_id {fixture.fixture_id!r}; "
                    f"first defined at the prior loaded path. Versions must be unique."
                )
                if strict:
                    raise FixtureValidationError(msg)
                continue

            out[fixture.fixture_id] = fixture

    return out
