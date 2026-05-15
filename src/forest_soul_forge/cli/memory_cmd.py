"""``fsf memory ...`` — ADR-0074 T5 (B308) operator CLI.

Two subcommands give the operator direct control over the
consolidation_state column on memory_entries without needing the
running daemon:

  - ``fsf memory pin <entry_id>`` — flip an entry to
    ``consolidation_state='pinned'`` so it never auto-consolidates.

  - ``fsf memory unpin <entry_id>`` — flip a pinned entry back to
    'pending' (eligible for the next consolidation pass).

Both subcommands operate directly on ``data/registry.sqlite``
(override via ``--registry-path``). They DON'T go through the
HTTP layer — the CLI is the offline operator surface for
post-crash or pre-daemon-boot recovery. The HTTP equivalents
(``POST /memory/consolidation/pin/{id}``) are the live-daemon
path.

Refuses (rc=2) when:
  - the entry doesn't exist
  - the entry is in a state incompatible with the requested
    transition (e.g. trying to pin an already-consolidated row)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional


DEFAULT_REGISTRY_PATH = Path("data/registry.sqlite")


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Register ``fsf memory ...`` subcommands."""
    memory = parent_subparsers.add_parser(
        "memory",
        help=(
            "Memory operator controls (ADR-0074). Pin/unpin entries "
            "against the consolidation runner."
        ),
    )
    memory_sub = memory.add_subparsers(
        dest="memory_cmd", metavar="<subcmd>",
    )
    memory_sub.required = True

    # ---- pin ---------------------------------------------------------------
    p_pin = memory_sub.add_parser(
        "pin",
        help=(
            "Pin a memory entry so it never auto-consolidates "
            "(ADR-0074 D1)."
        ),
    )
    p_pin.add_argument(
        "entry_id",
        help="entry_id from memory_entries.entry_id.",
    )
    p_pin.add_argument(
        "--registry-path",
        type=Path, default=None,
        help="Override registry path. Default: data/registry.sqlite.",
    )
    p_pin.set_defaults(_run=_run_pin)

    # ---- unpin -------------------------------------------------------------
    p_unpin = memory_sub.add_parser(
        "unpin",
        help=(
            "Unpin a memory entry so it's eligible for the next "
            "consolidation pass."
        ),
    )
    p_unpin.add_argument("entry_id")
    p_unpin.add_argument(
        "--registry-path", type=Path, default=None,
        help="Override registry path. Default: data/registry.sqlite.",
    )
    p_unpin.set_defaults(_run=_run_unpin)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def _run_pin(args: argparse.Namespace) -> int:
    """Flip pending -> pinned."""
    return _flip_state(
        registry_path=args.registry_path or DEFAULT_REGISTRY_PATH,
        entry_id=args.entry_id,
        from_states=("pending",),
        to_state="pinned",
    )


def _run_unpin(args: argparse.Namespace) -> int:
    """Flip pinned -> pending."""
    return _flip_state(
        registry_path=args.registry_path or DEFAULT_REGISTRY_PATH,
        entry_id=args.entry_id,
        from_states=("pinned",),
        to_state="pending",
    )


def _flip_state(
    *,
    registry_path: Path,
    entry_id: str,
    from_states: tuple[str, ...],
    to_state: str,
) -> int:
    """Open the registry, run the conditional UPDATE, print result.

    Returns 0 on success, 2 on missing/conflict. The conditional
    is a single transaction so a concurrent daemon write can't
    corrupt state — though concurrent operator action on the
    same row is rare enough we don't need explicit locking.
    """
    if not registry_path.exists():
        print(
            f"error: registry not found at {registry_path}",
            file=sys.stderr,
        )
        return 2

    try:
        conn = sqlite3.connect(str(registry_path))
    except sqlite3.Error as e:
        print(f"error: cannot open registry: {e}", file=sys.stderr)
        return 2

    try:
        cur = conn.execute(
            "SELECT consolidation_state FROM memory_entries "
            "WHERE entry_id = ?",
            (entry_id,),
        )
        row = cur.fetchone()
        if row is None:
            print(
                f"error: no memory entry with id {entry_id!r}",
                file=sys.stderr,
            )
            return 2
        current = row[0]
        if current not in from_states:
            print(
                f"error: entry {entry_id!r} is in state {current!r}, "
                f"not in {list(from_states)} — refuse to flip to "
                f"{to_state!r}",
                file=sys.stderr,
            )
            return 2
        with conn:
            conn.execute(
                "UPDATE memory_entries SET consolidation_state = ? "
                "WHERE entry_id = ?",
                (to_state, entry_id),
            )
        print(
            f"ok: {entry_id} {current} -> {to_state}",
        )
        return 0
    finally:
        conn.close()
