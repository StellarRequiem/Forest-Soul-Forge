"""``fsf index ...`` — ADR-0076 T5 (B323) personal-index admin CLI.

The PersonalIndex (B292) lives in-memory in the daemon process; it
rebuilds itself from scratch each daemon boot by walking
memory_entries WHERE scope='personal'. Most of the time this is
fine. But operators hit cases where they need to rebuild it
explicitly:

  - Switched embedders → dimension changed → existing vectors
    invalid.
  - Memory consolidation merged old entries → indexer didn't see
    the new ones at write-time.
  - Backup restore from a chain that pre-dates ADR-0076 → no
    indexer events ever fired.

This CLI gives them a way: walk the registry SQL truth, push every
qualifying entry through PersonalIndex.add. Daemon-offline use is
the primary case (daemon should be stopped during a rebuild so
concurrent writes don't race the wipe-and-rebuild). Two
subcommands:

  fsf index rebuild  [--dry-run]
  fsf index status

``--dry-run`` reports the entry count + sample but doesn't actually
import sentence-transformers or pay the embedder cold-load cost.
Useful for "how big is the rebuild going to be?"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    p = parent_subparsers.add_parser(
        "index",
        help=(
            "Manage the PersonalIndex vector store (ADR-0076). "
            "rebuild and status subcommands. Operator-driven; "
            "daemon should be stopped before rebuild."
        ),
    )
    sub = p.add_subparsers(dest="index_cmd", metavar="<subcmd>")
    sub.required = True

    rb = sub.add_parser(
        "rebuild",
        help=(
            "Re-embed every scope='personal' memory entry into a "
            "fresh PersonalIndex. Stop the daemon first to avoid "
            "racing concurrent writes."
        ),
    )
    rb.add_argument(
        "--registry-path", default=None,
        help="Override registry DB path (default: data/registry.sqlite).",
    )
    rb.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Count + sample entries without loading the embedder "
            "or building the index."
        ),
    )
    rb.add_argument(
        "--batch-size", type=int, default=32,
        help=(
            "Embed batch size for the rebuild loop. Higher = faster "
            "but more memory. Default 32 (good fit for 8GB RAM)."
        ),
    )
    rb.set_defaults(_run=_run_rebuild)

    st = sub.add_parser(
        "status",
        help=(
            "Report PersonalIndex source-of-truth counts: how many "
            "scope='personal' entries exist + how many would be "
            "indexed in a rebuild."
        ),
    )
    st.add_argument(
        "--registry-path", default=None,
        help="Override registry DB path.",
    )
    st.set_defaults(_run=_run_status)


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------


def _run_rebuild(args: argparse.Namespace) -> int:
    registry_path = _resolve_registry_path(args.registry_path)
    if not registry_path.exists():
        print(
            f"registry DB not found at {registry_path}",
            file=sys.stderr,
        )
        return 2

    entries = _load_personal_entries(registry_path)
    n = len(entries)
    print(f"# scope='personal' entries: {n}")

    if n == 0:
        print("nothing to rebuild")
        return 0

    if args.dry_run:
        # Show the first 3 entry_ids as a sanity sample, no
        # embedder load.
        sample = entries[:3]
        print("dry-run: would index the following (sample):")
        for entry_id, text, _layer, _tags in sample:
            preview = (text[:60] + "…") if len(text) > 60 else text
            print(f"  - {entry_id}: {preview!r}")
        if n > 3:
            print(f"  ... and {n - 3} more")
        return 0

    # Real rebuild. The embedder is lazy-imported inside
    # PersonalIndex so the cold-load happens here, not at module
    # import.
    print(f"loading embedder + initializing index (batch_size={args.batch_size})...")
    t0 = time.monotonic()
    from forest_soul_forge.core.personal_index import PersonalIndex
    index = PersonalIndex()
    index.clear()

    # Build add_batch items lists in chunks.
    indexed = 0
    failed = 0
    for chunk in _chunks(entries, args.batch_size):
        items = [
            {
                "doc_id": entry_id,
                "text":   text,
                "source": f"memory:{layer}:personal",
                "tags":   list(tags),
            }
            for (entry_id, text, layer, tags) in chunk
        ]
        try:
            index.add_batch(items)
            indexed += len(items)
        except Exception as e:  # noqa: BLE001
            failed += len(items)
            print(
                f"warn: batch failed ({len(items)} items): "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )

    dt = time.monotonic() - t0
    print(
        f"rebuild complete in {dt:.1f}s: "
        f"indexed={indexed} failed={failed} count={index.count()}"
    )
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _run_status(args: argparse.Namespace) -> int:
    registry_path = _resolve_registry_path(args.registry_path)
    if not registry_path.exists():
        print(
            f"registry DB not found at {registry_path}",
            file=sys.stderr,
        )
        return 2

    entries = _load_personal_entries(registry_path)
    print(f"registry: {registry_path}")
    print(f"scope='personal' entries eligible for indexing: {len(entries)}")
    if entries:
        layers = {}
        for _eid, _text, layer, _tags in entries:
            layers[layer] = layers.get(layer, 0) + 1
        print("by layer:")
        for layer, count in sorted(layers.items()):
            print(f"  {layer}: {count}")
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resolve_registry_path(override: str | None) -> Path:
    if override:
        return Path(override)
    return Path("data/registry.sqlite")


def _load_personal_entries(
    path: Path,
) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Walk memory_entries WHERE scope='personal' AND deleted_at
    IS NULL. Returns (entry_id, content, layer, tags_tuple) tuples.

    Plaintext content only — encrypted-row decryption requires the
    master key + EncryptionConfig wiring that the CLI doesn't have
    today. Operators on at-rest encryption use the daemon's
    rebuild endpoint (deferred to a future tranche); this CLI is
    the offline plaintext path.
    """
    import json
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT entry_id, content, layer, tags_json, content_encrypted
            FROM memory_entries
            WHERE scope='personal' AND deleted_at IS NULL
            ORDER BY rowid ASC;
            """,
        ).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, str, str, tuple[str, ...]]] = []
    for r in rows:
        if r["content_encrypted"]:
            # Skip encrypted rows in CLI rebuild — they need the
            # master key to decrypt, which is daemon-resident.
            continue
        try:
            tags = tuple(json.loads(r["tags_json"]) or [])
        except (TypeError, ValueError):
            tags = ()
        out.append((r["entry_id"], r["content"], r["layer"], tags))
    return out


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
