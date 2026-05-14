"""Ingest — parse canonical artifacts into registry-ready dataclasses.

Two kinds of artifacts to parse:

1. **soul.md** files — YAML frontmatter between two ``---`` fences, followed
   by generated prose. We only need the frontmatter. A tolerant parser is
   used (stdlib ``yaml`` is not installed in the sandbox; we hand-parse a
   restricted subset that matches what :mod:`soul.generator` writes).

2. **audit chain** — JSONL, one event per line. Exactly what
   :mod:`core.audit_chain` produces.

Parsing is **strict-enough**: fields we depend on must be present, but we
don't re-hash or re-verify the audit chain here (that's ``AuditChain.verify``
in the core module). The registry trusts the chain's own integrity guarantees
and only mirrors it.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Namespace UUID for deterministic instance_id synthesis when rebuilding a
# registry from legacy (pre-registry) soul artifacts that lack an explicit
# instance_id. Derived via uuid5(NAMESPACE_URL, "forest_soul_forge/legacy")
# so the value is reproducible from plain stdlib with no literal magic.
_LEGACY_INSTANCE_NS: uuid.UUID = uuid.uuid5(
    uuid.NAMESPACE_URL, "forest_soul_forge/legacy"
)


# ---------------------------------------------------------------------------
# Parsed-artifact dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParsedSoul:
    """Everything the registry needs from a soul.md file.

    ``instance_id`` is ``None`` if the frontmatter didn't have one (legacy
    Phase 2 souls). The caller (registry) decides how to mint one.
    """

    soul_path: Path
    dna: str
    dna_full: str
    role: str
    agent_name: str
    constitution_hash: str
    constitution_path: Path
    created_at: str
    parent_dna: str | None
    lineage: tuple[str, ...]
    lineage_depth: int
    instance_id: str | None = None
    parent_instance: str | None = None
    spawned_by: str | None = None
    owner_id: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    # Sibling index (1-based) for twin disambiguation — same DNA, different
    # births. ``None`` means legacy/pre-v2 soul; rebuild will compute one.
    sibling_index: int | None = None
    # ADR-0049 T4 (Burst 243): per-agent ed25519 public key for
    # per-event signature verification. Base64-encoded raw 32-byte
    # public-key bytes. ``None`` for pre-v19 souls; the verifier
    # (ADR-0049 D5) treats their chain entries as 'legacy unsigned.'
    # New agents born on v19+ get a public_key populated by the
    # birth pipeline.
    public_key: str | None = None


@dataclass(frozen=True)
class ParsedAuditEntry:
    seq: int
    timestamp: str
    prev_hash: str
    entry_hash: str
    agent_dna: str | None
    event_type: str
    event_data: dict[str, Any]

    @property
    def event_json(self) -> str:
        # Canonical-ish dump for the registry mirror. Not a hash input — the
        # audit chain owns that. Sort keys for stable representation.
        return json.dumps(self.event_data, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# soul.md parsing
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL
)


def parse_soul_file(path: Path, encryption_config=None) -> ParsedSoul:
    """Parse a single soul.md and return its ParsedSoul.

    Raises :class:`IngestError` if the file is missing frontmatter or
    required fields.

    ADR-0050 T5 (B271) — encryption-aware. When called with a path
    that's the encrypted variant (``.soul.md.enc``), the function
    strips the ``.enc`` suffix from the returned :attr:`ParsedSoul.soul_path`
    so the registry keeps recording the canonical (plaintext-named)
    path. Re-encrypt-after-read happens at write time only — readers
    don't change the on-disk shape.
    """
    # Detect on-disk variant. If caller passed the plaintext name but
    # only the encrypted variant exists, the helper switches in.
    actual_path = path
    if not path.exists() and path.name.endswith(".enc") is False:
        enc = path.with_name(path.name + ".enc")
        if enc.exists():
            actual_path = enc

    if actual_path.suffix == ".enc":
        # Read+decrypt; record the plaintext-named canonical path on
        # the registry row so callers can construct the .enc path back
        # via with_name(name + ".enc") without re-discovering it.
        from forest_soul_forge.core.at_rest_encryption import decrypt_text
        if encryption_config is None:
            raise IngestError(
                f"{actual_path}: soul artifact is encrypted but no "
                f"encryption_config was provided; rebuild requires the "
                f"daemon's master key (set FSF_AT_REST_ENCRYPTION=true)"
            )
        text = decrypt_text(
            actual_path.read_text(encoding="utf-8"),
            encryption_config,
        )
        # Canonical path strips the trailing ``.enc`` so the registry
        # records the same value it did pre-encryption — that's what
        # write_artifacts treats as input and what character_sheet
        # row.soul_path expects.
        canonical_path = actual_path.with_name(actual_path.name[:-len(".enc")])
        path = canonical_path
    else:
        text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise IngestError(f"{path}: missing YAML frontmatter")
    frontmatter = _parse_frontmatter_block(m.group(1))

    def require(key: str) -> str:
        v = frontmatter.get(key)
        if v is None or v == "":
            raise IngestError(f"{path}: missing required field '{key}'")
        return str(v)

    dna = require("dna")
    dna_full = require("dna_full")
    role = require("role")
    agent_name = require("agent_name")
    constitution_hash = require("constitution_hash")
    constitution_file = require("constitution_file")
    created_at = require("generated_at")

    parent_dna_raw = frontmatter.get("parent_dna")
    parent_dna = None if parent_dna_raw in (None, "null") else str(parent_dna_raw)

    lineage_raw = frontmatter.get("lineage", [])
    if not isinstance(lineage_raw, list):
        raise IngestError(f"{path}: 'lineage' must be a list")
    lineage = tuple(str(x) for x in lineage_raw)

    lineage_depth_raw = frontmatter.get("lineage_depth", 0)
    try:
        lineage_depth = int(lineage_depth_raw)
    except (TypeError, ValueError) as e:
        raise IngestError(f"{path}: lineage_depth not an int: {e!r}") from e

    instance_id_raw = frontmatter.get("instance_id")
    instance_id = (
        None if instance_id_raw in (None, "", "null") else str(instance_id_raw)
    )
    parent_instance_raw = frontmatter.get("parent_instance")
    parent_instance = (
        None if parent_instance_raw in (None, "", "null") else str(parent_instance_raw)
    )

    # sibling_index is v2+. Absent on legacy souls; rebuild will assign one.
    sibling_index_raw = frontmatter.get("sibling_index")
    if sibling_index_raw in (None, "", "null"):
        sibling_index: int | None = None
    else:
        try:
            sibling_index = int(sibling_index_raw)
        except (TypeError, ValueError) as e:
            raise IngestError(
                f"{path}: sibling_index not an int: {e!r}"
            ) from e
        if sibling_index < 1:
            raise IngestError(
                f"{path}: sibling_index must be >= 1, got {sibling_index}"
            )

    constitution_path = path.parent / constitution_file

    # ADR-0049 T4 (Burst 243): per-agent ed25519 public key. Optional;
    # legacy souls (pre-v19) lack the field and register with NULL
    # public_key. New agents born post-v19 carry the base64-encoded
    # raw public-key bytes in frontmatter.
    public_key = _optional_str(frontmatter.get("public_key"))

    return ParsedSoul(
        soul_path=path,
        dna=dna,
        dna_full=dna_full,
        role=role,
        agent_name=agent_name,
        constitution_hash=constitution_hash,
        constitution_path=constitution_path,
        created_at=created_at,
        parent_dna=parent_dna,
        lineage=lineage,
        lineage_depth=lineage_depth,
        instance_id=instance_id,
        parent_instance=parent_instance,
        spawned_by=_optional_str(frontmatter.get("spawned_by")),
        owner_id=_optional_str(frontmatter.get("owner_id")),
        model_name=_optional_str(frontmatter.get("model_name")),
        model_version=_optional_str(frontmatter.get("model_version")),
        sibling_index=sibling_index,
        public_key=public_key,
    )


def iter_soul_files(root: Path) -> Iterator[Path]:
    """Yield every ``*.soul.md`` (and ``.soul.md.enc``) under ``root``.

    ADR-0050 T5 (B271) — encryption-aware glob. Picks up both the
    plaintext and the encrypted variant. The yielded ``.enc`` paths
    are recognized by :func:`parse_soul_file` which decrypts on read
    and returns the canonical (plaintext-named) path on the
    :class:`ParsedSoul`.

    Mixed directories (some plaintext, some encrypted) are supported
    — operators who flipped encryption on at some agent boundary see
    both eras of artifacts surface here.

    Sorted so rebuild is deterministic — critical when legacy-minting
    instance_ids is involved.
    """
    plain = root.rglob("*.soul.md")
    encrypted = root.rglob("*.soul.md.enc")
    # rglob yields both shapes; sort together so the rebuild order is
    # alphabetical-by-name regardless of which variant each agent
    # happens to be in.
    yield from sorted(list(plain) + list(encrypted))


def synthesize_legacy_instance_id(
    dna_full: str, created_at: str, soul_path: str | Path = ""
) -> str:
    """Deterministic UUID for legacy souls missing an explicit instance_id.

    Same (dna_full, created_at, soul_path) triple → same UUID across rebuilds.
    ``soul_path`` is included because two souls can legitimately share the
    same trait profile and wall-clock birth time (e.g. a role default and a
    lineage root in the examples set). The file path disambiguates them
    without requiring fresh UUIDs on every rebuild.

    ``soul_path`` defaults to empty string for backwards compatibility with
    early callers. New callers should always pass the relative soul path.
    """
    key = f"{dna_full}:{created_at}:{soul_path}"
    return str(uuid.uuid5(_LEGACY_INSTANCE_NS, key))


# ---------------------------------------------------------------------------
# Audit chain parsing
# ---------------------------------------------------------------------------
def iter_audit_entries(chain_path: Path) -> Iterator[ParsedAuditEntry]:
    """Yield every audit entry from ``chain_path`` in file order.

    Silently skips blank lines. Raises :class:`IngestError` on malformed JSON.
    """
    if not chain_path.exists():
        return
    with chain_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise IngestError(
                    f"{chain_path}:{lineno}: invalid JSON: {e.msg}"
                ) from e
            try:
                yield ParsedAuditEntry(
                    seq=int(obj["seq"]),
                    timestamp=str(obj["timestamp"]),
                    prev_hash=str(obj["prev_hash"]),
                    entry_hash=str(obj["entry_hash"]),
                    agent_dna=_optional_str(obj.get("agent_dna")),
                    event_type=str(obj["event_type"]),
                    event_data=obj.get("event_data") or {},
                )
            except KeyError as e:
                raise IngestError(
                    f"{chain_path}:{lineno}: missing field {e.args[0]!r}"
                ) from e


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
class IngestError(Exception):
    """Raised when a canonical artifact can't be parsed into a registry row."""


def _optional_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and v in ("", "null"):
        return None
    return str(v)


def _parse_frontmatter_block(block: str) -> dict[str, Any]:
    """Tolerant YAML subset parser.

    Supports exactly what soul.generator writes:
      - ``key: value`` scalars (quoted or unquoted; quotes are stripped)
      - ``key:`` followed by an indented mapping (one level of nesting for
        ``trait_values:``)
      - ``key: []`` and ``key:`` followed by ``- item`` lines for lists
      - bare ``null`` string → None

    Refuses anything more complex. That's intentional — the registry shouldn't
    silently misread unfamiliar frontmatter.
    """
    out: dict[str, Any] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        if indent != 0:
            # Top-level loop only handles top-level keys. Nested mappings /
            # lists are consumed by the branches below.
            i += 1
            continue

        if ":" not in stripped:
            raise IngestError(f"frontmatter line without ':': {raw!r}")
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest == "":
            # Block follows: either a nested mapping or a list of items.
            block_lines: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "" or nxt.startswith("#"):
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent == 0:
                    break
                block_lines.append(nxt)
                j += 1
            out[key] = _parse_nested_block(block_lines)
            i = j
            continue

        if rest == "[]":
            out[key] = []
            i += 1
            continue

        out[key] = _parse_scalar(rest)
        i += 1
    return out


def _parse_nested_block(lines: list[str]) -> Any:
    """Nested block: either list (``- x``) or mapping (``k: v``)."""
    stripped_lines = [ln.strip() for ln in lines if ln.strip()]
    if not stripped_lines:
        return {}
    if all(ln.startswith("- ") for ln in stripped_lines):
        return [_parse_scalar(ln[2:].strip()) for ln in stripped_lines]
    # Mapping
    nested: dict[str, Any] = {}
    for ln in stripped_lines:
        if ":" not in ln:
            raise IngestError(f"nested frontmatter line without ':': {ln!r}")
        k, _, v = ln.partition(":")
        nested[k.strip()] = _parse_scalar(v.strip())
    return nested


def _parse_scalar(s: str) -> Any:
    if s in ("null", "~", ""):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    # Quoted string
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    # Int?
    try:
        return int(s)
    except ValueError:
        pass
    # Float?
    try:
        return float(s)
    except ValueError:
        pass
    return s
