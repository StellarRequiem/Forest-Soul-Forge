"""``fsf encrypt ...`` — ADR-0050 T8 (Burst 275) operator CLI.

Three subcommands:

  - ``fsf encrypt status`` — surface the configured backend, verify
    the master key resolves cleanly, and count how many entries on
    each persistence surface are encrypted vs plaintext. Read-only.

  - ``fsf encrypt decrypt-event <seq>`` — decrypt a single audit
    chain entry under the current master key and print its
    plaintext event_data. Operator-debug only; no state mutation.

  - ``fsf encrypt rotate-key`` — generate a fresh master key,
    re-encrypt every encrypted surface under it (audit chain
    envelopes, memory bodies, SQLCipher PRAGMA, soul + constitution
    files), then persist the new key to the backend. Atomic per-
    surface with a backup directory the operator can roll back to.
    Refuses to run without explicit ``--confirm-daemon-stopped``
    affirmation — concurrent rotation against a live daemon is
    explicitly unsafe.

All three are read-only against the live registry by default
(they open the SQLite file via sqlcipher3 with the current key).
``rotate-key`` is the only mutator; it writes to a backup dir +
the live surfaces atomically.

This module is the last code-touch for ADR-0050; after it lands
the arc closes 8/8.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Register ``fsf encrypt ...`` subcommands."""
    encrypt = parent_subparsers.add_parser(
        "encrypt",
        help="At-rest encryption status, debug, and key rotation (ADR-0050).",
    )
    encrypt_sub = encrypt.add_subparsers(
        dest="encrypt_cmd", metavar="<subcmd>",
    )
    encrypt_sub.required = True

    # ---- status ------------------------------------------------------------
    p_status = encrypt_sub.add_parser(
        "status",
        help=(
            "Show encryption posture: backend, key resolution, "
            "encrypted-vs-plaintext counts per surface."
        ),
    )
    p_status.add_argument(
        "--data-dir", default=None,
        help=(
            "Daemon data dir (where registry.sqlite + soul_generated "
            "+ audit_chain.jsonl live). Defaults to the daemon's "
            "config: data/ for registry+souls, examples/audit_chain.jsonl "
            "for the chain."
        ),
    )
    p_status.add_argument(
        "--audit-chain-path", default=None,
        help=(
            "Override audit chain location. Default: "
            "examples/audit_chain.jsonl per daemon/config.py."
        ),
    )
    p_status.add_argument(
        "--registry-path", default=None,
        help="Override registry path. Default: data/registry.sqlite.",
    )
    p_status.add_argument(
        "--souls-dir", default=None,
        help="Override souls directory. Default: data/soul_generated.",
    )
    p_status.set_defaults(_run=_run_status)

    # ---- decrypt-event -----------------------------------------------------
    p_dec = encrypt_sub.add_parser(
        "decrypt-event",
        help=(
            "Decrypt a single audit-chain entry by its seq number "
            "and print the plaintext event_data. Operator-debug only; "
            "no state mutation."
        ),
    )
    p_dec.add_argument(
        "seq", type=int,
        help="Sequence number of the audit-chain entry to decrypt.",
    )
    p_dec.add_argument(
        "--audit-chain-path", default=None,
        help=(
            "Override audit chain location. Default: "
            "examples/audit_chain.jsonl."
        ),
    )
    p_dec.set_defaults(_run=_run_decrypt_event)

    # ---- rotate-key --------------------------------------------------------
    p_rot = encrypt_sub.add_parser(
        "rotate-key",
        help=(
            "Generate a fresh master key, re-encrypt every encrypted "
            "surface under it, persist the new key. Refuses without "
            "explicit --confirm-daemon-stopped (concurrent rotation "
            "against a live daemon is unsafe)."
        ),
    )
    p_rot.add_argument(
        "--confirm-daemon-stopped", action="store_true",
        help=(
            "REQUIRED. Affirms the operator has stopped the daemon. "
            "Without this flag the command refuses — concurrent writes "
            "to a registry during rekey would corrupt the encrypted "
            "store."
        ),
    )
    p_rot.add_argument(
        "--data-dir", default=None,
        help="Daemon data dir (default: data/).",
    )
    p_rot.add_argument(
        "--audit-chain-path", default=None,
        help="Audit chain to rotate (default: examples/audit_chain.jsonl).",
    )
    p_rot.add_argument(
        "--registry-path", default=None,
        help="Registry to rotate (default: data/registry.sqlite).",
    )
    p_rot.add_argument(
        "--souls-dir", default=None,
        help="Souls dir to rotate (default: data/soul_generated).",
    )
    p_rot.add_argument(
        "--backup-suffix", default=".pre-rotate",
        help=(
            "Suffix appended to existing files when staging the "
            "rotation backup (default: '.pre-rotate'). The CLI never "
            "deletes the backup — operator decides when to clean up "
            "after verifying the rotation."
        ),
    )
    p_rot.set_defaults(_run=_run_rotate_key)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _run_status(args: argparse.Namespace) -> int:
    """Read-only inspection of the current encryption posture."""
    from forest_soul_forge.security.master_key import (
        FSF_MASTER_KEY_BACKEND_ENV,
        configured_backend_name,
    )

    at_rest_on = (
        os.environ.get("FSF_AT_REST_ENCRYPTION", "false")
        .strip().lower() == "true"
    )

    print("=== Forest at-rest encryption status (ADR-0050) ===")
    print(f"FSF_AT_REST_ENCRYPTION  = {os.environ.get('FSF_AT_REST_ENCRYPTION', '(unset)')}")
    print(f"FSF_MASTER_KEY_BACKEND  = {os.environ.get(FSF_MASTER_KEY_BACKEND_ENV, '(default)')}")
    print(f"Configured backend      = {configured_backend_name()}")
    print()

    # Key resolution probe — we don't reveal the key, just whether
    # resolve_master_key would succeed under the current env. Skip
    # entirely when encryption isn't enabled (avoids surfacing a
    # passphrase prompt or generating a Keychain entry just to run
    # `fsf encrypt status`).
    if at_rest_on:
        print("[key resolution]")
        try:
            from forest_soul_forge.security.master_key import (
                resolve_master_key,
            )
            key = resolve_master_key()
            print(f"  resolved          = ok ({len(key)} bytes)")
        except Exception as e:  # noqa: BLE001
            print(f"  resolved          = FAILED ({type(e).__name__}: {e})")
        print()
    else:
        print("[key resolution]  skipped (FSF_AT_REST_ENCRYPTION not true)")
        print()

    # Per-surface inventory. Walks files on disk — no daemon HTTP.
    chain_path = Path(args.audit_chain_path or "examples/audit_chain.jsonl")
    souls_dir = Path(args.souls_dir or "data/soul_generated")
    reg_path = Path(args.registry_path or "data/registry.sqlite")

    print("[audit chain]  " + str(chain_path))
    print(_summarize_audit_chain(chain_path))
    print()

    print("[soul + constitution artifacts]  " + str(souls_dir))
    print(_summarize_soul_dir(souls_dir))
    print()

    print("[registry]  " + str(reg_path))
    print(_summarize_registry(reg_path))
    return 0


def _summarize_audit_chain(path: Path) -> str:
    if not path.exists():
        return f"  not found at {path}"
    enc = plain = bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            entry = json.loads(s)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(entry, dict) and "encryption" in entry:
            enc += 1
        else:
            plain += 1
    total = enc + plain + bad
    return (
        f"  total lines      = {total}\n"
        f"  encrypted        = {enc}\n"
        f"  plaintext        = {plain}\n"
        f"  malformed        = {bad}"
    )


def _summarize_soul_dir(souls_dir: Path) -> str:
    if not souls_dir.exists():
        return f"  not found at {souls_dir}"
    plain_souls = list(souls_dir.rglob("*.soul.md"))
    enc_souls = list(souls_dir.rglob("*.soul.md.enc"))
    plain_const = list(souls_dir.rglob("*.constitution.yaml"))
    enc_const = list(souls_dir.rglob("*.constitution.yaml.enc"))
    return (
        f"  souls plaintext  = {len(plain_souls)}\n"
        f"  souls encrypted  = {len(enc_souls)}\n"
        f"  const plaintext  = {len(plain_const)}\n"
        f"  const encrypted  = {len(enc_const)}"
    )


def _summarize_registry(reg_path: Path) -> str:
    """Lightweight registry probe without unlocking SQLCipher.

    We can't open the file under encryption without the key (and we
    don't want to surface key material from a status command). The
    stdlib sqlite3 probe distinguishes 'encrypted (not a valid SQLite
    file)' from 'plaintext (header looks like SQLite)'.
    """
    if not reg_path.exists():
        return f"  not found at {reg_path}"
    # SQLite file header magic: first 16 bytes "SQLite format 3\0".
    try:
        with reg_path.open("rb") as fh:
            magic = fh.read(16)
    except OSError as e:
        return f"  read error: {e}"
    if magic == b"SQLite format 3\x00":
        return "  shape            = plaintext SQLite (NOT encrypted)"
    return "  shape            = opaque (likely SQLCipher-encrypted)"


# ---------------------------------------------------------------------------
# decrypt-event
# ---------------------------------------------------------------------------


def _run_decrypt_event(args: argparse.Namespace) -> int:
    """Decrypt a single audit-chain entry by seq and print event_data."""
    from forest_soul_forge.core.at_rest_encryption import (
        EncryptionConfig,
        decrypt_event_data,
        is_encrypted_entry,
    )
    from forest_soul_forge.security.master_key import resolve_master_key

    chain_path = Path(args.audit_chain_path or "examples/audit_chain.jsonl")
    if not chain_path.exists():
        print(f"audit chain not found at {chain_path}", file=sys.stderr)
        return 2

    seq_target = int(args.seq)
    target_entry: dict[str, Any] | None = None
    for line in chain_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            entry = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("seq") == seq_target:
            target_entry = entry
            break

    if target_entry is None:
        print(f"no audit entry found at seq={seq_target}", file=sys.stderr)
        return 1

    if not is_encrypted_entry(target_entry):
        # Already plaintext — just print the event_data field.
        ed = target_entry.get("event_data", {})
        print(json.dumps(ed, indent=2, sort_keys=True))
        return 0

    try:
        master_key = resolve_master_key()
    except Exception as e:  # noqa: BLE001
        print(
            f"master key not available for decrypt: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 3

    config = EncryptionConfig(master_key=master_key)
    try:
        plaintext = decrypt_event_data(target_entry, config)
    except Exception as e:  # noqa: BLE001
        print(
            f"decrypt failed for seq={seq_target}: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 4

    print(json.dumps(plaintext, indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# rotate-key
# ---------------------------------------------------------------------------


def _run_rotate_key(args: argparse.Namespace) -> int:
    """Generate a fresh master key + re-encrypt every surface under it.

    Safety posture:
      - Refuses without --confirm-daemon-stopped (concurrent rotation
        against a live daemon corrupts the encrypted store).
      - Stages a backup of every mutated file under <path><suffix>
        before writing the rotated version.
      - Refuses to commit if any per-surface re-encryption fails.
      - Operator decides when to clean up the .pre-rotate/ backups —
        the CLI never deletes them.
    """
    if not args.confirm_daemon_stopped:
        print(
            "REFUSED. fsf encrypt rotate-key requires "
            "--confirm-daemon-stopped to affirm no daemon is writing "
            "to the registry. Concurrent rotation corrupts the "
            "encrypted store. Stop the daemon, then retry with the "
            "flag.",
            file=sys.stderr,
        )
        return 2

    # Resolve current key first — refuse if it's not available.
    from forest_soul_forge.security.master_key import (
        MASTER_KEY_NAME,
        configured_backend_name,
        reset_cache,
        resolve_master_key,
    )

    try:
        old_key = resolve_master_key()
    except Exception as e:  # noqa: BLE001
        print(
            f"current master key not resolvable: {type(e).__name__}: {e}. "
            "Cannot rotate without first unlocking the existing store.",
            file=sys.stderr,
        )
        return 3

    # Generate fresh key.
    import secrets as _secrets
    new_key = _secrets.token_bytes(32)
    print(f"[rotate] backend={configured_backend_name()} "
          f"old_kid=default new_kid=default (overwrite)")

    chain_path = Path(args.audit_chain_path or "examples/audit_chain.jsonl")
    souls_dir = Path(args.souls_dir or "data/soul_generated")
    reg_path = Path(args.registry_path or "data/registry.sqlite")
    backup_suffix = args.backup_suffix

    # --- audit chain re-encrypt ---------------------------------------------
    if chain_path.exists():
        print(f"[rotate] audit chain -> {chain_path}")
        try:
            _rotate_audit_chain(chain_path, old_key, new_key, backup_suffix)
        except Exception as e:  # noqa: BLE001
            print(
                f"FAILED rotating audit chain: {type(e).__name__}: {e}. "
                f"Restore from {chain_path}{backup_suffix} if needed; "
                "the new master key has NOT been persisted yet so the "
                "old key is still authoritative.",
                file=sys.stderr,
            )
            return 4

    # --- soul + constitution .enc files -------------------------------------
    if souls_dir.exists():
        print(f"[rotate] soul + constitution files -> {souls_dir}")
        try:
            _rotate_soul_files(souls_dir, old_key, new_key, backup_suffix)
        except Exception as e:  # noqa: BLE001
            print(
                f"FAILED rotating soul/const files: {type(e).__name__}: {e}. "
                "The new master key has NOT been persisted; old key "
                "still authoritative. Restore from .pre-rotate backups.",
                file=sys.stderr,
            )
            return 5

    # --- registry: memory_entries + SQLCipher PRAGMA rekey ------------------
    if reg_path.exists():
        print(f"[rotate] registry (memory + SQLCipher) -> {reg_path}")
        try:
            _rotate_registry(reg_path, old_key, new_key, backup_suffix)
        except Exception as e:  # noqa: BLE001
            print(
                f"FAILED rotating registry: {type(e).__name__}: {e}. "
                "The chain + soul files were rotated successfully but "
                "the registry is still under the old key. Restore "
                f"{chain_path}{backup_suffix} and the soul-dir backups "
                "before retrying.",
                file=sys.stderr,
            )
            return 6

    # --- persist the new key to the backend ---------------------------------
    print("[rotate] persisting new master key to backend ...")
    try:
        _persist_new_master_key(new_key)
        reset_cache()
    except Exception as e:  # noqa: BLE001
        print(
            f"FAILED persisting new key: {type(e).__name__}: {e}. "
            "DANGER: data on disk is now encrypted under the NEW key "
            "but the backend still has the OLD key. Recover by "
            "manually writing the new key (32 bytes, base64) to the "
            f"{MASTER_KEY_NAME} slot in your backend, then restart "
            "the daemon. If unrecoverable, restore from .pre-rotate "
            "backups.",
            file=sys.stderr,
        )
        return 7

    print("")
    print("[rotate] SUCCESS. New master key persisted; old key retired.")
    print(f"[rotate] Pre-rotate backups left at *{backup_suffix} alongside "
          "each rotated file. Verify the daemon boots cleanly under the "
          "new key, then delete the backups when you're confident.")
    return 0


def _rotate_audit_chain(
    path: Path, old_key: bytes, new_key: bytes, backup_suffix: str,
) -> None:
    """Re-encrypt every encrypted entry; preserve plaintext entries.

    Atomic: writes <path>.rotating, then renames over <path> after
    backup is in place. Backup at <path><backup_suffix> survives.
    """
    from forest_soul_forge.core.at_rest_encryption import (
        EncryptionConfig,
        decrypt_event_data,
        encrypt_event_data,
        is_encrypted_entry,
    )

    old_cfg = EncryptionConfig(master_key=old_key)
    new_cfg = EncryptionConfig(master_key=new_key)

    backup = path.with_name(path.name + backup_suffix)
    shutil.copy2(path, backup)

    out_path = path.with_name(path.name + ".rotating")
    rotated_lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            rotated_lines.append(raw)
            continue
        entry = json.loads(s)
        if is_encrypted_entry(entry):
            plaintext = decrypt_event_data(entry, old_cfg)
            new_entry = dict(entry)
            new_entry.pop("encryption", None)
            new_entry["event_data"] = plaintext
            new_entry = encrypt_event_data(new_entry, new_cfg)
            rotated_lines.append(json.dumps(new_entry, sort_keys=True))
        else:
            # Plaintext entries (pre-T3 era) stay plaintext per ADR
            # Decision 6 — re-encrypting them would change entry_hash
            # and break the chain.
            rotated_lines.append(raw)

    out_path.write_text("\n".join(rotated_lines) + "\n", encoding="utf-8")
    out_path.replace(path)


def _rotate_soul_files(
    souls_dir: Path, old_key: bytes, new_key: bytes, backup_suffix: str,
) -> None:
    """Re-encrypt every .soul.md.enc + .constitution.yaml.enc file.

    Plaintext .soul.md / .constitution.yaml files are left unchanged
    — they were never encrypted; T8 rotation doesn't change posture.
    """
    from forest_soul_forge.core.at_rest_encryption import (
        EncryptionConfig, decrypt_text, encrypt_text,
    )

    old_cfg = EncryptionConfig(master_key=old_key)
    new_cfg = EncryptionConfig(master_key=new_key)

    for enc_file in list(souls_dir.rglob("*.enc")):
        backup = enc_file.with_name(enc_file.name + backup_suffix)
        shutil.copy2(enc_file, backup)
        plaintext = decrypt_text(
            enc_file.read_text(encoding="utf-8"), old_cfg,
        )
        new_cipher = encrypt_text(plaintext, new_cfg)
        # Atomic rename via tmp file.
        tmp = enc_file.with_name(enc_file.name + ".rotating")
        tmp.write_text(new_cipher, encoding="utf-8")
        tmp.replace(enc_file)


def _rotate_registry(
    reg_path: Path, old_key: bytes, new_key: bytes, backup_suffix: str,
) -> None:
    """Re-encrypt memory_entries.content + run SQLCipher PRAGMA rekey.

    Two layers: application-layer (memory_entries rows where
    content_encrypted=1) and SQLCipher whole-file. Both must rotate
    together — the row decrypts under the OLD app-layer key while
    the row is inside the OLD SQLCipher file, then re-encrypts under
    the NEW app-layer key, then the SQLCipher file gets rekeyed.

    Order matters: app-layer rotation FIRST while we still have the
    old SQLCipher key, then PRAGMA rekey at the end.
    """
    from forest_soul_forge.core.at_rest_encryption import (
        EncryptionConfig, decrypt_text, encrypt_text,
    )

    # Backup the registry file before mutating.
    backup = reg_path.with_name(reg_path.name + backup_suffix)
    shutil.copy2(reg_path, backup)

    try:
        import sqlcipher3.dbapi2 as sqlcipher3  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "sqlcipher3 not installed; cannot rotate the SQLCipher "
            "registry. Install via 'pip install sqlcipher3-binary'."
        ) from e

    old_hex = base64_to_hex(old_key)
    new_hex = base64_to_hex(new_key)

    conn = sqlcipher3.connect(str(reg_path))
    cur = conn.cursor()
    cur.execute(f"PRAGMA key = \"x'{old_hex}'\"")

    # App-layer memory rotation. content_encrypted=1 rows hold a
    # base64(envelope) string in content; decrypt under old config,
    # re-encrypt under new config, write back.
    old_cfg = EncryptionConfig(master_key=old_key)
    new_cfg = EncryptionConfig(master_key=new_key)
    cur.execute(
        "SELECT entry_id, content FROM memory_entries "
        "WHERE content_encrypted = 1"
    )
    rows = cur.fetchall()
    for entry_id, ciphertext in rows:
        plaintext = decrypt_text(ciphertext, old_cfg)
        new_cipher = encrypt_text(plaintext, new_cfg)
        cur.execute(
            "UPDATE memory_entries SET content = ? WHERE entry_id = ?",
            (new_cipher, entry_id),
        )
    conn.commit()

    # SQLCipher whole-file rekey.
    cur.execute(f"PRAGMA rekey = \"x'{new_hex}'\"")
    conn.commit()
    conn.close()


def _persist_new_master_key(new_key: bytes) -> None:
    """Write the rotated master key to whichever backend is active.

    Uses the same SecretStore backend resolve_master_key reads from.
    Keychain / file backends both implement the same put() surface.
    Passphrase backend rotation requires re-derivation — operator
    sets a new FSF_MASTER_PASSPHRASE before re-running this command
    on a fresh salt; the CLI's rotation flow assumes
    Keychain or file backend.
    """
    from forest_soul_forge.security.master_key import (
        MASTER_KEY_NAME, configured_backend_name,
    )
    from forest_soul_forge.security.keys import resolve_agent_key_store

    if configured_backend_name() == "passphrase":
        raise RuntimeError(
            "rotate-key under passphrase backend is unsupported in T8 — "
            "the new key is derived from the passphrase + salt, so "
            "rotation means changing one of those. Stop the daemon, "
            "set FSF_MASTER_PASSPHRASE to the new value (or wipe "
            "~/.forest/master_salt to force a fresh salt), and run "
            "the migration manually following docs/runbooks/"
            "encryption-at-rest.md."
        )

    store = resolve_agent_key_store()
    backend = store._backend  # noqa: SLF001 — internal access by design
    backend.put(
        MASTER_KEY_NAME,
        base64.b64encode(new_key).decode("ascii"),
    )


def base64_to_hex(key: bytes) -> str:
    """Return the hex-encoding of the 32-byte master key.

    SQLCipher's PRAGMA key accepts a hex form: ``"x'<hex>'"``. The
    32-byte key becomes a 64-char hex string. Mirrors the existing
    registry bootstrap helper.
    """
    return key.hex()
